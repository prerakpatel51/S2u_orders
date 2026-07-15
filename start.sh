#!/usr/bin/env sh
set -e

case "${PROCESS_TYPE:-web}" in
  worker)
    exec celery -A config worker -l INFO --concurrency="${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    exec celery -A config beat -l INFO
    ;;
  web)
    python manage.py migrate --noinput
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
