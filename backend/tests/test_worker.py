from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models import Notification, PriceAlert, now
from app.worker import AlertWorker, worker_lock
from .conftest import search


def setup_alert(client, auth, target="500.00"):
    data = search(client, auth).json()
    product = next(item["productId"] for item in data["results"]
                   if item["offers"][0]["quantity"] == "500 g")
    response = client.post("/alerts", headers=auth, json={"product_id": product, "target_price": target})
    assert response.status_code == 201, response.text
    worker = AlertWorker(client.app.state.sessions, client.app.state.settings, client.app.state.search)
    return response.json()["id"], worker


def due(client, identity):
    with client.app.state.sessions.begin() as db:
        db.get(PriceAlert, identity).next_check_at = now() - timedelta(seconds=1)


def rows(client):
    with client.app.state.sessions() as db:
        return list(db.scalars(select(Notification).order_by(Notification.created_at)))


def test_worker_qualifies_once_and_does_not_use_other_pack(client, auth):
    identity, worker = setup_alert(client, auth)
    worker.check(identity)
    worker.check(identity)
    notices = rows(client)
    assert len(notices) == 1 and notices[0].status == "pending"
    assert Decimal(notices[0].payload["price"]) <= Decimal("500")
    # Other fixture offers include a 100g pack below 100 INR, but the watched
    # 500g product is above 100 INR, so this target must never trigger.
    client.patch(f"/alerts/{identity}", headers=auth, json={"target_price": "100.00"})
    worker.check(identity)
    assert len(rows(client)) == 1
    assert rows(client)[0].status == "cancelled"


def test_worker_rearms_after_price_rises_and_falls(client, auth):
    identity, worker = setup_alert(client, auth)
    worker.check(identity)
    store = client.app.state.search.store
    for vendor in store.all():
        store.update(vendor, handling_fee="1000")
    worker.check(identity)
    with client.app.state.sessions() as db:
        assert not db.get(PriceAlert, identity).below_target
    for vendor in store.all():
        store.update(vendor, handling_fee="0")
    worker.check(identity)
    assert len(rows(client)) == 2
    assert len({r.dedupe_key for r in rows(client)}) == 2


def test_partial_failure_does_not_rearm_or_send_false_alert(client, auth):
    identity, worker = setup_alert(client, auth)
    worker.check(identity)
    for adapter in client.app.state.search.engine.adapters.values():
        adapter.parse = lambda _: []
    worker.check(identity)
    with client.app.state.sessions() as db:
        alert = db.get(PriceAlert, identity)
        assert alert.below_target and alert.last_error and alert.last_checked_at
    assert len(rows(client)) == 1


def test_delivery_retries_without_rescraping_and_marks_sent(client, auth, monkeypatch):
    identity, worker = setup_alert(client, auth)
    worker.check(identity)
    notification = rows(client)[0]
    calls = []

    def failed(*args):
        calls.append(args)
        raise ConnectionError("SMTP unavailable")

    monkeypatch.setattr(worker, "send_email", failed)
    worker.deliver(notification.id)
    assert rows(client)[0].status == "pending" and rows(client)[0].attempts == 1
    monkeypatch.setattr(worker, "send_email", lambda *args: calls.append(args))
    worker.deliver(notification.id)
    sent = rows(client)[0]
    assert sent.status == "sent" and sent.sent_at and sent.attempts == 2
    worker.deliver(notification.id)
    assert len(calls) == 2


def test_delivery_stops_after_five_failures(client, auth, monkeypatch):
    identity, worker = setup_alert(client, auth)
    worker.check(identity)
    notification = rows(client)[0]

    def fail(*_):
        raise RuntimeError("Mock failure")

    monkeypatch.setattr(worker, "send_email", fail)
    for _ in range(6):
        worker.deliver(notification.id)
    assert rows(client)[0].status == "failed" and rows(client)[0].attempts == 5


def test_pause_cancels_pending_and_tick_only_checks_due(client, auth, monkeypatch):
    identity, worker = setup_alert(client, auth)
    calls = []
    monkeypatch.setattr(worker, "send_email", lambda *args: calls.append(args))
    worker.tick()
    worker.tick()
    assert len(calls) == 1
    # Future ticks don't scrape until the interval elapses.
    with client.app.state.sessions() as db:
        checked = db.get(PriceAlert, identity).last_checked_at
    worker.tick()
    with client.app.state.sessions() as db:
        assert db.get(PriceAlert, identity).last_checked_at == checked
    client.patch(f"/alerts/{identity}", headers=auth, json={"active": False})
    due(client, identity)
    worker.tick()
    assert len(calls) == 1


def test_worker_lock_excludes_overlap(client):
    engine = client.app.state.db_engine
    with worker_lock(engine) as first:
        assert first
        with worker_lock(engine) as second:
            assert not second
    with worker_lock(engine) as again:
        assert again


def test_delete_alert_cascades_outbox(client, auth):
    identity, worker = setup_alert(client, auth)
    worker.check(identity)
    assert len(rows(client)) == 1
    assert client.delete(f"/alerts/{identity}", headers=auth).status_code == 204
    assert rows(client) == []
