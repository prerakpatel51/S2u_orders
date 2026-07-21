#!/usr/bin/env sh
set -e

case "${PROCESS_TYPE:-web}" in
  worker)
    python manage.py wait_for_redis
    exec celery -A config worker -l INFO \
      --queues short \
      --concurrency="${CELERY_CONCURRENCY:-2}" \
      --prefetch-multiplier=1
    ;;
  long-worker)
    python manage.py wait_for_redis
    exec celery -A config worker -l INFO \
      --queues long \
      --concurrency="${CELERY_LONG_CONCURRENCY:-1}" \
      --prefetch-multiplier=1
    ;;
  beat)
    python manage.py wait_for_redis
    exec celery -A config beat -l INFO
    ;;
  web)
    exec gunicorn config.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers 2 \
      --worker-class gthread \
      --threads 4 \
      --timeout 60 \
      --keep-alive 5
    ;;
  *)
    echo "Unknown PROCESS_TYPE: ${PROCESS_TYPE}" >&2
    exit 1
    ;;
esac
