import math
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .korona import KoronaClient
from .models import (
    FullSyncJob,
    Product,
    ProductCode,
    ProductMonthlyNeed,
    ProductStock,
    ReceiptSaleLine,
    SalesDailySummary,
    Store,
    SyncState,
)
from .services import MONTHLY_NEED_VERSION, STOCK_SYNC_ENTITY, _apply_receipt, month_start, reconcile_store_stocks
from .utils import normalize_search_text

PAGE_SIZE = 100
STAGES = [
    FullSyncJob.Stage.STORES,
    FullSyncJob.Stage.PRODUCTS,
    FullSyncJob.Stage.STOCKS,
    FullSyncJob.Stage.RECEIPTS,
    FullSyncJob.Stage.TOTALS,
]


def _progress(job, *, processed, total, current_batch, total_batches, complete=False):
    progress = dict(job.stage_progress)
    progress[job.stage] = {
        "processed": processed,
        "total": total,
        "current_batch": current_batch,
        "total_batches": total_batches,
        "status": "complete" if complete else "running",
    }
    job.processed = processed
    job.total = total
    job.current_batch = current_batch
    job.total_batches = total_batches
    job.stage_progress = progress
    job.heartbeat_at = timezone.now()


def _advance(job, stage):
    job.stage = stage
    job.processed = 0
    job.total = 0
    job.current_batch = 0
    job.total_batches = 0
    job.checkpoint = {}


@transaction.atomic
def initialize_full_sync(job):
    ProductStock.objects.all().delete()
    ReceiptSaleLine.objects.all().delete()
    SalesDailySummary.objects.all().delete()
    ProductMonthlyNeed.objects.all().delete()
    ProductCode.objects.all().delete()
    SyncState.objects.filter(
        entity__in=["stores", "products", "receipts", "receipts_live", "receipts_recent", STOCK_SYNC_ENTITY]
    ).delete()
    job.stage_progress = {}
    _advance(job, FullSyncJob.Stage.STORES)
    job.save()


def _upsert_store(data):
    Store.objects.update_or_create(
        korona_id=data["id"],
        defaults={
            "number": data.get("number") or "",
            "name": data.get("name") or data.get("number") or data["id"],
            "active": bool(data.get("active", True)),
            "is_warehouse": bool(data.get("warehouse")),
            "revision": data.get("revision") or 0,
            "raw_data": data,
            "last_synced_at": timezone.now(),
        },
    )


def _upsert_product(data):
    product, _ = Product.objects.update_or_create(
        korona_id=data["id"],
        defaults={
            "number": data.get("number") or "",
            "name": data.get("name") or data.get("number") or data["id"],
            "normalized_name": normalize_search_text(data.get("name")),
            "active": bool(data.get("active", True)) and not bool(data.get("deactivated")),
            "track_inventory": bool(data.get("trackInventory", True)),
            "revision": data.get("revision") or 0,
            "raw_data": data,
            "last_synced_at": timezone.now(),
        },
    )
    codes = []
    for code_data in data.get("codes") or []:
        code = str(code_data.get("productCode") or "").strip()
        if code:
            codes.append(
                ProductCode(
                    product=product,
                    code=code,
                    normalized_code=normalize_search_text(code),
                    container_size=code_data.get("containerSize"),
                    description=code_data.get("description") or "",
                )
            )
    ProductCode.objects.filter(product=product).delete()
    ProductCode.objects.bulk_create(codes, ignore_conflicts=True)


def _catalog_step(job, client, *, entity, suffix, upsert, next_stage):
    page = int(job.checkpoint.get("page", 1))
    payload = client.request(
        "GET",
        client.account_path(suffix),
        params={"page": page, "size": PAGE_SIZE, "includeDeleted": "true"},
    ) or {}
    rows = payload.get("results") or []
    for row in rows:
        upsert(row)

    total = max(int(payload.get("resultsTotal") or 0), len(rows))
    total_batches = max(int(payload.get("pagesTotal") or 0), 1 if total else 0)
    processed = min((page - 1) * PAGE_SIZE + len(rows), total) if total else len(rows)
    complete = not (payload.get("links") or {}).get("next")
    _progress(
        job,
        processed=processed,
        total=total,
        current_batch=page,
        total_batches=total_batches,
        complete=complete,
    )
    max_revision = max(
        int(job.checkpoint.get("max_revision", 0)),
        int(payload.get("maxRevision") or 0),
        max((int(row.get("revision") or 0) for row in rows), default=0),
    )
    if complete:
        SyncState.objects.update_or_create(
            entity=entity,
            store=None,
            defaults={"last_revision": max_revision, "last_synced_at": timezone.now()},
        )
        _advance(job, next_stage)
    else:
        job.checkpoint = {"page": page + 1, "max_revision": max_revision}


def _stock_step(job, client):
    last_id = int(job.checkpoint.get("last_store_id", 0))
    total = int(job.checkpoint.get("total", 0)) or Store.objects.filter(
        active=True, is_warehouse=True
    ).count()
    store = Store.objects.filter(active=True, is_warehouse=True, id__gt=last_id).order_by("id").first()
    if store:
        reconcile_store_stocks(store, client)

    processed = min(int(job.checkpoint.get("processed", 0)) + (1 if store else 0), total)
    complete = store is None or processed >= total
    _progress(
        job,
        processed=processed,
        total=total,
        current_batch=processed,
        total_batches=total,
        complete=complete,
    )
    if complete:
        _advance(job, FullSyncJob.Stage.RECEIPTS)
    else:
        job.checkpoint = {
            "last_store_id": store.id,
            "processed": processed,
            "total": total,
        }


def _receipt_step(job, client):
    revision = int(job.checkpoint.get("revision", 0))
    payload = client.request(
        "GET",
        client.account_path("receipts"),
        params={
            "revision": revision,
            "voidedItems": "true",
            "sort": "revision",
            "size": PAGE_SIZE,
            "page": 1,
        },
    ) or {}
    receipts = payload.get("results") or []
    affected = set()
    counts = {"seen": 0, "created": 0, "updated": 0}
    for receipt in receipts:
        counts["seen"] += 1
        _apply_receipt(receipt, affected, counts)

    initial_total = int(job.checkpoint.get("total", 0))
    total = initial_total or int(payload.get("resultsTotal") or len(receipts))
    processed = min(int(job.checkpoint.get("processed", 0)) + len(receipts), total) if total else 0
    current_batch = math.ceil(processed / PAGE_SIZE) if processed else 0
    total_batches = math.ceil(total / PAGE_SIZE) if total else 0
    complete = not receipts or len(receipts) < PAGE_SIZE or processed >= total
    _progress(
        job,
        processed=processed,
        total=total,
        current_batch=current_batch,
        total_batches=total_batches,
        complete=complete,
    )
    next_revision = max(
        revision,
        int(payload.get("maxRevision") or 0) if complete else 0,
        max((int(receipt.get("revision") or 0) for receipt in receipts), default=0),
    )
    if complete:
        now = timezone.now()
        SyncState.objects.update_or_create(
            entity="receipts", store=None, defaults={"last_revision": next_revision, "last_synced_at": now}
        )
        SyncState.objects.update_or_create(
            entity="receipts_live", store=None, defaults={"last_revision": next_revision, "last_synced_at": now}
        )
        _advance(job, FullSyncJob.Stage.TOTALS)
    else:
        job.checkpoint = {"revision": next_revision, "processed": processed, "total": total}


def rebuild_all_monthly_needs():
    today = timezone.localdate()
    start_30 = today - timedelta(days=29)
    now = timezone.now()
    rows = SalesDailySummary.objects.filter(sales_date__range=(start_30, today)).values(
        "store_id", "product_id"
    ).annotate(total=Sum("quantity_sold"))
    ProductMonthlyNeed.objects.filter(month=month_start()).delete()
    totals = [
        ProductMonthlyNeed(
            store_id=row["store_id"],
            product_id=row["product_id"],
            month=month_start(today),
            needed_quantity=max(row["total"], Decimal("0")),
            avg_daily_sales_30=max(row["total"], Decimal("0")) / Decimal("30"),
            avg_daily_sales_90=0,
            seasonal_quantity=0,
            confidence=1,
            calculation_version=MONTHLY_NEED_VERSION,
            last_calculated_at=now,
        )
        for row in rows
    ]
    ProductMonthlyNeed.objects.bulk_create(totals, batch_size=1000)
    return len(totals)


def run_full_sync_step(job):
    client = None
    if job.stage == FullSyncJob.Stage.INITIALIZE:
        initialize_full_sync(job)
    elif job.stage == FullSyncJob.Stage.STORES:
        client = KoronaClient()
        _catalog_step(
            job,
            client,
            entity="stores",
            suffix="organizationalUnits",
            upsert=_upsert_store,
            next_stage=FullSyncJob.Stage.PRODUCTS,
        )
    elif job.stage == FullSyncJob.Stage.PRODUCTS:
        client = KoronaClient()
        _catalog_step(
            job,
            client,
            entity="products",
            suffix="products",
            upsert=_upsert_product,
            next_stage=FullSyncJob.Stage.STOCKS,
        )
    elif job.stage == FullSyncJob.Stage.STOCKS:
        client = KoronaClient()
        _stock_step(job, client)
    elif job.stage == FullSyncJob.Stage.RECEIPTS:
        client = KoronaClient()
        _receipt_step(job, client)
    elif job.stage == FullSyncJob.Stage.TOTALS:
        total = rebuild_all_monthly_needs()
        _progress(job, processed=total, total=total, current_batch=1, total_batches=1, complete=True)
        _advance(job, FullSyncJob.Stage.COMPLETE)
        job.status = FullSyncJob.Status.SUCCESS
        job.finished_at = timezone.now()
        job.active_lock = None
    else:
        return False

    if job.stage != FullSyncJob.Stage.COMPLETE:
        job.status = FullSyncJob.Status.QUEUED
    job.step_number += 1
    job.heartbeat_at = timezone.now()
    job.save()
    return job.stage != FullSyncJob.Stage.COMPLETE
