# Backend (Person B): requirements, instructions, and tasks

Source: the visible **2. Backend (Person B)** section in the provided screenshot. The frontend and engine sections are cut off; this implementation does not invent requirements from those sections. The existing repository supplies Person C's engine.

## 1. REST API, JWT authentication, and password hashing

- [x] Build a FastAPI service around the Python engine.
- [x] Register users with validated, case-normalized unique emails and 12–128 character passwords.
- [x] Store Argon2id hashes; never return password hashes.
- [x] Issue signed, expiring JWT access tokens with fixed algorithm, issuer, and audience validation.
- [x] Load authorization roles from the database, rather than trusting a role supplied by the client.
- [x] Support current-user lookup, password changes, and logout from all existing sessions through token version revocation.
- [x] Require authentication for search, history, catalog, alerts, and notifications.
- [x] Enforce ownership on saved history, alerts, and push subscriptions; admin role on operational endpoints.
- [x] Include OpenAPI documentation and runnable request examples.

Acceptance: registration cannot create admins; invalid/expired/revoked tokens return 401; ordinary users receive 403 on admin endpoints and 404 for another user's private resources.

## 2. Database tables

- [x] User: credentials, role, token version, creation time.
- [x] SearchHistory: owner, validated query/category, selected sites, status, saved response.
- [x] PriceAlert: owner, product, target, sites, channel, activation and check state.
- [x] Product: category, representative name, original query, matching anchor.
- [x] Vendor: engine adapter identity and supported categories.
- [x] PriceListing: product/vendor links, observed and effective prices, stock, URL, timestamp, full snapshot.
- [x] SiteConfig: persistent engine settings and update timestamp.
- [x] ScrapingJob: persistent engine job ID, vendor, query, status, attempts/errors/logs via job detail.
- [x] Add Notification as a durable delivery outbox and PushSubscription for browser registrations.
- [x] Add PostgreSQL driver, SQLAlchemy models, Alembic migration, indexes, and foreign keys.

Acceptance: search observations and completed job logs survive an application restart. Removing search history preserves shared price observations. Removing an alert removes its delivery records.

## 3. Search orchestration

- [x] Validate query length/content and category against the supported category registry.
- [x] Reject unknown, disabled, or category-incompatible requested sites before invoking the engine.
- [x] Choose all enabled compatible sites when no explicit sites are supplied.
- [x] Call `Engine.search(query, vendors)` directly; reuse its normalization, effective pricing, matching, ranking, retry, and scraper behavior.
- [x] Replace the in-memory site configuration store with a database-backed implementation.
- [x] Save search history, products, observed prices, engine jobs, and logs.
- [x] Return `succeeded`, `partial`, or `failed` so vendor outages remain visible.
- [x] Keep the original engine response fields; add backend IDs, category, currency, and demo/status metadata.

Acceptance: electronics searches exclude BigBasket; disabling a site changes future searches without restarting the API; one failed scraper preserves successful vendor results.

Category verification here means a supported category plus configured site compatibility. It is not a machine-learning determination that the submitted words describe the right category. The initial category/vendor matrix is application configuration, not a verified statement about every retailer department.

## 4. Scheduled alerts and notifications

- [x] Create, read, update, pause, and delete price alerts for products returned by search.
- [x] Run a separate worker, with a single-run cron option and overlap protection.
- [x] Check fresh engine results for an in-stock match to the original product anchor.
- [x] Compare effective price to the target, preserving pack-size/model checks from the engine.
- [x] Trigger once on crossing below target; rearm after a complete result above target/no in-stock match.
- [x] Preserve trigger state through partial vendor failures.
- [x] Save notification intent and alert trigger state in one transaction.
- [x] Retry queued delivery up to five times; expose pending/sent/failed/cancelled state.
- [x] Send SMTP email or VAPID Web Push; remove expired push subscriptions.
- [x] Provide local Mailpit email testing and frontend service-worker setup instructions.

Acceptance: remaining below target does not repeatedly enqueue; a different pack size does not trigger; SMTP failure leaves a retryable delivery record; pausing/retargeting cancels pending deliveries.

## 5. Admin jobs, logs, and site configuration

- [x] List jobs with pagination and status filtering.
- [x] Inspect a job and its engine logs.
- [x] Retry a failed/skipped job as a new durable job while preserving its old record.
- [x] Read site configuration without returning stored cookies or headers.
- [x] Validate and update selectors, enabled flags, timeouts, retry/rate-limit controls, result limits, and pricing fees.
- [x] Validate CSS selectors before saving them; do not expose an arbitrary outbound scraper URL through the API.
- [x] Provide a terminal-only command to create the first admin.

## 6. Setup, integration, and remaining environment tasks

- [x] Provide PostgreSQL + API + worker + local SMTP Compose configuration.
- [x] Provide a local SQLite development path and generated secret configuration.
- [x] Include fixture-based tests and an API demo script.
- [x] Document the frontend contract, schema, environment variables, and known limits.
- [ ] Verify live vendor selectors and fee policies using the engine diagnostics and real authorized data.
- [ ] Set delivery-location cookies for location-dependent listings, particularly BigBasket.
- [ ] Supply the deployment's SMTP credentials or VAPID keys and exercise actual delivery.
- [ ] Connect the frontend to the documented endpoints and install its service worker for browser push.
- [ ] Configure the target host's TLS, ingress limits, database backups, and secrets before public deployment.

These final tasks depend on the deployment, retailer pages, or frontend. They are not represented as completed by fixture tests.
