import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .full_sync import run_full_sync_step
from .models import FullSyncJob, ServiceControl, SyncRun, SystemLog
from .services import reconcile_stocks, sync_products, sync_receipts, sync_stocks, sync_stores

logger = logging.getLogger(__name__)

SERVICES = {
    "stores": (sync_stores, 1800),
    "products": (sync_products, 900),
    "stocks": (sync_stocks, settings.KORONA_STOCK_INCREMENTAL_INTERVAL_SECONDS),
    "stock_reconciliation": (reconcile_stocks, settings.KORONA_STOCK_RECONCILE_INTERVAL_SECONDS),
    "receipts": (sync_receipts, 120),
}
STOCK_SERVICES = {"stocks", "stock_reconciliation"}


def run_controlled(service_name, force=False, ignore_interval=False):
    function, default_interval = SERVICES[service_name]
    control, _ = ServiceControl.objects.get_or_create(
        service_name=service_name, defaults={"interval_seconds": default_interval}
    )
    now = timezone.now()
    if FullSyncJob.objects.filter(active_lock="full-sync").exists():
        SyncRun.objects.create(job_name=service_name, status=SyncRun.Status.SKIPPED, finished_at=now)
        return {"skipped": True, "reason": "full reconciliation active"}
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
    if control.locked_until and control.locked_until > now:
        return {"skipped": True, "reason": "already running"}
    control.status = ServiceControl.Status.RUNNING
    control.locked_until = now + timedelta(minutes=15)
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
        run.save()
        control.status = ServiceControl.Status.IDLE if control.enabled else ServiceControl.Status.DISABLED
        control.last_run_at = finished
        control.next_run_at = (
            None
            if service_name == "stock_reconciliation"
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
    return run_controlled("receipts", force)


@shared_task(bind=True, name="orders.tasks.full_resync_step_task")
def full_resync_step_task(self, job_id, expected_step):
    with transaction.atomic():
        job = FullSyncJob.objects.select_for_update().get(pk=job_id)
        if (
            job.active_lock != "full-sync"
            or job.step_number != expected_step
            or job.status != FullSyncJob.Status.QUEUED
        ):
            return {"skipped": True, "reason": "stale or duplicate step"}
        job.status = FullSyncJob.Status.RUNNING
        job.started_at = job.started_at or timezone.now()
        job.heartbeat_at = timezone.now()
        job.celery_task_id = self.request.id or ""
        job.error_message = ""
        job.save()

    try:
        job.refresh_from_db()
        has_more = run_full_sync_step(job)
    except Exception as exc:
        logger.exception("Full KORONA reconciliation failed for job %s", job_id)
        FullSyncJob.objects.filter(pk=job_id).update(
            status=FullSyncJob.Status.ERROR,
            error_message=str(exc)[:4000],
            heartbeat_at=timezone.now(),
        )
        SystemLog.objects.create(
            level="ERROR",
            source="sync.full",
            message=str(exc),
            context={"job_id": job_id, "stage": job.stage},
        )
        raise

    if has_more:
        job.refresh_from_db()
        try:
            next_task = full_resync_step_task.delay(job.id, job.step_number)
            FullSyncJob.objects.filter(pk=job.id, step_number=job.step_number).update(
                celery_task_id=next_task.id
            )
        except Exception as exc:
            FullSyncJob.objects.filter(pk=job.id).update(
                status=FullSyncJob.Status.ERROR,
                error_message=f"Could not queue the next batch: {exc}"[:4000],
                heartbeat_at=timezone.now(),
            )
            raise
    return {"job_id": job_id, "complete": not has_more}
