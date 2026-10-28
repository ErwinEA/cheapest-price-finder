# Cheapest Price Finder — Backend + Engine

This adds the **Backend (Person B)** work from your screenshot to the existing [ErwinEA/cheapest-price-finder engine](https://github.com/ErwinEA/cheapest-price-finder). The original engine files are unchanged.

Implemented: FastAPI REST endpoints, JWT authentication, Argon2 password hashing, PostgreSQL models and migrations, query/category validation, vendor selection, engine integration, saved search history and price observations, scheduled price alerts, SMTP email, browser Web Push, and protected admin endpoints for jobs, logs, and site settings.

**Start with fixture mode.** It uses the original engine's synthetic retailer HTML and labels results `demo: true`. It demonstrates backend behavior; its prices are not live offers. The engine's live selectors and fee defaults still need verification before real use.

## Start with Docker and PostgreSQL

Requires Docker Compose and Python 3.11+ for the configuration helper. From this repository's root:

```sh
python3 scripts/configure.py
docker compose up --build -d
```

Open:

- API and interactive endpoint documentation: [localhost:8000/docs](http://localhost:8000/docs)
- Health: [localhost:8000/health](http://localhost:8000/health)
- Local test email inbox: [localhost:8025](http://localhost:8025)

Compose runs PostgreSQL, one migration job, the API, the alert worker, and Mailpit. Demo emails stay in Mailpit; it does not send them to real inboxes. Only the API and Mailpit UI are published, on localhost. The database has a persistent volume. `docker compose down` stops services and preserves data.

Create an admin with a prompted password:

```sh
docker compose exec api python -m app.cli create-admin --email admin@example.com
```

Registration never grants admin access. Log in with this account in `/docs`, copy its `access_token`, and use **Authorize** to enter the token. Login accepts JSON, as shown in the examples below.

## Start locally without Docker

Python 3.11 or 3.12 is recommended. SQLite is supported for local development; deployment uses PostgreSQL.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.lock
python scripts/configure.py
cd backend
alembic upgrade head
uvicorn app.main:create_app --factory --reload --port 8000
```

Run database and API commands from `backend/`; the default SQLite file is relative to that directory. Settings always load the root `.env`. For PostgreSQL, change `DATABASE_URL` to `postgresql+psycopg://USER:PASSWORD@HOST:5432/pricefinder` **before** running the migration. Reserved characters in credentials must be URL encoded.

In a second terminal, with the same virtual environment active:

```sh
cd backend
python -m app.worker
```

The worker polls every minute; each active alert is checked every 15 minutes by default. Set `ALERT_INTERVAL_SECONDS` to change that interval, with a minimum of 60 seconds. For cron, run `python -m app.worker --once` once per minute instead of the persistent worker. PostgreSQL advisory locking prevents overlapping scheduler processes from checking or delivering the same queue concurrently.

Local Python mode leaves email disabled until `SMTP_HOST` is configured. Point it at a local SMTP inbox or your provider, set `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `EMAIL_FROM`, then restart API and worker. Email alert creation returns `503` when no SMTP host is set.

## Try the API

The API must already be running. From the repository root with dependencies installed:

```sh
python scripts/demo.py
```

This creates a fresh demo account, logs in, searches through the actual engine using fixtures, and verifies saved history. To exercise endpoints manually:

```sh
curl -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"buyer@example.com","password":"a-long-demo-password-123"}'

curl -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"buyer@example.com","password":"a-long-demo-password-123"}'
```

Copy `access_token` from login and set it in your terminal:

```sh
export TOKEN='paste-access-token-here'

curl -X POST http://localhost:8000/searches \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"amul butter 500 g","category":"groceries"}'

curl http://localhost:8000/searches -H "Authorization: Bearer $TOKEN"
```

Search response groups include `productId`, `bestOffer`, `offers`, `relevance`, and `savings`. Use the ID for the particular product/pack you want to watch:

```sh
curl -X POST http://localhost:8000/alerts \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"product_id":"paste-productId-here","target_price":"500.00","channel":"email"}'

curl http://localhost:8000/notifications -H "Authorization: Bearer $TOKEN"
```

With Compose, the worker puts qualifying demo emails in Mailpit on its next tick. Prices include the engine's configured delivery and handling fees. All prices are INR. Alerts use `<= target_price`, require an in-stock matching product, and rescrape instead of trusting an old price snapshot.

## Project instructions and tasks

- [Backend requirements and completed task checklist](docs/BACKEND_TASKS.md)
- [Architecture, data model, frontend contract, and endpoint reference](docs/API_AND_ARCHITECTURE.md)
- [Browser push setup](docs/WEB_PUSH.md)
- [Verification and deployment notes](docs/VERIFICATION.md)
- [Local test results](docs/TEST_REPORT.md)
- [Original engine setup and live-site caveats](engine/README.md)

## Run tests

From the root with the virtual environment active:

```sh
python -m pytest -q
```

The combined suite includes the original engine tests and backend integration tests. Tests use temporary SQLite databases and fixture pages; SMTP and push transports are mocked. To test PostgreSQL, create a **disposable, dedicated** test database and set `TEST_DATABASE_URL` when running pytest. The test fixture drops and recreates tables in that test database; it never reads the application's `DATABASE_URL` for test storage.

The API exposes the current OpenAPI specification at `/openapi.json`; a saved copy is in `docs/openapi.json`. It does not deploy to or push changes to GitHub. This delivery is a local implementation for review and integration.
