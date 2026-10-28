# Local verification — 28 October 2026

| Check | Result |
|---|---|
| Full engine + backend suite with temporary SQLite | **92 passed** |
| Full engine + backend suite with dedicated PostgreSQL 16 | **92 passed** |
| Alembic initial migration on PostgreSQL | Passed |
| Alembic model/schema comparison on PostgreSQL | No new upgrade operations detected |
| SQLite downgrade → upgrade → schema comparison | Passed; no schema drift |
| Native HTTP API register → login → fixture search → saved history | Passed on PostgreSQL |
| Separate `python -m app.worker --once` execution | Passed on PostgreSQL |
| Docker Compose configuration validation | Passed |
| Python compilation | Passed |
| Original engine source comparison with upstream checkout | Unchanged |
| OpenAPI export | 25 route paths; saved as `docs/openapi.json` |

Test environment: Python 3.12, a local PostgreSQL 16 instance, synthetic engine fixtures. Test SMTP and browser push calls were mocked; no real notifications were sent. One upstream Starlette deprecation warning recommends its newer HTTP test-client dependency; no tests failed.

Live retailer scraping, real SMTP/browser push delivery, and a Docker image build/run were **not** verified. The local Docker daemon was unavailable, so runtime checks used native Python and PostgreSQL. Follow the setup and deployment notes for those environment-specific steps.
