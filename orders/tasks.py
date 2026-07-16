import logging
from datetime import timedelta

from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .delivery_storage import (
    create_metadata_backup,
    delete_object,
    delete_dr_object,
    dr_is_configured,
    is_configured,
    build_recovery_export,
    replicate_asset,
    verify_asset_replica,
)
from .models import (
    DeliveryAsset,
    DeliveryAssetReplica,
    DeliveryBackup,
    DeliveryRecoveryExport,
    ServiceControl,
    SyncRun,
    SystemLog,
)
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


@shared_task(name="orders.tasks.backup_delivery_metadata_task")
def backup_delivery_metadata_task():
    """Create a daily searchable metadata catalog in independent DR storage."""
    if not dr_is_configured():
        return {"skipped": True, "reason": "delivery DR bucket is not configured"}
    backup = DeliveryBackup.objects.create()
    try:
        key, delivery_count, asset_count, size_bytes = create_metadata_backup(backup)
        backup.object_key = key
        backup.delivery_count = delivery_count
        backup.asset_count = asset_count
        backup.size_bytes = size_bytes
        backup.status = DeliveryBackup.Status.COMPLETE
        backup.save()
        return {
            "backup_uuid": str(backup.uuid),
            "deliveries": delivery_count,
            "assets": asset_count,
            "size_bytes": size_bytes,
        }
    except Exception as exc:
        logger.exception("Delivery metadata backup failed")
        backup.status = DeliveryBackup.Status.FAILED
        backup.error_message = str(exc)[:2000]
        backup.save(update_fields=["status", "error_message", "updated_at"])
        SystemLog.objects.create(
            level="ERROR", source="delivery.backup", message=backup.error_message
        )
        raise


@shared_task(bind=True, name="orders.tasks.replicate_delivery_asset_task", max_retries=5)
def replicate_delivery_asset_task(self, asset_id):
    """Copy a confirmed immutable asset to DR and retry transient failures."""
    try:
        asset = DeliveryAsset.objects.select_related("delivery").get(
            pk=asset_id, upload_status=DeliveryAsset.UploadStatus.UPLOADED
        )
    except DeliveryAsset.DoesNotExist:
        return {"skipped": True, "reason": "uploaded asset no longer exists"}
    try:
        replica = replicate_asset(asset)
        return {
            "asset_uuid": str(asset.uuid),
            "status": replica.status,
            "size_bytes": replica.size_bytes,
            "checksum_sha256": replica.checksum_sha256,
        }
    except Exception as exc:
        logger.exception("Delivery asset replication failed for %s", asset.uuid)
        countdown = min(3600, 30 * (2 ** self.request.retries))
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(name="orders.tasks.verify_delivery_replica_task")
def verify_delivery_replica_task(replica_id):
    try:
        replica = DeliveryAssetReplica.objects.get(pk=replica_id)
    except DeliveryAssetReplica.DoesNotExist:
        return {"skipped": True, "reason": "replica record no longer exists"}
    checked = verify_asset_replica(replica)
    return {"asset_uuid": str(checked.asset.uuid), "status": checked.status}


@shared_task(name="orders.tasks.reconcile_delivery_replicas_task")
def reconcile_delivery_replicas_task():
    """Catch missed jobs and periodically prove that recovery copies still exist."""
    if not is_configured() or not dr_is_configured():
        return {"skipped": True, "reason": "both delivery buckets must be configured"}
    copying_cutoff = timezone.now() - timedelta(minutes=30)
    unsynced = list(
        DeliveryAsset.objects.filter(upload_status=DeliveryAsset.UploadStatus.UPLOADED)
        .filter(
            Q(replica__isnull=True)
            | Q(
                replica__status=DeliveryAssetReplica.Status.COPYING,
                replica__updated_at__lt=copying_cutoff,
            )
            | Q(
                replica__status__in=[
                    DeliveryAssetReplica.Status.PENDING,
                    DeliveryAssetReplica.Status.FAILED,
                    DeliveryAssetReplica.Status.MISSING,
                ]
            )
        )
        .values_list("pk", flat=True)[:200]
    )
    for asset_id in unsynced:
        replicate_delivery_asset_task.delay(asset_id)

    verify_before = timezone.now() - timedelta(days=7)
    stale = list(
        DeliveryAssetReplica.objects.filter(status=DeliveryAssetReplica.Status.VERIFIED)
        .filter(Q(verified_at__lt=verify_before) | Q(verified_at__isnull=True))
        .values_list("pk", flat=True)[:200]
    )
    for replica_id in stale:
        verify_delivery_replica_task.delay(replica_id)
    return {"replication_queued": len(unsynced), "verification_queued": len(stale)}


@shared_task(name="orders.tasks.build_delivery_recovery_export_task")
def build_delivery_recovery_export_task(export_id):
    try:
        export = DeliveryRecoveryExport.objects.get(pk=export_id)
    except DeliveryRecoveryExport.DoesNotExist:
        return {"skipped": True, "reason": "recovery export no longer exists"}
    export.status = DeliveryRecoveryExport.Status.RUNNING
    export.error_message = ""
    export.save(update_fields=["status", "error_message", "updated_at"])
    try:
        key, deliveries, files, size, checksum = build_recovery_export(export)
        export.object_key = key
        export.delivery_count = deliveries
        export.file_count = files
        export.size_bytes = size
        export.checksum_sha256 = checksum
        export.expires_at = timezone.now() + timedelta(
            days=settings.DELIVERY_EXPORT_RETENTION_DAYS
        )
        export.status = DeliveryRecoveryExport.Status.COMPLETE
        export.save()
        return {
            "export_uuid": str(export.uuid),
            "deliveries": deliveries,
            "files": files,
            "size_bytes": size,
            "checksum_sha256": checksum,
        }
    except Exception as exc:
        logger.exception("Delivery recovery export failed for %s", export.uuid)
        export.status = DeliveryRecoveryExport.Status.FAILED
        export.error_message = str(exc)[:2000]
        export.save(update_fields=["status", "error_message", "updated_at"])
        return {"export_uuid": str(export.uuid), "failed": True, "error": export.error_message}


@shared_task(name="orders.tasks.cleanup_expired_delivery_exports_task")
def cleanup_expired_delivery_exports_task():
    expired = list(
        DeliveryRecoveryExport.objects.filter(
            status=DeliveryRecoveryExport.Status.COMPLETE,
            expires_at__lte=timezone.now(),
        )[:100]
    )
    cleaned = 0
    for export in expired:
        try:
            if export.object_key and dr_is_configured():
                delete_dr_object(export.object_key)
            export.status = DeliveryRecoveryExport.Status.EXPIRED
            export.object_key = ""
            export.save(update_fields=["status", "object_key", "updated_at"])
            cleaned += 1
        except Exception:
            logger.exception("Could not expire delivery recovery export %s", export.uuid)
    return {"seen": len(expired), "cleaned": cleaned}


@shared_task(name="orders.tasks.cleanup_abandoned_delivery_uploads_task")
def cleanup_abandoned_delivery_uploads_task():
    """Remove failed and never-confirmed uploads after clients have had time to retry."""
    cutoff = timezone.now() - timedelta(hours=24)
    abandoned = list(
        DeliveryAsset.objects.filter(
            upload_status__in=[
                DeliveryAsset.UploadStatus.PENDING,
                DeliveryAsset.UploadStatus.FAILED,
            ],
            updated_at__lt=cutoff,
        )[:500]
    )
    deleted = 0
    for asset in abandoned:
        if is_configured():
            try:
                delete_object(asset.object_key)
            except Exception:
                logger.warning("Could not remove abandoned delivery object %s", asset.object_key)
                continue
        asset.delete()
        deleted += 1
    return {"seen": len(abandoned), "deleted": deleted}
