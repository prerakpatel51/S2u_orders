import logging
from datetime import timedelta

from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import ServiceControl, SyncRun, SystemLog
from .services import reconcile_monthly_needs, reconcile_stocks, sync_products, sync_receipts, sync_stocks, sync_stores

logger = logging.getLogger(__name__)

SERVICES = {
    "stores": (sync_stores, 1800),
    "products": (sync_products, 900),
    "stocks": (sync_stocks, settings.KORONA_STOCK_INCREMENTAL_INTERVAL_SECONDS),
    "stock_reconciliation": (reconcile_stocks, settings.KORONA_STOCK_RECONCILE_INTERVAL_SECONDS),
    "receipts": (sync_receipts, 120),
    "monthly_reconciliation": (reconcile_monthly_needs, 86400),
}
STOCK_SERVICES = {"stocks", "stock_reconciliation"}
MONTHLY_SERVICES = {"receipts", "monthly_reconciliation"}
MONTHLY_CONFLICT_REASON = "another monthly calculation is running"
SERVICE_LOCK_TIMEOUTS = {
    "stock_reconciliation": timedelta(hours=2),
    "monthly_reconciliation": timedelta(hours=4),
}
INTERRUPTED_MESSAGE = "Worker stopped before this job completed. Run it again when ready."


def resolve_worker_interruptions(service_name, resolved_at):
    """Mark restart errors historical after the affected service succeeds."""
    resolved = []
    for row in SystemLog.objects.filter(source="sync.worker", message=INTERRUPTED_MESSAGE):
        context = dict(row.context or {})
        if service_name not in context.get("services", []) or context.get("resolved_at"):
            continue
        context["resolved_at"] = resolved_at.isoformat()
        context["resolved_by"] = service_name
        row.context = context
        resolved.append(row)
    if resolved:
        SystemLog.objects.bulk_update(resolved, ["context"])
    return len(resolved)


def recover_interrupted_runs(*, expired_only=True):
    """Turn abandoned database locks into visible failed runs."""
    now = timezone.now()
    controls = ServiceControl.objects.filter(status=ServiceControl.Status.RUNNING)
    if expired_only:
        controls = controls.filter(locked_until__lte=now)
    names = list(controls.values_list("service_name", flat=True))
    if not names:
        return 0
    interrupted_runs = list(
        SyncRun.objects.filter(job_name__in=names, status=SyncRun.Status.RUNNING)
    )
    for run in interrupted_runs:
        run.status = SyncRun.Status.ERROR
        run.finished_at = now
        run.duration_ms = max(0, int((now - run.started_at).total_seconds() * 1000))
        run.error_message = INTERRUPTED_MESSAGE
        run.updated_at = now
    SyncRun.objects.bulk_update(
        interrupted_runs,
        ["status", "finished_at", "duration_ms", "error_message", "updated_at"],
    )
    controls.update(
        status=ServiceControl.Status.ERROR,
        locked_until=None,
        last_error=INTERRUPTED_MESSAGE,
    )
    SystemLog.objects.create(
        level="ERROR",
        source="sync.worker",
        message=INTERRUPTED_MESSAGE,
        context={"services": names},
    )
    return len(names)


@worker_ready.connect
def recover_runs_after_worker_restart(**_kwargs):
    # A task from the previous worker process cannot still be executing here.
    recover_interrupted_runs(expired_only=False)


def run_controlled(service_name, force=False, ignore_interval=False):
    function, default_interval = SERVICES[service_name]
    control, _ = ServiceControl.objects.get_or_create(
        service_name=service_name, defaults={"interval_seconds": default_interval}
    )
    now = timezone.now()
    recover_interrupted_runs(expired_only=True)
    if not force and not control.enabled:
        control.status = ServiceControl.Status.DISABLED
        control.save(update_fields=["status", "updated_at"])
        SyncRun.objects.create(job_name=service_name, status=SyncRun.Status.SKIPPED, finished_at=now)
        return {"skipped": True, "reason": "disabled"}
    if not force and not ignore_interval and control.next_run_at and control.next_run_at > now:
        return {"skipped": True, "reason": "not due"}
    if service_name in STOCK_SERVICES and ServiceControl.objects.filter(
        service_name__in=STOCK_SERVICES,
        locked_until__gt=now,
    ).exclude(pk=control.pk).exists():
        SyncRun.objects.create(job_name=service_name, status=SyncRun.Status.SKIPPED, finished_at=now)
        return {"skipped": True, "reason": "another stock synchronization is running"}
    if service_name in MONTHLY_SERVICES and ServiceControl.objects.filter(
        service_name__in=MONTHLY_SERVICES,
    ).exclude(pk=control.pk).filter(
        Q(locked_until__gt=now) | Q(status=ServiceControl.Status.QUEUED)
    ).exists():
        SyncRun.objects.create(job_name=service_name, status=SyncRun.Status.SKIPPED, finished_at=now)
        return {"skipped": True, "reason": MONTHLY_CONFLICT_REASON}
    if control.locked_until and control.locked_until > now:
        return {"skipped": True, "reason": "already running"}
    control.status = ServiceControl.Status.RUNNING
    control.locked_until = now + SERVICE_LOCK_TIMEOUTS.get(service_name, timedelta(minutes=15))
    control.last_error = ""
    control.save(update_fields=["status", "locked_until", "last_error", "updated_at"])
    run = SyncRun.objects.create(job_name=service_name)
    started = timezone.now()
    try:
        counts = function()
        finished = timezone.now()
        run.status = SyncRun.Status.SUCCESS
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.records_seen = counts.get("seen", 0)
        run.records_created = counts.get("created", 0)
        run.records_updated = counts.get("updated", 0)
        run.metrics = counts
        run.save()
        resolve_worker_interruptions(service_name, finished)
        control.status = ServiceControl.Status.IDLE if control.enabled else ServiceControl.Status.DISABLED
        control.last_run_at = finished
        control.next_run_at = (
            None
            if service_name in {"stock_reconciliation", "monthly_reconciliation"}
            else finished + timedelta(seconds=control.interval_seconds)
        )
        return counts
    except Exception as exc:
        logger.exception("Sync service %s failed", service_name)
        finished = timezone.now()
        run.status = SyncRun.Status.ERROR
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.error_message = str(exc)[:4000]
        run.save()
        control.status = ServiceControl.Status.ERROR
        control.last_error = str(exc)[:4000]
        SystemLog.objects.create(level="ERROR", source=f"sync.{service_name}", message=str(exc))
        raise
    finally:
        control.locked_until = None
        control.save()


@shared_task(name="orders.tasks.sync_stores_task")
def sync_stores_task(force=False):
    return run_controlled("stores", force)


@shared_task(name="orders.tasks.sync_products_task")
def sync_products_task(force=False):
    return run_controlled("products", force)


@shared_task(name="orders.tasks.sync_stocks_task")
def sync_stocks_task(force=False):
    return run_controlled("stocks", force)


@shared_task(name="orders.tasks.reconcile_stocks_task")
def reconcile_stocks_task(force=False):
    return run_controlled("stock_reconciliation", force, ignore_interval=True)


@shared_task(name="orders.tasks.sync_receipts_task")
def sync_receipts_task(force=False):
    try:
        return run_controlled("receipts", force)
    finally:
        dispatch_waiting_monthly_reconciliation()


@shared_task(name="orders.tasks.reconcile_monthly_totals_task")
def reconcile_monthly_totals_task(force=False):
    result = run_controlled("monthly_reconciliation", force, ignore_interval=True)
    control, _ = ServiceControl.objects.get_or_create(
        service_name="monthly_reconciliation",
        defaults={"interval_seconds": SERVICES["monthly_reconciliation"][1]},
    )
    if result.get("reason") == MONTHLY_CONFLICT_REASON:
        control.status = ServiceControl.Status.QUEUED
        control.last_error = ""
        control.save(update_fields=["status", "last_error", "updated_at"])
        return {**result, "queued": True}
    if result.get("skipped") and control.status == ServiceControl.Status.QUEUED:
        control.status = (
            ServiceControl.Status.IDLE if control.enabled else ServiceControl.Status.DISABLED
        )
        control.locked_until = None
        control.last_error = ""
        control.save(update_fields=["status", "locked_until", "last_error", "updated_at"])
    return result


def dispatch_waiting_monthly_reconciliation():
    """Hand a queued reconciliation the worker slot released by receipt sync."""
    control = ServiceControl.objects.filter(
        service_name="monthly_reconciliation",
        status=ServiceControl.Status.QUEUED,
    ).first()
    if control is None:
        return False
    if ServiceControl.objects.filter(
        service_name="receipts", locked_until__gt=timezone.now()
    ).exists():
        return False
    try:
        reconcile_monthly_totals_task.delay(force=True)
    except Exception as exc:
        control.status = ServiceControl.Status.ERROR
        control.last_error = f"Could not retry queued reconciliation: {exc}"[:4000]
        control.save(update_fields=["status", "last_error", "updated_at"])
        SystemLog.objects.create(
            level="ERROR",
            source="sync.monthly_reconciliation",
            message=control.last_error,
        )
        return False
    return True
