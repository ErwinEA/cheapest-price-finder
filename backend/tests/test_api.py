from datetime import timedelta

import jwt
import pytest
from sqlalchemy import func, select

from app.main import create_app
from app.models import PriceListing, ScrapingJob, User, now
from app.security import passwords
from .conftest import account, search


def test_auth_hashing_duplicate_login_logout(client, auth):
    assert client.get("/users/me").status_code == 401
    me = client.get("/users/me", headers=auth).json()
    assert "password_hash" not in me and not me["is_admin"]
    with client.app.state.sessions() as db:
        user = db.get(User, me["id"])
        assert user.password_hash.startswith("$argon2id$")
        assert passwords.verify("correct-horse-battery-123", user.password_hash)
    assert client.post("/auth/register", json={"email": "BUYER@example.com", "password": "another-password-123"}).status_code == 409
    assert client.post("/auth/login", json={"email": "buyer@example.com", "password": "wrong-password-123"}).status_code == 401
    assert client.get("/admin/sites", headers=auth).status_code == 403
    assert client.post("/auth/logout", headers=auth).status_code == 204
    assert client.get("/users/me", headers=auth).status_code == 401


def test_password_change_revokes_tokens(client, auth):
    assert client.post("/auth/change-password", headers=auth, json={
        "current_password": "correct-horse-battery-123", "new_password": "replacement-password-123"}).status_code == 204
    assert client.get("/users/me", headers=auth).status_code == 401
    assert client.post("/auth/login", json={"email": "buyer@example.com", "password": "replacement-password-123"}).status_code == 200


def test_expired_and_forged_tokens(client, auth):
    settings = client.app.state.settings
    identity = client.get("/users/me", headers=auth).json()["id"]
    claims = {"sub": identity, "ver": 0, "iat": now() - timedelta(hours=2),
              "exp": now() - timedelta(hours=1), "iss": "pricefinder", "aud": "pricefinder-api"}
    expired = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    assert client.get("/users/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    claims["exp"] = now() + timedelta(hours=1)
    forged = jwt.encode(claims, "wrong-secret-" * 5, algorithm="HS256")
    assert client.get("/users/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


@pytest.mark.parametrize("changes", [
    {"query": " "}, {"query": "!@#$"}, {"query": "a" * 201}, {"category": "unknown"},
    {"vendors": []}, {"vendors": ["unknown"]}, {"category": "electronics", "vendors": ["bigbasket"]},
])
def test_search_validation(client, auth, changes):
    assert search(client, auth, **changes).status_code == 422


def test_search_persistence_prices_and_ownership(client, auth):
    response = search(client, auth)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["demo"] and data["currency"] == "INR" and data["status"] == "succeeded"
    assert len(data["vendors"]) == 3 and data["results"]
    assert all("productId" in item for item in data["results"])
    history = client.get("/searches", headers=auth).json()
    assert history[0]["id"] == data["searchId"]
    assert client.get(f"/searches/{data['searchId']}", headers=auth).json()["result"] == data
    product = data["results"][0]["productId"]
    assert client.get(f"/products/{product}/prices", headers=auth).json()
    assert client.get("/products", headers=auth, params={"q": "%"}).json() == []
    other = account(client, "other@example.com")
    assert client.get(f"/searches/{data['searchId']}", headers=other).status_code == 404
    assert client.delete(f"/searches/{data['searchId']}", headers=other).status_code == 404
    assert client.delete(f"/searches/{data['searchId']}", headers=auth).status_code == 204
    assert client.get(f"/products/{product}/prices", headers=auth).json()
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(PriceListing)) > 0


def test_category_selects_sites_and_disabled_sites(client, auth, admin_auth):
    assert client.patch("/admin/sites/amazon", headers=admin_auth, json={"enabled": False}).status_code == 200
    result = search(client, auth, category="electronics", query="iphone 15").json()
    assert [v["vendor"] for v in result["vendors"]] == ["flipkart"]
    assert search(client, auth, vendors=["amazon"]).status_code == 422
    assert client.patch("/admin/sites/flipkart", headers=admin_auth, json={"enabled": False}).status_code == 200
    assert search(client, auth, category="electronics").status_code == 422


def test_partial_failure_durable_logs_admin_retry(client, auth, admin_auth):
    service = client.app.state.search
    original = service.engine.adapters["amazon"].parse
    service.engine.adapters["amazon"].parse = lambda _: []
    data = search(client, auth).json()
    assert data["status"] == "partial" and data["results"]
    jobs = client.get("/admin/jobs", headers=admin_auth, params={"status": "failed"}).json()
    assert len(jobs) == 1 and jobs[0]["vendor_id"] == "amazon"
    identity = jobs[0]["id"]
    assert client.get(f"/admin/jobs/{identity}/logs", headers=admin_auth).json()["log"]
    # The engine's volatile registry is cleared; jobs remain in SQL.
    assert not service.engine.scheduler.list_jobs()
    service.engine.adapters["amazon"].parse = original
    retried = client.post(f"/admin/jobs/{identity}/retry", headers=admin_auth)
    assert retried.status_code == 200 and retried.json()["status"] == "succeeded"
    assert client.get(f"/admin/jobs/{identity}", headers=admin_auth).json()["status"] == "failed"
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ScrapingJob)) == 4


def test_all_vendors_failed_and_site_settings_validation(client, auth, admin_auth):
    for adapter in client.app.state.search.engine.adapters.values():
        adapter.parse = lambda _: []
    assert search(client, auth).json()["status"] == "failed"
    assert client.patch("/admin/sites/amazon", headers=admin_auth, json={"max_retries": -1}).status_code == 422
    assert client.patch("/admin/sites/amazon", headers=admin_auth, json={"base_url": "http://127.0.0.1"}).status_code == 422
    assert client.patch("/admin/sites/amazon", headers=admin_auth, json={"selectors": {"item": "["}}).status_code == 422
    assert client.patch("/admin/sites/amazon", headers=admin_auth, json={"delivery_fee": "7.50"}).json()["delivery_fee"] == "7.50"


def test_alert_crud_access_and_unconfigured_delivery(client, auth):
    product = search(client, auth).json()["results"][0]["productId"]
    response = client.post("/alerts", headers=auth, json={"product_id": product, "target_price": "500.00"})
    assert response.status_code == 201, response.text
    identity = response.json()["id"]
    other = account(client, "other@example.com")
    for method in ("get", "delete"):
        assert getattr(client, method)(f"/alerts/{identity}", headers=other).status_code == 404
    assert client.patch(f"/alerts/{identity}", headers=auth, json={"active": False}).json()["active"] is False
    assert client.post("/alerts", headers=auth, json={"product_id": product, "target_price": -1}).status_code == 422
    assert client.post("/alerts", headers=auth, json={"product_id": product, "target_price": 100, "channel": "sms"}).status_code == 422
    client.app.state.settings.smtp_host = None
    assert client.post("/alerts", headers=auth, json={"product_id": product, "target_price": 100}).status_code == 503
    assert client.delete(f"/alerts/{identity}", headers=auth).status_code == 204


def test_push_endpoint_validation(client, auth):
    assert client.post("/push/subscriptions", headers=auth, json={"endpoint": "https://127.0.0.1/test", "keys": {}}).status_code == 422
    assert client.post("/push/subscriptions", headers=auth, json={"endpoint": "https://fcm.googleapis.com/fcm/send/test", "keys": {"p256dh": "broken", "auth": "broken"}}).status_code == 422


def test_jobs_survive_new_application(client, auth, admin_auth):
    from fastapi.testclient import TestClient
    search(client, auth)
    settings = client.app.state.settings
    with TestClient(create_app(settings)) as restarted:
        assert len(restarted.get("/admin/jobs", headers=admin_auth).json()) == 3
        assert restarted.get("/admin/sites", headers=admin_auth).status_code == 200
