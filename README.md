# S2U Orders

S2U Orders is a Django and Django REST Framework order-management application for KORONA.cloud. It provides store-specific order lists, product search and barcode workflows, stock transfers, supplier and commodity information, configurable AG Grid views, and PDF/XLSX exports.

## Architecture

- Django and Django REST Framework provide the web application and API.
- PostgreSQL stores users, order lists, cached KORONA data, stock, and rolling sales totals.
- Redis is the Celery broker and result backend.
- Railway Object Storage keeps private delivery-proof photos independently of application deploys.
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

Create PostgreSQL and Redis services, then create Web, Worker, and Beat services from this repository using the same Dockerfile:

- Web: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --worker-class gthread --threads 4 --timeout 60 --keep-alive 5`
- Worker: `celery -A config worker -l INFO`
- Beat: `celery -A config beat -l INFO`

Set the variables from `.env.example` on every service. Set `RUN_MIGRATIONS=1` only on the Railway Web service and `RUN_MIGRATIONS=0` on Worker and Beat. Local Compose uses a dedicated one-shot migration service before application containers start.

### Delivery proof storage

Create a private Railway Bucket named `s2u-delivery-proofs`, then inject its S3-compatible credentials into the Web, Worker, and Beat services using the `DELIVERY_BUCKET_*` variables in `.env.example`. Configure bucket CORS to allow `PUT` from the production application origin with the `Content-Type` header. The browser resizes delivery images and uploads them through short-lived signed URLs; the bucket remains private.

Objects are organized as `deliveries/YYYY/MM/DD/store-NUMBER/delivery-UUID/{invoice,boxes,damage,notes}/`. PostgreSQL stores searchable metadata, verification state, keywords, checksums, and the audit trail. A nightly task writes a compressed metadata catalog under `backups/delivery-metadata/`; administrators can also create and download one from the verification workspace. Keep Railway PostgreSQL backups enabled because metadata catalogs complement rather than replace database backups.
