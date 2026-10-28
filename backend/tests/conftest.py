import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.main import create_app
from app.models import Base, User
from app.settings import Settings


@pytest.fixture
def client(tmp_path):
    # TEST_DATABASE_URL must point to an expendable test database: this fixture
    # recreates the schema. Normal application DATABASE_URL is never used here.
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    settings = Settings(_env_file=None, jwt_secret="test-only-" + uuid4().hex,
                        database_url=url, fixture_mode=True, smtp_host="mock.invalid")
    app = create_app(settings)
    Base.metadata.drop_all(app.state.db_engine)
    Base.metadata.create_all(app.state.db_engine)
    with TestClient(app) as test_client:
        # Eliminate demo rate-limit delays without changing production settings.
        for vendor in app.state.search.store.all():
            app.state.search.store.update(vendor, min_request_interval_s=0, max_retries=0)
        yield test_client
    Base.metadata.drop_all(app.state.db_engine)
    app.state.db_engine.dispose()


def account(client, email="buyer@example.com", administrator=False):
    credentials = {"email": email, "password": "correct-horse-battery-123"}
    response = client.post("/auth/register", json=credentials)
    assert response.status_code == 201, response.text
    if administrator:
        with client.app.state.sessions.begin() as db:
            db.execute(update(User).where(User.email == email).values(is_admin=True))
    response = client.post("/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.fixture
def auth(client):
    return account(client)


@pytest.fixture
def admin_auth(client):
    return account(client, "admin@example.com", True)


def search(client, auth, **changes):
    return client.post("/searches", headers=auth, json={"query": "amul butter 500 g", "category": "groceries"} | changes)
