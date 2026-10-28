from base64 import urlsafe_b64encode
from unittest.mock import MagicMock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException
from sqlalchemy import select

from app.models import PushSubscription
from app.worker import AlertWorker
from .conftest import account, search


def subscription_data():
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": "https://fcm.googleapis.com/fcm/send/demo-test",
            "keys": {"p256dh": urlsafe_b64encode(key).decode().rstrip("="),
                     "auth": urlsafe_b64encode(b"test-auth-key-16").decode().rstrip("=")}}


def test_smtp_message_and_tls(client, monkeypatch):
    settings = client.app.state.settings
    settings.smtp_username, settings.smtp_password = "smtp-user", "test-password"
    smtp_class = MagicMock()
    smtp = smtp_class.return_value.__enter__.return_value
    monkeypatch.setattr("app.worker.smtplib.SMTP", smtp_class)
    worker = AlertWorker(client.app.state.sessions, settings, client.app.state.search)
    worker.send_email("buyer@example.com", {"product": "Amul Butter 500g", "price": "300.00",
                      "vendor": "amazon", "url": "https://www.amazon.in/example", "demo": True}, "test-id")
    smtp_class.assert_called_once_with("mock.invalid", 587, timeout=20)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("smtp-user", "test-password")
    message = smtp.send_message.call_args.args[0]
    assert message["To"] == "buyer@example.com"
    assert message["Subject"].startswith("[DEMO]")
    assert "300.00" in message.get_content()


def test_push_subscription_ownership_delivery_and_expiry(client, auth, monkeypatch):
    body = subscription_data()
    # 16-byte auth secret required by the protocol.
    body["keys"]["auth"] = urlsafe_b64encode(b"0123456789abcdef").decode().rstrip("=")
    response = client.post("/push/subscriptions", headers=auth, json=body)
    assert response.status_code == 201, response.text
    identity = response.json()["id"]
    assert client.post("/push/subscriptions", headers=auth, json=body).json()["id"] == identity
    other = account(client, "second@example.com")
    assert client.post("/push/subscriptions", headers=other, json=body).status_code == 409
    assert client.delete(f"/push/subscriptions/{identity}", headers=other).status_code == 404
    client.app.state.settings.vapid_private_key = "mock-private-key"
    worker = AlertWorker(client.app.state.sessions, client.app.state.settings, client.app.state.search)
    with client.app.state.sessions() as db:
        subscriptions = list(db.scalars(select(PushSubscription)))
    webpush = MagicMock()
    monkeypatch.setattr("pywebpush.webpush", webpush)
    worker.send_push(subscriptions, {"title": "Price alert"})
    assert webpush.call_args.kwargs["subscription_info"] == body
    assert webpush.call_args.kwargs["vapid_private_key"] == "mock-private-key"
    expired = MagicMock()
    expired.status_code = 410
    webpush.side_effect = WebPushException("Gone", response=expired)
    import pytest
    with pytest.raises(RuntimeError):
        worker.send_push(subscriptions, {"title": "Price alert"})
    with client.app.state.sessions() as db:
        assert not list(db.scalars(select(PushSubscription)))


def test_push_alert_requires_keys_and_subscription(client, auth):
    product = search(client, auth).json()["results"][0]["productId"]
    body = {"product_id": product, "target_price": "500", "channel": "push"}
    assert client.post("/alerts", headers=auth, json=body).status_code == 503
    client.app.state.settings.vapid_private_key = "mock-private-key"
    assert client.post("/alerts", headers=auth, json=body).status_code == 422
    subscription = subscription_data()
    subscription["keys"]["auth"] = urlsafe_b64encode(b"0123456789abcdef").decode().rstrip("=")
    assert client.post("/push/subscriptions", headers=auth, json=subscription).status_code == 201
    assert client.post("/alerts", headers=auth, json=body).status_code == 201
