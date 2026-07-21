import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-local-dev-key")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "https://*.ngrok-free.app").split(",")
    if origin.strip()
]
IS_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT"))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "orders",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "orders.middleware.RequestLogMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "America/New_York")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = IS_RAILWAY and not DEBUG
SESSION_COOKIE_SECURE = IS_RAILWAY and not DEBUG
CSRF_COOKIE_SECURE = IS_RAILWAY and not DEBUG
SECURE_HSTS_SECONDS = 31536000 if IS_RAILWAY and not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = IS_RAILWAY and not DEBUG
SECURE_HSTS_PRELOAD = IS_RAILWAY and not DEBUG

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
# Task state is persisted in PostgreSQL by the application. No caller reads a
# Celery return value, so keeping a second copy in Redis only adds keys and RDB
# writes to the broker.
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_DEFAULT_QUEUE = "short"
CELERY_TASK_ROUTES = {
    "orders.tasks.reconcile_stocks_task": {"queue": "long"},
    "orders.tasks.reconcile_monthly_totals_task": {"queue": "long"},
    "orders.tasks.backup_delivery_metadata_task": {"queue": "long"},
    "orders.tasks.build_delivery_recovery_export_task": {"queue": "long"},
}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = 3 * 60 * 60
CELERY_TASK_TIME_LIMIT = 3 * 60 * 60 + 5 * 60
CELERY_BROKER_TRANSPORT_OPTIONS = {
    # Long reconciliation tasks must not be delivered to a second worker while
    # the original worker is still processing them.
    "visibility_timeout": 4 * 60 * 60,
}
CELERY_BROKER_CONNECTION_TIMEOUT = int(os.getenv("CELERY_BROKER_CONNECTION_TIMEOUT", "5"))
RUNTIME_HEARTBEAT_STALE_SECONDS = max(
    60, int(os.getenv("RUNTIME_HEARTBEAT_STALE_SECONDS", "180"))
)
KORONA_STOCK_RECONCILE_HOUR = min(23, max(0, int(os.getenv("KORONA_STOCK_RECONCILE_HOUR", "3"))))
KORONA_STOCK_RECONCILE_MINUTE = min(59, max(0, int(os.getenv("KORONA_STOCK_RECONCILE_MINUTE", "15"))))
KORONA_MONTHLY_RECONCILE_HOUR = min(23, max(0, int(os.getenv("KORONA_MONTHLY_RECONCILE_HOUR", "4"))))
KORONA_MONTHLY_RECONCILE_MINUTE = min(59, max(0, int(os.getenv("KORONA_MONTHLY_RECONCILE_MINUTE", "15"))))
KORONA_STOCK_INCREMENTAL_INTERVAL_SECONDS = max(
    30, int(os.getenv("KORONA_STOCK_INCREMENTAL_INTERVAL_SECONDS", "120"))
)
CELERY_BEAT_SCHEDULE = {
    "sync-korona-receipts": {
        "task": "orders.tasks.sync_receipts_task",
        "schedule": 120.0,
        "options": {"expires": 110},
    },
    "sync-korona-products": {
        "task": "orders.tasks.sync_products_task",
        "schedule": 900.0,
        "options": {"expires": 840},
    },
    "sync-korona-stores": {
        "task": "orders.tasks.sync_stores_task",
        "schedule": 1800.0,
        "options": {"expires": 1740},
    },
    "sync-korona-stocks": {
        "task": "orders.tasks.sync_stocks_task",
        "schedule": KORONA_STOCK_INCREMENTAL_INTERVAL_SECONDS,
        "options": {"expires": max(30, KORONA_STOCK_INCREMENTAL_INTERVAL_SECONDS - 10)},
    },
    "runtime-heartbeat": {
        "task": "orders.tasks.runtime_heartbeat_task",
        "schedule": 60.0,
        "options": {"expires": 50},
    },
    "reconcile-korona-stocks-nightly": {
        "task": "orders.tasks.reconcile_stocks_task",
        "schedule": crontab(
            hour=KORONA_STOCK_RECONCILE_HOUR,
            minute=KORONA_STOCK_RECONCILE_MINUTE,
        ),
    },
    "reconcile-monthly-totals-nightly": {
        "task": "orders.tasks.reconcile_monthly_totals_task",
        "schedule": crontab(
            hour=KORONA_MONTHLY_RECONCILE_HOUR,
            minute=KORONA_MONTHLY_RECONCILE_MINUTE,
        ),
    },
    "backup-delivery-metadata-nightly": {
        "task": "orders.tasks.backup_delivery_metadata_task",
        "schedule": crontab(hour=2, minute=30),
    },
    "reconcile-delivery-replicas": {
        "task": "orders.tasks.reconcile_delivery_replicas_task",
        "schedule": crontab(minute="*/15"),
    },
    "cleanup-abandoned-delivery-uploads": {
        "task": "orders.tasks.cleanup_abandoned_delivery_uploads_task",
        "schedule": crontab(minute=15),
    },
    "cleanup-expired-delivery-exports": {
        "task": "orders.tasks.cleanup_expired_delivery_exports_task",
        "schedule": crontab(hour=3, minute=10),
    },
}

KORONA_ACCOUNT_ID = os.getenv("KORONA_ACCOUNT_ID", "")
KORONA_BASE_URL = os.getenv("KORONA_BASE_URL", "").rstrip("/")
KORONA_USER = os.getenv("KORONA_USER", "")
KORONA_PASSWORD = os.getenv("KORONA_PASSWORD", "")
KORONA_CONNECT_TIMEOUT_SECONDS = float(os.getenv("KORONA_CONNECT_TIMEOUT_SECONDS", "5"))
KORONA_READ_TIMEOUT_SECONDS = float(os.getenv("KORONA_READ_TIMEOUT_SECONDS", "45"))
KORONA_HTTP_RETRIES = max(0, int(os.getenv("KORONA_HTTP_RETRIES", "3")))
KORONA_HTTP_BACKOFF_SECONDS = max(0.0, float(os.getenv("KORONA_HTTP_BACKOFF_SECONDS", "0.5")))
KORONA_STOCK_PAGE_SIZE = max(100, min(1000, int(os.getenv("KORONA_STOCK_PAGE_SIZE", "1000"))))
KORONA_STOCK_RECONCILE_INTERVAL_SECONDS = max(
    3600, int(os.getenv("KORONA_STOCK_RECONCILE_INTERVAL_SECONDS", "86400"))
)

# Railway's private, S3-compatible object storage bucket for delivery proof.
DELIVERY_BUCKET_ENDPOINT = os.getenv("DELIVERY_BUCKET_ENDPOINT", os.getenv("BUCKET_ENDPOINT", ""))
DELIVERY_BUCKET_NAME = os.getenv("DELIVERY_BUCKET_NAME", os.getenv("BUCKET_NAME", ""))
DELIVERY_BUCKET_ACCESS_KEY_ID = os.getenv(
    "DELIVERY_BUCKET_ACCESS_KEY_ID", os.getenv("BUCKET_ACCESS_KEY_ID", "")
)
DELIVERY_BUCKET_SECRET_ACCESS_KEY = os.getenv(
    "DELIVERY_BUCKET_SECRET_ACCESS_KEY", os.getenv("BUCKET_SECRET_ACCESS_KEY", "")
)
DELIVERY_BUCKET_REGION = os.getenv("DELIVERY_BUCKET_REGION", "auto")
# Independent Railway bucket used only for disaster recovery. Delivery objects
# keep exactly the same key in both buckets so a restore never needs path
# translation.
DELIVERY_DR_BUCKET_ENDPOINT = os.getenv("DELIVERY_DR_BUCKET_ENDPOINT", "")
DELIVERY_DR_BUCKET_NAME = os.getenv("DELIVERY_DR_BUCKET_NAME", "")
DELIVERY_DR_BUCKET_ACCESS_KEY_ID = os.getenv("DELIVERY_DR_BUCKET_ACCESS_KEY_ID", "")
DELIVERY_DR_BUCKET_SECRET_ACCESS_KEY = os.getenv("DELIVERY_DR_BUCKET_SECRET_ACCESS_KEY", "")
DELIVERY_DR_BUCKET_REGION = os.getenv("DELIVERY_DR_BUCKET_REGION", "auto")
DELIVERY_UPLOAD_MAX_BYTES = max(1, int(os.getenv("DELIVERY_UPLOAD_MAX_MB", "12"))) * 1024 * 1024
DELIVERY_SIGNED_URL_SECONDS = min(
    3600, max(60, int(os.getenv("DELIVERY_SIGNED_URL_SECONDS", "600")))
)
DELIVERY_EXPORT_RETENTION_DAYS = max(
    1, min(30, int(os.getenv("DELIVERY_EXPORT_RETENTION_DAYS", "7")))
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
