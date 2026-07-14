web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --worker-class gthread --threads 4 --timeout 60 --keep-alive 5
worker: celery -A config worker -l INFO --concurrency=2
beat: celery -A config beat -l INFO
