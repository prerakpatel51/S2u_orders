from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Avg, Count, Max
from django.utils import timezone
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from redis import Redis
from redis.exceptions import RedisError

from .models import ApiRequestLog, TaskExecutionMetric

METRICS_WINDOW = timedelta(hours=24)


def _gauge(registry, name, documentation, labels=()):
    return Gauge(name, documentation, labelnames=labels, registry=registry)


def _status(value):
    return str(value) if value is not None else "network_error"


def _queues():
    queues = {settings.CELERY_TASK_DEFAULT_QUEUE}
    queues.update(route["queue"] for route in settings.CELERY_TASK_ROUTES.values())
    return sorted(queues)


def render_metrics():
    """Build replica-safe metrics from shared Postgres history and live Redis."""
    registry = CollectorRegistry()
    collection_ok = _gauge(
        registry,
        "s2u_metrics_collection_success",
        "Whether a metrics source was collected successfully.",
        ("source",),
    )
    _gauge(
        registry,
        "s2u_metrics_window_seconds",
        "Rolling aggregation window used by application metrics.",
    ).set(METRICS_WINDOW.total_seconds())
    since = timezone.now() - METRICS_WINDOW

    http_requests = _gauge(
        registry,
        "s2u_http_requests_24h",
        "Django requests observed in the rolling 24-hour window.",
        ("method", "route", "status"),
    )
    http_latency_avg = _gauge(
        registry,
        "s2u_http_request_duration_seconds_avg_24h",
        "Average Django request duration in the rolling 24-hour window.",
        ("method", "route", "status"),
    )
    http_latency_max = _gauge(
        registry,
        "s2u_http_request_duration_seconds_max_24h",
        "Maximum Django request duration in the rolling 24-hour window.",
        ("method", "route", "status"),
    )
    try:
        rows = (
            ApiRequestLog.objects.filter(service="django", created_at__gte=since)
            .values("method", "url_path", "status_code")
            .annotate(requests=Count("id"), average_ms=Avg("latency_ms"), maximum_ms=Max("latency_ms"))
        )
        for row in rows:
            labels = (row["method"], row["url_path"], _status(row["status_code"]))
            http_requests.labels(*labels).set(row["requests"])
            http_latency_avg.labels(*labels).set((row["average_ms"] or 0) / 1000)
            http_latency_max.labels(*labels).set((row["maximum_ms"] or 0) / 1000)
        collection_ok.labels("django_requests").set(1)
    except DatabaseError:
        collection_ok.labels("django_requests").set(0)

    task_runs = _gauge(
        registry,
        "s2u_celery_task_runs_24h",
        "Celery task runs in the rolling 24-hour window.",
        ("task", "status"),
    )
    task_duration_avg = _gauge(
        registry,
        "s2u_celery_task_duration_seconds_avg_24h",
        "Average Celery task duration in the rolling 24-hour window.",
        ("task", "status"),
    )
    task_duration_max = _gauge(
        registry,
        "s2u_celery_task_duration_seconds_max_24h",
        "Maximum Celery task duration in the rolling 24-hour window.",
        ("task", "status"),
    )
    task_failures = _gauge(
        registry,
        "s2u_celery_task_failures_24h",
        "Failed or revoked Celery task runs in the rolling 24-hour window.",
        ("task",),
    )
    try:
        failures = {}
        rows = (
            TaskExecutionMetric.objects.filter(created_at__gte=since)
            .values("task_name", "status")
            .annotate(runs=Count("id"), average_ms=Avg("duration_ms"), maximum_ms=Max("duration_ms"))
        )
        for row in rows:
            labels = (row["task_name"], row["status"])
            task_runs.labels(*labels).set(row["runs"])
            task_duration_avg.labels(*labels).set((row["average_ms"] or 0) / 1000)
            task_duration_max.labels(*labels).set((row["maximum_ms"] or 0) / 1000)
            if row["status"] in {"FAILURE", "REVOKED"}:
                failures[row["task_name"]] = failures.get(row["task_name"], 0) + row["runs"]
        for task_name, count in failures.items():
            task_failures.labels(task_name).set(count)
        collection_ok.labels("celery_tasks").set(1)
    except DatabaseError:
        collection_ok.labels("celery_tasks").set(0)

    korona_requests = _gauge(
        registry,
        "s2u_korona_requests_24h",
        "KORONA requests observed in the rolling 24-hour window.",
        ("method", "status"),
    )
    korona_errors = _gauge(
        registry,
        "s2u_korona_errors_24h",
        "KORONA HTTP and network errors in the rolling 24-hour window.",
        ("method", "status"),
    )
    try:
        rows = (
            ApiRequestLog.objects.filter(service="korona", created_at__gte=since)
            .values("method", "status_code")
            .annotate(requests=Count("id"))
        )
        for row in rows:
            status = _status(row["status_code"])
            korona_requests.labels(row["method"], status).set(row["requests"])
            if row["status_code"] is None or row["status_code"] >= 400:
                korona_errors.labels(row["method"], status).set(row["requests"])
        collection_ok.labels("korona_requests").set(1)
    except DatabaseError:
        collection_ok.labels("korona_requests").set(0)

    queue_depth = _gauge(
        registry,
        "s2u_celery_queue_depth",
        "Ready Celery messages currently waiting in a Redis queue.",
        ("queue",),
    )
    unacknowledged = _gauge(
        registry,
        "s2u_celery_unacknowledged_tasks",
        "Celery messages reserved by workers but not yet acknowledged.",
    )
    try:
        redis_client = Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        for queue in _queues():
            queue_depth.labels(queue).set(redis_client.llen(queue))
        unacknowledged.set(redis_client.zcard("unacked_index"))
        collection_ok.labels("celery_queues").set(1)
    except RedisError:
        collection_ok.labels("celery_queues").set(0)

    return generate_latest(registry)
