import logging
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .korona import KoronaClient, KoronaError
from .models import (
    DeferredReceipt,
    Product,
    ProductCode,
    ProductMonthlyNeed,
    ProductStock,
    ReceiptSaleLine,
    SalesDailySummary,
    Store,
    SyncState,
)
from .utils import decimal_value, normalize_search_text, reference_id

logger = logging.getLogger(__name__)
def month_start(day=None):
    day = day or timezone.localdate()
    return day.replace(day=1)


def get_sync_state(entity):
    state, _ = SyncState.objects.get_or_create(entity=entity, store=None)
    return state


def save_sync_state(state, revision):
    """Advance a cursor without allowing an older worker to move it backwards."""
    with transaction.atomic():
        locked = SyncState.objects.select_for_update().get(pk=state.pk)
        locked.last_revision = max(locked.last_revision, int(revision or 0))
        locked.last_synced_at = timezone.now()
        locked.save(update_fields=["last_revision", "last_synced_at", "updated_at"])
    state.last_revision = locked.last_revision
    state.last_synced_at = locked.last_synced_at
    return state


def upsert_store(data):
    with transaction.atomic():
        store = Store.objects.select_for_update().filter(korona_id=data["id"]).first()
        incoming_revision = int(data.get("revision") or 0)
        if store and incoming_revision < store.revision:
            return store, False
        defaults = {
            "number": data.get("number") or "",
            "name": data.get("name") or data.get("number") or data["id"],
            "active": bool(data.get("active", True)),
            "is_warehouse": bool(data.get("warehouse")),
            "revision": incoming_revision,
            "raw_data": data,
            "last_synced_at": timezone.now(),
        }
        if store:
            for field, value in defaults.items():
                setattr(store, field, value)
            store.save(update_fields=[*defaults, "updated_at"])
            created = False
        else:
            store = Store.objects.create(korona_id=data["id"], **defaults)
            created = True
        if not store.active or not store.is_warehouse:
            ProductStock.objects.filter(store=store).delete()
            SyncState.objects.filter(entity=STOCK_SYNC_ENTITY, store=store).delete()
    return store, created


def sync_stores():
    client = KoronaClient()
    state = get_sync_state("stores")
    counts = {"seen": 0, "created": 0, "updated": 0}
    max_revision = state.last_revision
    for rows, page_revision in client.paginated(
        "organizationalUnits", {"revision": state.last_revision, "includeDeleted": "true"}
    ):
        for data in rows:
            counts["seen"] += 1
            _, created = upsert_store(data)
            counts["created" if created else "updated"] += 1
        max_revision = page_revision or max_revision
    save_sync_state(state, max_revision)
    return counts


def sync_products():
    client = KoronaClient()
    state = get_sync_state("products")
    counts = {"seen": 0, "created": 0, "updated": 0}
    max_revision = state.last_revision
    for rows, page_revision in client.paginated(
        "products", {"revision": state.last_revision, "includeDeleted": "true"}
    ):
        for data in rows:
            counts["seen"] += 1
            product = Product.objects.select_for_update().filter(korona_id=data["id"]).first()
            incoming_revision = int(data.get("revision") or 0)
            if product and incoming_revision < product.revision:
                continue
            defaults = {
                "number": data.get("number") or "",
                "name": data.get("name") or data.get("number") or data["id"],
                "normalized_name": normalize_search_text(data.get("name")),
                "active": bool(data.get("active", True)) and not bool(data.get("deactivated")),
                "track_inventory": bool(data.get("trackInventory", True)),
                "revision": incoming_revision,
                "raw_data": data,
                "last_synced_at": timezone.now(),
            }
            if product:
                for field, value in defaults.items():
                    setattr(product, field, value)
                product.save(update_fields=[*defaults, "updated_at"])
                created = False
            else:
                product = Product.objects.create(korona_id=data["id"], **defaults)
                created = True
            codes = []
            for code_data in data.get("codes") or []:
                code = str(code_data.get("productCode") or "").strip()
                if not code:
                    continue
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
            counts["created" if created else "updated"] += 1
        max_revision = page_revision or max_revision
    save_sync_state(state, max_revision)
    if counts["seen"]:
        replay_deferred_receipts()
    return counts


def refresh_product_stocks(product, client=None):
    client = client or KoronaClient()
    counts = {"seen": 0, "created": 0, "updated": 0}
    store_by_korona = {
        str(s.korona_id): s for s in Store.objects.filter(active=True, is_warehouse=True)
    }
    now = timezone.now()
    for data in client.product_stocks(product.korona_id):
        store = store_by_korona.get(str(reference_id(data.get("warehouse"))))
        if not store:
            continue
        amount = data.get("amount") or {}
        _, created = ProductStock.objects.update_or_create(
            product=product,
            store=store,
            defaults={
                "actual": decimal_value(amount.get("actual")),
                "ordered": decimal_value(amount.get("ordered")),
                "lent": decimal_value(amount.get("lent")),
                "reorder_level": decimal_value(amount.get("reorderLevel")),
                "max_level": decimal_value(amount.get("maxLevel")),
                "revision": data.get("revision") or 0,
                "last_synced_at": now,
            },
        )
        counts["seen"] += 1
        counts["created" if created else "updated"] += 1
    Product.objects.filter(pk=product.pk).update(stock_last_synced_at=now)
    product.stock_last_synced_at = now
    return counts


STOCK_SYNC_ENTITY = "stocks_by_store"


def _download_store_stocks(store, client, revision=None):
    rows = []
    max_revision = int(revision or 0)
    try:
        for page, page_revision in client.organizational_unit_product_stocks(
            store.korona_id,
            revision=revision,
            page_size=settings.KORONA_STOCK_PAGE_SIZE,
        ):
            rows.extend(page)
            max_revision = max(
                max_revision,
                int(page_revision or 0),
                max((int(row.get("revision") or 0) for row in page), default=0),
            )
    except KoronaError as exc:
        if exc.status_code != 404 or exc.error_code != "CONDITION_MISMATCH":
            raise
        data = client.organizational_unit(store.korona_id)
        refreshed, _ = upsert_store(data)
        if refreshed.active and refreshed.is_warehouse:
            raise
        logger.info(
            "Retired stock cache for organizational unit %s because KORONA no longer marks it as a warehouse",
            refreshed.number,
        )
        return [], 0, False
    return rows, max_revision, True


def _bulk_upsert_store_stocks(store, rows, now):
    row_by_product = {
        str(product_id): row
        for row in rows
        if (product_id := reference_id(row.get("product")))
    }
    products = {
        str(product.korona_id): product
        for product in Product.objects.filter(korona_id__in=row_by_product)
    }
    missing_product_ids = set(row_by_product) - set(products)
    existing_by_product_id = {
        stock.product_id: stock
        for stock in ProductStock.objects.filter(
            store=store,
            product_id__in=[product.id for product in products.values()],
        )
    }
    stock_rows = []
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    for korona_id, product in products.items():
        data = row_by_product[korona_id]
        amount = data.get("amount") or {}
        values = {
            "actual": decimal_value(amount.get("actual")),
            "ordered": decimal_value(amount.get("ordered")),
            "lent": decimal_value(amount.get("lent")),
            "reorder_level": decimal_value(amount.get("reorderLevel")),
            "max_level": decimal_value(amount.get("maxLevel")),
        }
        existing = existing_by_product_id.get(product.id)
        if existing is None:
            created_count += 1
        elif any(getattr(existing, field) != value for field, value in values.items()):
            updated_count += 1
        else:
            unchanged_count += 1
        stock_rows.append(
            ProductStock(
                product=product,
                store=store,
                **values,
                revision=data.get("revision") or 0,
                last_synced_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    if stock_rows:
        ProductStock.objects.bulk_create(
            stock_rows,
            update_conflicts=True,
            unique_fields=["product", "store"],
            update_fields=[
                "actual",
                "ordered",
                "lent",
                "reorder_level",
                "max_level",
                "revision",
                "last_synced_at",
                "updated_at",
            ],
            batch_size=1000,
        )
        Product.objects.filter(id__in=[product.id for product in products.values()]).update(
            stock_last_synced_at=now
        )
    return {
        "seen": len(stock_rows),
        "created": created_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "missing_product_ids": missing_product_ids,
        "product_ids": {product.id for product in products.values()},
    }


def sync_store_stocks_incremental(store, client=None):
    client = client or KoronaClient()
    state, _ = SyncState.objects.get_or_create(entity=STOCK_SYNC_ENTITY, store=store)
    requested_revision = state.last_revision or None
    rows, max_revision, available = _download_store_stocks(store, client, requested_revision)
    if not available:
        return {"seen": 0, "created": 0, "updated": 0, "deferred": 0, "retired": 1}
    now = timezone.now()
    with transaction.atomic():
        counts = _bulk_upsert_store_stocks(store, rows, now)
        state = SyncState.objects.select_for_update().get(pk=state.pk)
        state.last_synced_at = now
        if not counts["missing_product_ids"]:
            state.last_revision = max(state.last_revision, max_revision)
        else:
            logger.warning(
                "Stock cursor for store %s was not advanced because %s products are not synchronized yet",
                store.number,
                len(counts["missing_product_ids"]),
            )
        state.save(update_fields=["last_revision", "last_synced_at", "updated_at"])
    counts["deferred"] = len(counts.pop("missing_product_ids"))
    counts.pop("product_ids")
    return counts


def reconcile_store_stocks(store, client=None):
    client = client or KoronaClient()
    rows, max_revision, available = _download_store_stocks(store, client)
    if not available:
        return {"seen": 0, "created": 0, "updated": 0, "deferred": 0, "retired": 1}
    now = timezone.now()
    with transaction.atomic():
        counts = _bulk_upsert_store_stocks(store, rows, now)
        missing_local = ProductStock.objects.filter(store=store).exclude(
            product_id__in=counts["product_ids"]
        )
        reset_count = missing_local.exclude(
            actual=0,
            ordered=0,
            lent=0,
            reorder_level=0,
            max_level=0,
        ).count()
        missing_local.update(
            actual=0,
            ordered=0,
            lent=0,
            reorder_level=0,
            max_level=0,
            last_synced_at=now,
            updated_at=now,
        )
        state, _ = SyncState.objects.select_for_update().get_or_create(
            entity=STOCK_SYNC_ENTITY, store=store
        )
        state.last_synced_at = now
        if not counts["missing_product_ids"]:
            state.last_revision = max_revision
        else:
            logger.warning(
                "Reconciliation cursor for store %s was not advanced because %s products are not synchronized yet",
                store.number,
                len(counts["missing_product_ids"]),
            )
        state.save(update_fields=["last_revision", "last_synced_at", "updated_at"])
    counts["updated"] += reset_count
    counts["deferred"] = len(counts.pop("missing_product_ids"))
    counts.pop("product_ids")
    return counts


def _sync_all_stores(function):
    client = KoronaClient()
    totals = {
        "seen": 0,
        "created": 0,
        "updated": 0,
        "deferred": 0,
        "stores_checked": 0,
        "stores_retired": 0,
        "unchanged": 0,
    }
    for store in Store.objects.filter(active=True, is_warehouse=True).order_by("number", "id"):
        counts = function(store, client)
        totals["stores_checked"] += 1
        totals["stores_retired"] += counts.get("retired", 0)
        for key in ("seen", "created", "updated", "deferred", "unchanged"):
            totals[key] += counts.get(key, 0)
    return totals


def sync_stocks():
    return _sync_all_stores(sync_store_stocks_incremental)


def reconcile_stocks():
    return _sync_all_stores(reconcile_store_stocks)


def recalculate_monthly_need(store_id, product_id, target_month=None):
    target_month = target_month or month_start()
    today = timezone.localdate()
    start_30 = today - timedelta(days=29)
    sold_30 = SalesDailySummary.objects.filter(
        store_id=store_id,
        product_id=product_id,
        sales_date__range=(start_30, today),
    ).aggregate(total=Sum("quantity_sold"))["total"] or Decimal("0")
    calculated_at = timezone.now()
    obj, _ = ProductMonthlyNeed.objects.update_or_create(
        store_id=store_id,
        product_id=product_id,
        month=target_month,
        defaults=_monthly_need_values(sold_30, calculated_at),
    )
    return obj


def recalculate_monthly_needs_for_pairs(pairs, target_month=None):
    """Recalculate only changed store/product totals in a bounded number of queries."""
    pairs = {(int(store_id), int(product_id)) for store_id, product_id in pairs}
    if not pairs:
        return 0

    today = timezone.localdate()
    target_month = target_month or month_start(today)
    start_30 = today - timedelta(days=29)
    store_ids = {store_id for store_id, _ in pairs}
    product_ids = {product_id for _, product_id in pairs}
    totals = {
        (row["store_id"], row["product_id"]): row["total"]
        for row in SalesDailySummary.objects.filter(
            store_id__in=store_ids,
            product_id__in=product_ids,
            sales_date__range=(start_30, today),
        )
        .values("store_id", "product_id")
        .annotate(total=Sum("quantity_sold"))
        if (row["store_id"], row["product_id"]) in pairs
    }
    existing = {
        (row.store_id, row.product_id): row
        for row in ProductMonthlyNeed.objects.filter(
            month=target_month,
            store_id__in=store_ids,
            product_id__in=product_ids,
        )
        if (row.store_id, row.product_id) in pairs
    }
    calculated_at = timezone.now()
    create_rows = []
    update_rows = []
    for store_id, product_id in pairs:
        values = _monthly_need_values(
            totals.get((store_id, product_id), Decimal("0")), calculated_at
        )
        row = existing.get((store_id, product_id))
        if row is None:
            create_rows.append(
                ProductMonthlyNeed(
                    store_id=store_id,
                    product_id=product_id,
                    month=target_month,
                    **values,
                )
            )
            continue
        for field, value in values.items():
            setattr(row, field, value)
        row.updated_at = calculated_at
        update_rows.append(row)

    ProductMonthlyNeed.objects.bulk_create(create_rows, batch_size=1000)
    ProductMonthlyNeed.objects.bulk_update(
        update_rows,
        ["needed_quantity", "avg_daily_sales_30", "last_calculated_at", "updated_at"],
        batch_size=1000,
    )
    return len(pairs)


def _monthly_need_values(sold_30, calculated_at):
    sold_30 = max(sold_30, Decimal("0"))
    return {
        "needed_quantity": sold_30,
        "avg_daily_sales_30": sold_30 / Decimal("30"),
        "last_calculated_at": calculated_at,
    }


def recalculate_stale_monthly_needs():
    today = timezone.localdate()
    stale = list(
        ProductMonthlyNeed.objects.filter(month=month_start(), last_calculated_at__date__lt=today)
    )
    if not stale:
        return 0

    start_30 = today - timedelta(days=29)
    totals = {
        (row["store_id"], row["product_id"]): row["total"]
        for row in SalesDailySummary.objects.filter(sales_date__range=(start_30, today))
        .values("store_id", "product_id")
        .annotate(total=Sum("quantity_sold"))
    }
    calculated_at = timezone.now()
    fields = [
        "needed_quantity",
        "avg_daily_sales_30",
        "last_calculated_at",
        "updated_at",
    ]
    for row in stale:
        values = _monthly_need_values(totals.get((row.store_id, row.product_id), Decimal("0")), calculated_at)
        for field, value in values.items():
            setattr(row, field, value)
        row.updated_at = calculated_at
    ProductMonthlyNeed.objects.bulk_update(stale, fields, batch_size=1000)
    return len(stale)


@transaction.atomic
def rebuild_all_monthly_needs():
    """Repair recent daily sales from receipt lines, then replace all rolling-30 totals."""
    today = timezone.localdate()
    start_30 = today - timedelta(days=29)
    calculated_at = timezone.now()
    daily_rows = list(
        ReceiptSaleLine.objects.filter(sales_date__range=(start_30, today))
        .values("store_id", "product_id", "sales_date")
        .annotate(
            total=Sum("quantity"),
            receipt_count=Count("id", filter=~Q(quantity=0)),
            max_revision=Max("receipt_revision"),
        )
    )
    SalesDailySummary.objects.filter(sales_date__range=(start_30, today)).delete()
    SalesDailySummary.objects.bulk_create(
        [
            SalesDailySummary(
                store_id=row["store_id"],
                product_id=row["product_id"],
                sales_date=row["sales_date"],
                quantity_sold=row["total"],
                receipts_count=row["receipt_count"],
                last_receipt_revision=row["max_revision"],
            )
            for row in daily_rows
        ],
        batch_size=1000,
    )
    rows = (
        SalesDailySummary.objects.filter(sales_date__range=(start_30, today))
        .values("store_id", "product_id")
        .annotate(total=Sum("quantity_sold"))
    )
    ProductMonthlyNeed.objects.filter(month=month_start(today)).delete()
    totals = [
        ProductMonthlyNeed(
            store_id=row["store_id"],
            product_id=row["product_id"],
            month=month_start(today),
            **_monthly_need_values(row["total"], calculated_at),
        )
        for row in rows
    ]
    ProductMonthlyNeed.objects.bulk_create(totals, batch_size=1000)
    return {
        "seen": len(totals),
        "created": len(totals),
        "updated": 0,
        "daily_summaries_rebuilt": len(daily_rows),
        "monthly_totals_rebuilt": len(totals),
    }


def _sync_revision_receipts(client, state, affected, counts, batch_pages, initialize_to_latest=False):
    if initialize_to_latest and not state.last_revision:
        payload = client.request(
            "GET",
            client.account_path("receipts"),
            params={
                "minBookingTime": (timezone.now() - timedelta(days=90)).isoformat(timespec="seconds"),
                "sort": "-revision",
                "omitPageCounts": "true",
                "size": 1,
                "page": 1,
            },
        ) or {}
        rows = payload.get("results") or []
        save_sync_state(
            state,
            int(payload.get("maxRevision") or (rows[0].get("revision") if rows else 0) or 0),
        )
        return

    cursor = state.last_revision
    # KORONA rejects fractional seconds on this filter for some account clusters.
    min_booking_time = (timezone.now() - timedelta(days=400)).isoformat(timespec="seconds")
    for _ in range(batch_pages):
        params = {
            "revision": cursor,
            "voidedItems": "true",
            "omitPageCounts": "true",
            "minBookingTime": min_booking_time,
            "sort": "revision",
            "size": 100,
            "page": 1,
        }
        payload = client.request("GET", client.account_path("receipts"), params=params)
        receipts = (payload or {}).get("results", [])
        if not receipts:
            cursor = (payload or {}).get("maxRevision", cursor)
            break
        for receipt in receipts:
            counts["seen"] += 1
            _apply_receipt(receipt, affected, counts)
        cursor = max(int(receipt.get("revision") or 0) for receipt in receipts)
        save_sync_state(state, cursor)
        if len(receipts) < 100:
            cursor = payload.get("maxRevision", cursor)
            break
    save_sync_state(state, cursor)


def sync_receipts(batch_pages=5):
    """Apply only receipts changed after the live revision cursor."""
    client = KoronaClient()
    counts = {"seen": 0, "created": 0, "updated": 0}
    affected = set()
    state = get_sync_state("receipts_live")
    starting_revision = state.last_revision

    _sync_revision_receipts(
        client,
        state,
        affected,
        counts,
        max(1, min(batch_pages, 5)),
        initialize_to_latest=True,
    )
    counts["products_recalculated"] = recalculate_monthly_needs_for_pairs(affected)
    counts["deferred"] = DeferredReceipt.objects.count()
    counts["starting_revision"] = starting_revision
    counts["ending_revision"] = state.last_revision
    return counts


def replay_deferred_receipts(limit=5000):
    counts = {"seen": 0, "created": 0, "updated": 0, "deferred": 0}
    affected = set()
    payloads = list(
        DeferredReceipt.objects.order_by("receipt_revision").values_list("raw_data", flat=True)[:limit]
    )
    for receipt in payloads:
        counts["seen"] += 1
        _apply_receipt(receipt, affected, counts)
    for store_id, product_id in affected:
        recalculate_monthly_need(store_id, product_id)
    counts["deferred"] = DeferredReceipt.objects.count()
    return counts


def sync_monthly_receipt_history(days=30):
    """Replace each day in the rolling window with a complete KORONA receipt download."""
    client = KoronaClient()
    counts = {"seen": 0, "created": 0, "updated": 0, "deferred": 0, "days_refreshed": 0}
    now = timezone.now().replace(microsecond=0)
    today = timezone.localdate(now)
    for offset in range(days):
        sales_date = today - timedelta(days=offset)
        day_start = timezone.make_aware(datetime.combine(sales_date, time.min))
        day_end = min(now, timezone.make_aware(datetime.combine(sales_date, time.max)).replace(microsecond=0))
        page = 1
        day_receipts = []
        while True:
            payload = client.request(
                "GET",
                client.account_path("receipts"),
                params={
                    "minBookingTime": day_start.isoformat(timespec="seconds"),
                    "maxBookingTime": day_end.isoformat(timespec="seconds"),
                    "voidedItems": "true",
                    "omitPageCounts": "true",
                    "sort": "-revision",
                    "size": 100,
                    "page": page,
                },
            ) or {}
            receipts = payload.get("results") or []
            day_receipts.extend(receipts)
            if len(receipts) < 100:
                break
            page += 1

        # Fetch the complete day before replacing it, so a failed download never
        # clears that day's known-good local receipt ledger.
        with transaction.atomic():
            ReceiptSaleLine.objects.filter(sales_date=sales_date).delete()
            SalesDailySummary.objects.filter(sales_date=sales_date).delete()
            affected = set()
            for receipt in day_receipts:
                counts["seen"] += 1
                _apply_receipt(receipt, affected, counts)
        counts["days_refreshed"] += 1

    replayed = replay_deferred_receipts()
    counts["created"] += replayed["created"]
    counts["updated"] += replayed["updated"]
    counts["deferred"] = DeferredReceipt.objects.count()
    return counts


def reconcile_monthly_needs():
    """Fetch the complete rolling month from KORONA, then rebuild every total once."""
    receipt_counts = sync_monthly_receipt_history(days=30)
    total_counts = rebuild_all_monthly_needs()
    return {
        "seen": receipt_counts.get("seen", 0),
        "created": receipt_counts.get("created", 0),
        "updated": receipt_counts.get("updated", 0),
        "deferred": receipt_counts.get("deferred", 0),
        "days_refreshed": receipt_counts.get("days_refreshed", 0),
        "receipts_checked": receipt_counts.get("seen", 0),
        "receipt_lines_created": receipt_counts.get("created", 0),
        "receipt_lines_updated": receipt_counts.get("updated", 0),
        "daily_summaries_rebuilt": total_counts.get("daily_summaries_rebuilt", 0),
        "monthly_totals_rebuilt": total_counts.get("monthly_totals_rebuilt", 0),
    }


def _defer_receipt(receipt, reason):
    receipt_id = receipt.get("id")
    incoming_revision = int(receipt.get("revision") or 0)
    deferred, created = DeferredReceipt.objects.get_or_create(
        receipt_id=receipt_id,
        defaults={
            "receipt_revision": incoming_revision,
            "raw_data": receipt,
            "reason": reason,
        },
    )
    if not created and incoming_revision >= deferred.receipt_revision:
        deferred.receipt_revision = incoming_revision
        deferred.raw_data = receipt
        deferred.reason = reason
        deferred.save(update_fields=["receipt_revision", "raw_data", "reason", "updated_at"])


@transaction.atomic
def _apply_receipt(receipt, affected, counts):
    store_id = reference_id(receipt.get("organizationalUnit")) or reference_id(receipt.get("warehouse"))
    receipt_id = receipt.get("id")
    if not receipt_id:
        return
    incoming_revision = int(receipt.get("revision") or 0)
    store = Store.objects.filter(korona_id=store_id).first()
    if not store:
        _defer_receipt(receipt, f"Store {store_id or 'unknown'} is not synchronized")
        counts["deferred"] = counts.get("deferred", 0) + 1
        return
    booking = parse_datetime(receipt.get("bookingTime") or receipt.get("creationTime") or "")
    sales_date = timezone.localtime(booking).date() if booking else timezone.localdate()
    cancelled = bool(receipt.get("cancelled") or receipt.get("voided"))
    quantities = defaultdict(Decimal)
    referenced_product_ids = set()
    for item in receipt.get("items") or []:
        product_id = reference_id(item.get("product"))
        if product_id:
            referenced_product_ids.add(str(product_id))
            if not cancelled:
                quantities[str(product_id)] += decimal_value(item.get("quantity"))
    existing = {
        str(line.product.korona_id): line
        for line in ReceiptSaleLine.objects.select_for_update()
        .select_related("product")
        .filter(receipt_id=receipt_id)
    }
    candidate_product_ids = referenced_product_ids | set(existing)
    products = {str(p.korona_id): p for p in Product.objects.filter(korona_id__in=candidate_product_ids)}
    missing_product_ids = referenced_product_ids - set(products)
    if missing_product_ids:
        _defer_receipt(receipt, f"Waiting for {len(missing_product_ids)} product(s)")
        counts["deferred"] = counts.get("deferred", 0) + 1
    else:
        DeferredReceipt.objects.filter(receipt_id=receipt_id).delete()
    product_ids = (set(quantities) | set(existing)) & set(products)
    for korona_product_id in product_ids:
        product = products.get(korona_product_id)
        if not product:
            continue
        old_line = existing.get(korona_product_id)
        if old_line and old_line.receipt_revision > incoming_revision:
            continue
        old_quantity = old_line.quantity if old_line else Decimal("0")
        new_quantity = quantities.get(korona_product_id, Decimal("0"))
        old_store = old_line.store if old_line else store
        old_date = old_line.sales_date if old_line else sales_date
        if (
            old_line
            and old_line.receipt_revision == incoming_revision
            and old_quantity == new_quantity
            and old_line.store_id == store.id
            and old_line.sales_date == sales_date
        ):
            continue
        if old_quantity:
            _adjust_daily(old_store, product, old_date, -old_quantity, -1, incoming_revision)
            affected.add((old_store.id, product.id))
        if new_quantity:
            _adjust_daily(store, product, sales_date, new_quantity, 1, incoming_revision)
            affected.add((store.id, product.id))
            _, created = ReceiptSaleLine.objects.update_or_create(
                receipt_id=receipt_id,
                product=product,
                defaults={
                    "receipt_revision": incoming_revision,
                    "store": store,
                    "sales_date": sales_date,
                    "quantity": new_quantity,
                },
            )
            counts["created" if created else "updated"] += 1
        elif old_line:
            old_line.delete()
            counts["updated"] += 1


def _adjust_daily(store, product, sales_date, quantity_delta, receipt_delta, revision):
    summary, _ = SalesDailySummary.objects.select_for_update().get_or_create(
        store=store, product=product, sales_date=sales_date
    )
    # Returns are negative sales and must offset sales before the final
    # rolling-30 requirement is clamped to zero.
    summary.quantity_sold += quantity_delta
    summary.receipts_count = max(summary.receipts_count + receipt_delta, 0)
    summary.last_receipt_revision = max(summary.last_receipt_revision, revision)
    if summary.quantity_sold == 0 and summary.receipts_count == 0:
        summary.delete()
    else:
        summary.save()
