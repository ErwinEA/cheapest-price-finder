# Verification and deployment notes

## What is verified locally

The delivery's test report records the final test counts and smoke-check results. The checks include:

- Original engine tests with synthetic Amazon/Flipkart/BigBasket fixtures.
- Backend API integration tests against temporary SQLite and a dedicated PostgreSQL 16 database.
- JWT expiry/signature/revocation, Argon2 hashing, duplicate email handling, password changes, and admin permissions.
- Query/category/site validation, disabled sites, partial and total vendor failures, durable job/log access, history ownership, and retained shared price observations.
- Matching alerts against the selected pack, trigger deduplication, rearming, partial-failure behavior, delivery retry bounds, and scheduler overlap exclusion.
- Notification transport tests with mocked SMTP and browser push providers; these never send real messages.
- Alembic schema verification and a real HTTP fixture search/history smoke test.

## Scope and known limits

1. **Live retailer data is unverified.** The original engine explicitly identifies its selectors and fees as untested defaults. Its fixture HTML is synthetic. Before setting `FIXTURE_MODE=false`, install Chromium for BigBasket (`playwright install chromium`), use the engine diagnostics, verify selectors, configure delivery location, and confirm fees. Engine anti-bot failures are exposed per vendor; the backend does not bypass them.
2. **Product matching inherits engine limitations.** Conservative fuzzy matching rejects known incompatible sizes and model variants, but it is not a retailer SKU identity system. Accessories and changing retailer titles may still require further matching work. Catalog identities can change with representative titles.
3. **Searches are synchronous.** Vendor calls are concurrent inside one search, while searches are serialized per API process. Choose HTTP/proxy timeouts to cover configured retries. Before scaling many processes, implement distributed vendor rate reservations; the engine's existing rate limiter is per process, including a separate limiter in the alert worker.
4. **Worker delivery is at least once.** A durable outbox and unique trigger key prevent repeated enqueueing at a sustained low price, but external provider acceptance and a SQL commit cannot be atomic. A crash or mixed push-device outcomes can duplicate delivery. Provider delivery/read confirmation is not implemented.
5. **Email ownership is not verified.** Registration, login, logout, and change-password are implemented. Email verification, forgotten-password recovery, OAuth/social login, and refresh tokens are outside the screenshot's requirements. Protect public registration before using externally delivered email at scale.
6. **Interrupted searches need operational recovery.** Jobs/logs persist at start and completion; per-attempt live logs stay in the engine until completion. Abrupt process death can leave a running search/job. Automatic recovery/cancellation and streaming progress are not implemented. Retry endpoints support terminal failed/skipped jobs.
7. **Operator configuration is deliberately bounded.** HTTP admin updates can change selectors, fees, enable flags, timeout, retry, and rate settings. Sensitive cookies/headers and target URLs require a trusted operator database change. Capability categories are currently seeded configuration, not an admin-editable taxonomy.
8. **Pagination and batches are bounded.** Worker ticks process 100 due alerts and 100 pending notifications. High-volume deployments should add queue scheduling, cache/coalesce repeated product checks, and retention for observed listings/jobs/history.

## Deployment steps

1. Create a PostgreSQL database; configure `DATABASE_URL`, a fresh random `JWT_SECRET`, explicit `CORS_ORIGINS`, and a backup policy.
2. Install from `backend/requirements.lock`, run `alembic upgrade head` from `backend/`, and use `create-admin` for an administrative account.
3. Verify live engine settings before turning off fixture mode. Demo and live data share the same schema; use separate databases/environments to keep synthetic observations out of live reports.
4. Configure SMTP STARTTLS/credentials or mount the VAPID private key. Compose's Mailpit defaults are for local testing. Set the real public sender/contact values.
5. Run the API and one worker (or a once-per-minute cron call). Configure HTTPS, ingress authentication/search rate limits, request limits, and timeouts for the target environment. Keep credentials out of images and Git.
6. Use `/health` for database reachability, inspect worker logs, and monitor failed jobs/notifications. An `ok` health response does not verify retailer connectivity or worker freshness.

## Migration workflow

Run from `backend/` with configured settings:

```sh
alembic upgrade head
alembic check
```

For future model edits, create and review a new migration with `alembic revision --autogenerate -m "Describe change"`; never edit an already deployed revision. `alembic downgrade base` deletes the tables and data and is only appropriate in disposable test databases.

The Docker Compose configuration is supplied, but a Docker image build/run requires a running Docker daemon. Native Python execution and PostgreSQL checks are independent of Docker.

## Optional GitHub Actions setup

`docs/ci-tests.yml.example` contains a ready-to-use GitHub Actions workflow for SQLite and PostgreSQL checks. It is provided as a template because the GitHub connection used for this delivery does not have the `workflow` permission required to create files under `.github/workflows/`.

To activate it later, use an account/connection with workflow permissions, copy the template to `.github/workflows/tests.yml`, and commit that file. No GitHub Actions workflow is activated by this delivery.
