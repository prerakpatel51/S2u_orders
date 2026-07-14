# S2U Orders

S2U Orders is a Django and Django REST Framework order-management application for KORONA.cloud. It provides store-specific order lists, product search and barcode workflows, stock transfers, supplier and commodity information, configurable AG Grid views, and PDF/XLSX exports.

## Architecture

- Django and Django REST Framework provide the web application and API.
- PostgreSQL stores users, order lists, cached KORONA data, stock, and rolling sales totals.
- Redis is the Celery broker and result backend.
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
- Run exactly one Celery Beat instance.
- Use TLS URLs in `CSRF_TRUSTED_ORIGINS` and for the public application endpoint.
- Restrict access to PostgreSQL and Redis to the application network.

## Data synchronization

KORONA stores and products are revision-synchronized. Receipt revisions update a compact receipt ledger by delta, and affected store/product rolling 30-day requirements are recalculated. Stock is cached locally and refreshed when products are selected as well as by background synchronization.

Order-list stock and monthly values have saved snapshots, but the API displays newer cache values when current records are available. User-entered shelf quantities, supplier quantities, transfers, and notes are not changed by synchronization jobs.

## Railway deployment

Create PostgreSQL and Redis services, then create Web, Worker, and Beat services from this repository using the same Dockerfile:

- Web: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --worker-class gthread --threads 4 --timeout 60 --keep-alive 5`
- Worker: `celery -A config worker -l INFO`
- Beat: `celery -A config beat -l INFO`

Set the variables from `.env.example` on every service. Set `RUN_MIGRATIONS=1` only on the Railway Web service and `RUN_MIGRATIONS=0` on Worker and Beat. Local Compose uses a dedicated one-shot migration service before application containers start.
