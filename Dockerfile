FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput
RUN chmod +x /app/entrypoint.sh /app/start.sh
RUN groupadd --system appuser && useradd --system --gid appuser --home /app appuser \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "gthread", "--threads", "4", "--timeout", "60", "--keep-alive", "5"]
