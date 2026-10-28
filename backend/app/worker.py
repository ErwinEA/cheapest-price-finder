"""Run separately from web workers, or call --once from cron."""
import argparse
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import logging
import signal
import smtplib
import ssl
import tempfile
import threading
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select, text

from .db import database
from .engine_bridge import SearchService, seed
from .models import Notification, PriceAlert, Product, PushSubscription, User, now
from .settings import Settings

from engine import Listing, matchListing
from engine.normalize import extract_quantity

log = logging.getLogger(__name__)


@contextmanager
def worker_lock(engine):
    """Keep overlapping worker/cron processes from checking or sending twice."""
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection, connection.begin():
            acquired = connection.scalar(text("SELECT pg_try_advisory_xact_lock(193746281)"))
            yield bool(acquired)
    else:
        import fcntl
        key = hashlib.sha256(str(engine.url).encode()).hexdigest()[:20]
        with (Path(tempfile.gettempdir()) / f"pricefinder-{key}.lock").open("w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class AlertWorker:
    def __init__(self, sessions, settings, service):
        self.sessions, self.settings, self.service = sessions, settings, service

    def check(self, identity):
        with self.sessions() as db:
            alert = db.get(PriceAlert, identity)
            if not alert or not alert.active:
                return
            product = db.get(Product, alert.product_id)
            anchor_data = product.anchor
            anchor = Listing(vendor=anchor_data["vendor"], name=anchor_data["productName"],
                             price=Decimal(str(anchor_data["price"])), url=anchor_data["url"],
                             quantity=extract_quantity(anchor_data["productName"]))
        error, best, complete = None, None, False
        try:
            result, payload = self.service.execute(product.query, product.category, alert.vendors)
            # Never compare the target against a different pack size/variant.
            offers = [offer for comparison in result.comparisons for offer in comparison.offers
                      if offer.in_stock and matchListing(anchor, offer).is_match]
            if offers:
                best = min(offers, key=lambda o: o.effective_price if o.effective_price is not None else o.price)
            complete = payload["status"] == "succeeded"
            if not complete:
                error = "One or more vendors failed; any successful offers are still checked"
        except Exception as exc:
            log.exception("Alert check failed for %s", identity)
            error = f"{type(exc).__name__}: {exc}"

        with self.sessions.begin() as db:
            current = db.get(PriceAlert, identity, with_for_update=True)
            if not current or not current.active:
                return
            current.last_checked_at = now()
            current.next_check_at = now() + timedelta(seconds=self.settings.alert_interval_seconds)
            current.last_error = error
            price = (best.effective_price if best.effective_price is not None else best.price) if best else None
            qualifies = price is not None and price <= current.target_price
            if qualifies and not current.below_target:
                current.below_target = True
                current.generation += 1
                db.add(Notification(alert_id=current.id, user_id=current.user_id,
                                    dedupe_key=f"{current.id}:{current.generation}", channel=current.channel,
                                    payload={"title": "Price alert", "product": product.name,
                                             "productId": product.id, "price": str(price), "currency": "INR",
                                             "vendor": best.vendor, "url": best.url,
                                             "demo": self.settings.fixture_mode}))
            elif not qualifies and complete:
                # A complete result above target (or no in-stock match) rearms.
                # Partial failures must not spuriously rearm a triggered alert.
                current.below_target = False

    def deliver(self, identity):
        with self.sessions() as db:
            row = db.get(Notification, identity)
            alert = db.get(PriceAlert, row.alert_id) if row else None
            if not row or row.status != "pending":
                return
            if not alert or not alert.active:
                row.status = "cancelled"
                db.commit()
                return
            user = db.get(User, row.user_id)
            subscriptions = list(db.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id)))
            # Save attempt before the external side effect. Delivery is at least
            # once: an SMTP acceptance followed by process death can duplicate.
            row.attempts += 1
            db.commit()
            try:
                if row.channel == "email":
                    self.send_email(user.email, row.payload, row.id)
                else:
                    self.send_push(subscriptions, row.payload)
            except Exception as exc:
                row.error = f"{type(exc).__name__}: delivery failed; check worker logs"
                log.exception("Notification %s delivery failed", row.id)
                if row.attempts >= 5:
                    row.status = "failed"
            else:
                row.status, row.sent_at, row.error = "sent", now(), None
            db.commit()

    def send_email(self, recipient, payload, identity):
        if not self.settings.smtp_host:
            raise RuntimeError("SMTP is not configured")
        message = EmailMessage()
        demo = "[DEMO] " if payload["demo"] else ""
        message["Subject"] = f"{demo}Price alert: {payload['product']}"
        message["From"], message["To"] = self.settings.email_from, recipient
        message["Message-ID"] = f"<{identity}@pricefinder>"
        message.set_content(f"{payload['product']} is INR {payload['price']} on {payload['vendor']}.\n"
                            f"{payload['url']}\nPrices can change before checkout.")
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as smtp:
            if self.settings.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self.settings.smtp_username:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password or "")
            smtp.send_message(message)

    def send_push(self, subscriptions, payload):
        from pywebpush import WebPushException, webpush
        if not subscriptions or not self.settings.vapid_private_key:
            raise RuntimeError("No push subscriptions or missing VAPID private key")
        failures = []
        for subscription in subscriptions:
            try:
                webpush(subscription_info={"endpoint": subscription.endpoint, "keys": subscription.keys},
                        data=json.dumps(payload), vapid_private_key=self.settings.vapid_private_key,
                        vapid_claims={"sub": self.settings.vapid_subject}, timeout=20)
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    with self.sessions.begin() as db:
                        stale = db.get(PushSubscription, subscription.id)
                        if stale:
                            db.delete(stale)
                failures.append(exc)
        if failures:
            raise RuntimeError("Some browser push deliveries failed") from failures[0]

    def tick(self):
        with self.sessions() as db:
            identities = list(db.scalars(select(PriceAlert.id).where(
                PriceAlert.active.is_(True), PriceAlert.next_check_at <= now()).limit(100)))
        for identity in identities:
            self.check(identity)
        with self.sessions() as db:
            # Retry once per tick, never in a tight loop.
            identities = list(db.scalars(select(Notification.id).where(Notification.status == "pending")
                              .order_by(Notification.created_at).limit(100)))
        for identity in identities:
            self.deliver(identity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="One scheduler tick for cron")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    engine, sessions = database(settings.database_url)
    seed(sessions)
    worker = AlertWorker(sessions, settings, SearchService(sessions, settings))
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        while not stop.is_set():
            try:
                with worker_lock(engine) as acquired:
                    if acquired:
                        worker.tick()
            except Exception:
                log.exception("Worker tick failed; will retry on the next tick")
                if args.once:
                    raise
            if args.once:
                break
            stop.wait(60)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
