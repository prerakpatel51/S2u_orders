# S2U Orders

S2U Orders is a Django and Django REST Framework order-management application for KORONA.cloud. It provides store-specific order lists, product search and barcode workflows, stock transfers, supplier and commodity information, configurable AG Grid views, and PDF/XLSX exports.

## Architecture

- Django and Django REST Framework provide the web application and API.
- PostgreSQL stores users, order lists, cached KORONA data, stock, and rolling sales totals.
- Redis is the Celery broker and result backend.
- Two independent Railway Object Storage buckets keep live delivery proof and verified disaster-recovery copies independently of application deploys.
- Celery Worker runs synchronization jobs.
- Celery Beat schedules KORONA stores, products, stocks, and receipts synchronization.
- Docker Compose provides an isolated local development environment.

## Secure local development

Requirements:

- Docker Desktop with Docker Compose
- KORONA.cloud API credentials

Create a local configuration file. Never commit this file:

```sh
cp .env.example .env
```

Fill in the KORONA credentials and use development-only secrets in `.env`. Start the dedicated development stack:

```sh
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

Open [http://localhost:8000](http://localhost:8000). The application, PostgreSQL, Redis, Celery worker, and scheduler run in separate containers. Local PostgreSQL and Redis are not published to the host network.

Useful commands:

```sh
docker compose ps
docker compose logs -f web worker beat
docker compose exec web python manage.py test orders
docker compose down
```

Use the administrator **Operations** page for the initial KORONA reconciliation and to monitor synchronization status.

## Configuration

All deployment-specific values are environment variables documented in `.env.example`. Important rules:

- Keep `DJANGO_DEBUG=0` outside local development.
- Use a unique, randomly generated `DJANGO_SECRET_KEY` in production.
- Do not put credentials in source code or Docker images.
- `.env` is excluded from both Git and the Docker build context; Compose injects it only at runtime.
- Run exactly one Celery Beat instance.
- Use TLS URLs in `CSRF_TRUSTED_ORIGINS` and for the public application endpoint.
- Restrict access to PostgreSQL and Redis to the application network.

## Data synchronization

KORONA stores and products are revision-synchronized. Receipt revisions update a compact receipt ledger by delta, and affected store/product rolling 30-day requirements are recalculated.

Stock synchronization has three layers:

- Every two minutes, the background service requests stock changes newer than each store's successfully committed revision cursor.
- Product selection can refresh one product immediately for the order workflow.
- At the configured nightly time, a complete store-by-store reconciliation repairs missing or stale local rows and resets stock records no longer returned by KORONA.

If KORONA changes an organizational unit so it is no longer a warehouse, the next catalog or stock poll retires its stock cursor and cached stock rows immediately. A `404 CONDITION_MISMATCH` for that transition is handled as a catalog change and does not fail synchronization for the remaining warehouses.

Monthly need is the non-negative net quantity sold over the trailing 30 calendar days. Receipt revisions update affected daily totals incrementally; returns reduce sales and voids remove the prior contribution. The nightly monthly reconciliation downloads every day in the complete rolling 30-day window from KORONA, replaces each local day only after its download completes, and then recalculates every current product total once.

Receipts that reference a store or product not yet present locally are retained in a deferred queue and replayed after catalog synchronization. The complete nightly monthly download removes stale receipt values and provides a second recovery path for timing races and temporary outages without double-counting sales.

The store stock endpoint is paged at up to 1,000 records. Revision cursors advance only after database writes commit successfully. A cursor does not advance when a stock row references a product that has not reached the local product catalog yet, allowing the next cycle to retry it safely. HTTP 429 and temporary server failures use bounded GET-only retries with exponential backoff.

Order-list stock and monthly values have saved snapshots, but the API displays newer cache values when current records are available. User-entered shelf quantities, supplier quantities, transfers, and notes are not changed by synchronization jobs.

Stock timing, paging, timeout, and retry behavior is configurable using the `KORONA_STOCK_*` and `KORONA_HTTP_*` variables in `.env.example`. The administrator Operations page exposes the incremental and reconciliation services separately.

The Operations page also reports per-store cursor freshness, last successful poll, revision number, cached stock-row count, incremental run duration, stale/missing stores, and the latest nightly reconciliation. It refreshes automatically while open and more frequently while a job is running.

## Rollback

Production changes should be reverted with a new Git commit so history remains auditable:

```sh
git log --oneline
git revert <commit-to-revert>
git push origin main
```

The initial imported application is preserved as commit `255ddb1`.

## Railway deployment

Create PostgreSQL and Redis services, then create Web, short Worker, long Worker, and Beat services from this repository using the same Dockerfile:

- Web: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --worker-class gthread --threads 4 --timeout 60 --keep-alive 5`
- Worker: set `PROCESS_TYPE=worker`; consumes the `short` queue with concurrency 2
- Long Worker: set `PROCESS_TYPE=long-worker`; consumes the `long` queue with concurrency 1
- Beat: `celery -A config beat -l INFO`

Set the variables from `.env.example` on every service. Set `RUN_MIGRATIONS=1` only on the Railway Web service and `RUN_MIGRATIONS=0` on both Workers and Beat. Local Compose uses a dedicated one-shot migration service before application containers start. Worker and Beat startup performs a bounded Redis readiness check and exits on failure so Railway can restart and alert on the failed process.

Configure the Web service deployment healthcheck as `/api/health/`. Railway uses this during deployment; continuous external monitoring should poll `/api/health/runtime/`, which also requires a recent task published by Beat and consumed by the short Worker. Optional `MONITORING_*_HEARTBEAT_URL` variables can notify Better Stack, UptimeRobot, or another POST-compatible heartbeat provider.

The Redis service is a disposable Celery broker: PostgreSQL remains the system of record. Do not use an every-minute `--save 60 1` snapshot policy. Either run the broker without persistence/volume, or use a less aggressive RDB policy such as `--save 900 1 --save 300 10 --save 60 10000` and monitor restart count and snapshot latency.

### Delivery proof storage

Create two private Railway Buckets named `s2u-delivery-proofs` and `s2u-delivery-proofs-dr`. Inject the live bucket credentials with `DELIVERY_BUCKET_*` and the recovery bucket credentials with `DELIVERY_DR_BUCKET_*` into Web, Worker, and Beat. The application rejects a DR configuration that points back to the live bucket. Only the live bucket needs browser CORS allowing `PUT` from the production application origin with the `Content-Type` header; both buckets remain private.

Every confirmed photo and immutable notes snapshot is copied by the worker and stored under the same `deliveries/YYYY/MM/DD/store-NUMBER/delivery-UUID/{invoice,boxes,damage,notes}/` path in both buckets. Each copy is verified by size and SHA-256; a 15-minute reconciliation repairs missed/failed jobs and a weekly integrity pass confirms recovery objects still exist. Photo viewing and ZIP downloads can fall back to the verified DR copy when the live object is unavailable.

The admin verification workspace renders invoices and received cases side by side from the stored full-resolution objects, with an in-place review decision, notes, keywords, and a phone-style full-screen gallery. Supported JPEG, PNG, and WebP uploads are retained byte-for-byte when they fit the upload limit; only unsupported or oversized images are converted.

The superuser Operations page probes the primary and DR buckets independently and reports connectivity latency, tracked evidence size, replication coverage, pending or failed files, recovery catalogs, and ready exports. Confirmed delivery evidence has no application delete endpoint, and deletion is disabled for delivery and asset records in Django Admin; direct object removal therefore requires Railway project access and bucket credentials.

Administrators can build an entire DR archive or a filtered archive by delivery date, store, and keyword. Exports are assembled asynchronously in the DR bucket under `exports/YYYY/MM/DD/`, retain the original dated delivery and backup paths, and include a README, JSON manifest, SHA-256 checksums, and CSV delivery index. Ready export ZIPs expire after `DELIVERY_EXPORT_RETENTION_DAYS` (seven days by default), and the daily cleanup task deletes expired archive objects without touching delivery evidence or catalogs.

PostgreSQL stores searchable metadata, replication state, keywords, checksums, and the audit trail. Nightly compressed metadata catalogs are written only to the DR bucket under `backups/delivery-metadata/YYYY/MM/DD/`; administrators can create/download a catalog, inspect recovery coverage, and retry unsynced files from the verification workspace. Keep Railway PostgreSQL backups enabled because DR object replication protects files, while database backups protect relational state.
