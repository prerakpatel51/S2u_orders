import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .korona import KoronaClient
from .models import (
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
MONTHLY_NEED_VERSION = "trailing-30-v1"


def month_start(day=None):
    day = day or timezone.localdate()
    return day.replace(day=1)


def get_sync_state(entity):
    state, _ = SyncState.objects.get_or_create(entity=entity, store=None)
    return state


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
            _, created = Store.objects.update_or_create(
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
            counts["created" if created else "updated"] += 1
        max_revision = page_revision or max_revision
    state.last_revision = max_revision
    state.last_synced_at = timezone.now()
    state.save(update_fields=["last_revision", "last_synced_at", "updated_at"])
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
            product, created = Product.objects.update_or_create(
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
    state.last_revision = max_revision
    state.last_synced_at = timezone.now()
    state.save(update_fields=["last_revision", "last_synced_at", "updated_at"])
    return counts


def refresh_product_stocks(product, client=None):
    client = client or KoronaClient()
    counts = {"seen": 0, "created": 0, "updated": 0}
    store_by_korona = {str(s.korona_id): s for s in Store.objects.filter(active=True)}
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


def sync_stocks(limit=100):
    priority_ids = list(
        Product.objects.filter(orderlistitem__order_list__status="draft")
        .values_list("id", flat=True)
        .distinct()[:limit]
    )
    products = list(Product.objects.filter(id__in=priority_ids).order_by("stock_last_synced_at", "id"))
    remaining = limit - len(products)
    if remaining > 0:
        products.extend(
            Product.objects.filter(active=True, track_inventory=True)
            .exclude(id__in=priority_ids)
            .order_by("stock_last_synced_at", "id")[:remaining]
        )
    client = KoronaClient()
    totals = {"seen": 0, "created": 0, "updated": 0}
    for product in products:
        counts = refresh_product_stocks(product, client)
        for key in totals:
            totals[key] += counts[key]
    return totals


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


def _monthly_need_values(sold_30, calculated_at):
    sold_30 = max(sold_30, Decimal("0"))
    return {
        "needed_quantity": sold_30,
        "avg_daily_sales_30": sold_30 / Decimal("30"),
        "avg_daily_sales_90": Decimal("0"),
        "seasonal_quantity": Decimal("0"),
        "confidence": Decimal("1"),
        "calculation_version": MONTHLY_NEED_VERSION,
        "last_calculated_at": calculated_at,
    }


def recalculate_stale_monthly_needs():
    today = timezone.localdate()
    stale = list(
        ProductMonthlyNeed.objects.filter(month=month_start()).filter(
            ~Q(calculation_version=MONTHLY_NEED_VERSION) | Q(last_calculated_at__date__lt=today)
        )
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
        "avg_daily_sales_90",
        "seasonal_quantity",
        "confidence",
        "calculation_version",
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
        state.last_revision = int(payload.get("maxRevision") or (rows[0].get("revision") if rows else 0) or 0)
        state.last_synced_at = timezone.now()
        state.save(update_fields=["last_revision", "last_synced_at", "updated_at"])
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
        state.last_revision = cursor
        state.last_synced_at = timezone.now()
        state.save(update_fields=["last_revision", "last_synced_at", "updated_at"])
        if len(receipts) < 100:
            cursor = payload.get("maxRevision", cursor)
            break
    state.last_revision = cursor
    state.last_synced_at = timezone.now()
    state.save(update_fields=["last_revision", "last_synced_at", "updated_at"])


def _sync_recent_receipts(client, affected, counts, days_per_run=2, lookback_days=90):
    state = get_sync_state("receipts_recent")
    today = timezone.localdate()
    cutoff = today - timedelta(days=lookback_days - 1)
    try:
        next_date = date.fromisoformat(state.cursor_data.get("next_date", ""))
    except ValueError:
        next_date = today
    if next_date < cutoff:
        state.cursor_data = {"complete": True, "next_date": next_date.isoformat()}
        state.last_synced_at = timezone.now()
        state.save(update_fields=["cursor_data", "last_synced_at", "updated_at"])
        return

    for _ in range(days_per_run):
        if next_date < cutoff:
            break
        day_start = timezone.make_aware(datetime.combine(next_date, time.min))
        day_end = timezone.make_aware(datetime.combine(next_date, time.max)).replace(microsecond=0)
        page = 1
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
            for receipt in receipts:
                counts["seen"] += 1
                _apply_receipt(receipt, affected, counts)
            if len(receipts) < 100:
                break
            page += 1
        next_date -= timedelta(days=1)
        state.cursor_data = {"complete": next_date < cutoff, "next_date": next_date.isoformat()}
        state.last_synced_at = timezone.now()
        state.save(update_fields=["cursor_data", "last_synced_at", "updated_at"])


def sync_receipts(batch_pages=20, recent_days_per_run=2):
    client = KoronaClient()
    counts = {"seen": 0, "created": 0, "updated": 0}
    affected = set()

    # Keep new transactions current while recent demand history fills newest-first.
    _sync_revision_receipts(
        client, get_sync_state("receipts_live"), affected, counts, min(batch_pages, 5), initialize_to_latest=True
    )
    _sync_recent_receipts(client, affected, counts, days_per_run=recent_days_per_run)
    # Continue the older revision backfill for seasonal forecasting at a lower rate.
    recent_complete = get_sync_state("receipts_recent").cursor_data.get("complete", False)
    historical_pages = batch_pages if recent_complete else min(batch_pages, 5)
    _sync_revision_receipts(client, get_sync_state("receipts"), affected, counts, historical_pages)

    for store_id, product_id in affected:
        recalculate_monthly_need(store_id, product_id)
    recalculate_stale_monthly_needs()
    return counts


@transaction.atomic
def _apply_receipt(receipt, affected, counts):
    store_id = reference_id(receipt.get("organizationalUnit")) or reference_id(receipt.get("warehouse"))
    store = Store.objects.filter(korona_id=store_id).first()
    receipt_id = receipt.get("id")
    if not store or not receipt_id:
        return
    booking = parse_datetime(receipt.get("bookingTime") or receipt.get("creationTime") or "")
    sales_date = timezone.localtime(booking).date() if booking else timezone.localdate()
    cancelled = bool(receipt.get("cancelled") or receipt.get("voided"))
    quantities = defaultdict(Decimal)
    if not cancelled:
        for item in receipt.get("items") or []:
            product_id = reference_id(item.get("product"))
            if product_id:
                quantities[str(product_id)] += decimal_value(item.get("quantity"))
    existing = {str(line.product.korona_id): line for line in ReceiptSaleLine.objects.select_related("product").filter(receipt_id=receipt_id)}
    product_ids = set(quantities) | set(existing)
    products = {str(p.korona_id): p for p in Product.objects.filter(korona_id__in=product_ids)}
    incoming_revision = int(receipt.get("revision") or 0)
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
    summary.quantity_sold = max(summary.quantity_sold + quantity_delta, Decimal("0"))
    summary.receipts_count = max(summary.receipts_count + receipt_delta, 0)
    summary.last_receipt_revision = max(summary.last_receipt_revision, revision)
    summary.save()
