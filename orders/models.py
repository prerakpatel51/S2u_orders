import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Store(TimeStampedModel):
    korona_id = models.UUIDField(unique=True)
    number = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    is_warehouse = models.BooleanField(default=False)
    revision = models.BigIntegerField(default=0, db_index=True)
    raw_data = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["number", "name"]

    def __str__(self):
        return f"{self.number} - {self.name}"


class Product(TimeStampedModel):
    korona_id = models.UUIDField(unique=True)
    number = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=255, db_index=True)
    normalized_name = models.CharField(max_length=255, db_index=True)
    active = models.BooleanField(default=True)
    track_inventory = models.BooleanField(default=True)
    revision = models.BigIntegerField(default=0, db_index=True)
    raw_data = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    stock_last_synced_at = models.DateTimeField(null=True, blank=True, db_index=True)
    preferred_supplier_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return f"{self.number} - {self.name}"


class ProductCode(TimeStampedModel):
    product = models.ForeignKey(Product, related_name="codes", on_delete=models.CASCADE)
    code = models.CharField(max_length=128, db_index=True)
    normalized_code = models.CharField(max_length=128, db_index=True)
    container_size = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("product", "code")]


class ProductStock(TimeStampedModel):
    product = models.ForeignKey(Product, related_name="stocks", on_delete=models.CASCADE)
    store = models.ForeignKey(Store, related_name="product_stocks", on_delete=models.CASCADE)
    actual = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    ordered = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    lent = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    max_level = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    revision = models.BigIntegerField(default=0, db_index=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("product", "store")]
        indexes = [models.Index(fields=["store", "product"])]


class SalesDailySummary(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    sales_date = models.DateField(db_index=True)
    quantity_sold = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    receipts_count = models.PositiveIntegerField(default=0)
    last_receipt_revision = models.BigIntegerField(default=0)

    class Meta:
        unique_together = [("store", "product", "sales_date")]
        indexes = [
            models.Index(fields=["store", "product", "sales_date"]),
        ]


class ReceiptSaleLine(TimeStampedModel):
    """Latest quantity contribution for one product on one KORONA receipt."""

    receipt_id = models.UUIDField(db_index=True)
    receipt_revision = models.BigIntegerField(default=0, db_index=True)
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    sales_date = models.DateField(db_index=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        unique_together = [("receipt_id", "product")]
        indexes = [models.Index(fields=["store", "product", "sales_date"])]


class DeferredReceipt(TimeStampedModel):
    """Latest receipt payload waiting for a referenced store or product."""

    receipt_id = models.UUIDField(unique=True)
    receipt_revision = models.BigIntegerField(default=0, db_index=True)
    raw_data = models.JSONField(default=dict)
    reason = models.CharField(max_length=255, blank=True)


class ProductMonthlyNeed(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    month = models.DateField(db_index=True)
    needed_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    avg_daily_sales_30 = models.DecimalField(max_digits=12, decimal_places=5, default=0)
    last_calculated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("store", "product", "month")]


class OrderList(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"
        CANCELLED = "cancelled", "Cancelled"

    store = models.ForeignKey(Store, related_name="order_lists", on_delete=models.PROTECT)
    order_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="order_lists", on_delete=models.PROTECT)
    korona_store_order_id = models.UUIDField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("store", "order_date")]
        ordering = ["-order_date", "store__number"]

    def __str__(self):
        return f"{self.store} / {self.order_date}"


class OrderListItem(TimeStampedModel):
    order_list = models.ForeignKey(OrderList, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    current_stock_snapshot = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    on_shelf_quantity = models.PositiveIntegerField(default=0)
    monthly_needed_snapshot = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    joe_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    bt_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    sqw_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    notes = models.TextField(blank=True)
    row_order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="created_order_items", on_delete=models.PROTECT)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="updated_order_items", on_delete=models.PROTECT)

    class Meta:
        unique_together = [("order_list", "product")]
        ordering = ["row_order", "id"]


class OrderItemTransfer(TimeStampedModel):
    item = models.ForeignKey(OrderListItem, related_name="transfers", on_delete=models.CASCADE)
    from_store = models.ForeignKey(Store, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        unique_together = [("item", "from_store")]


class BulkOrderList(TimeStampedModel):
    name = models.CharField(max_length=160)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="bulk_order_lists", on_delete=models.PROTECT)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class BulkOrderItem(TimeStampedModel):
    bulk_order = models.ForeignKey(BulkOrderList, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    row_order = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("bulk_order", "product")]
        ordering = ["row_order", "id"]


class BulkOrderQuantity(TimeStampedModel):
    item = models.ForeignKey(BulkOrderItem, related_name="quantities", on_delete=models.CASCADE)
    store = models.ForeignKey(Store, on_delete=models.PROTECT)
    cases = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        unique_together = [("item", "store")]


class UserGridPreference(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    grid_key = models.CharField(max_length=128)
    column_state = models.JSONField(default=list, blank=True)
    filter_state = models.JSONField(default=dict, blank=True)
    sort_state = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = [("user", "grid_key")]


class SystemSetting(TimeStampedModel):
    key = models.CharField(max_length=128, unique=True)
    value = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self):
        return self.key


class ServiceControl(TimeStampedModel):
    class Status(models.TextChoices):
        IDLE = "idle", "Idle"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DISABLED = "disabled", "Disabled"
        ERROR = "error", "Error"

    service_name = models.CharField(max_length=80, unique=True)
    enabled = models.BooleanField(default=True)
    interval_seconds = models.PositiveIntegerField(default=300)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IDLE)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.service_name


class SyncState(TimeStampedModel):
    entity = models.CharField(max_length=80)
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.CASCADE)
    last_revision = models.BigIntegerField(default=0)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    cursor_data = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = [("entity", "store")]


class SyncRun(TimeStampedModel):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"
        SKIPPED = "skipped", "Skipped"

    job_name = models.CharField(max_length=80, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    records_seen = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    metrics = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)


class SystemLog(TimeStampedModel):
    level = models.CharField(max_length=20, db_index=True)
    source = models.CharField(max_length=120, db_index=True)
    message = models.TextField()
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ApiRequestLog(TimeStampedModel):
    service = models.CharField(max_length=80, default="korona", db_index=True)
    method = models.CharField(max_length=12)
    url_path = models.TextField()
    status_code = models.PositiveIntegerField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)


class Delivery(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under review"
        NEEDS_INFO = "needs_info", "Needs more information"
        ISSUE_FOUND = "issue_found", "Issue found"
        VERIFIED = "verified", "Verified"
        RESOLVED = "resolved", "Resolved"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    store = models.ForeignKey(Store, related_name="deliveries", on_delete=models.PROTECT)
    delivered_at = models.DateTimeField(default=timezone.now, db_index=True)
    reference_number = models.CharField(max_length=120, blank=True, db_index=True)
    general_notes = models.TextField(blank=True)
    issue_notes = models.TextField(blank=True)
    expected_cases = models.PositiveIntegerField(null=True, blank=True)
    delivered_cases = models.PositiveIntegerField(null=True, blank=True)
    damaged_cases = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="submitted_deliveries", on_delete=models.PROTECT
    )
    submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="reviewed_deliveries",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-delivered_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "delivered_at"]),
            models.Index(fields=["store", "delivered_at"]),
            models.Index(fields=["submitted_by", "delivered_at"]),
        ]

    @property
    def storage_prefix(self):
        stamp = timezone.localtime(self.delivered_at)
        safe_store = "".join(c if c.isalnum() or c in "-_" else "-" for c in self.store.number)
        return (
            f"deliveries/{stamp:%Y/%m/%d}/store-{safe_store}/delivery-{self.uuid}"
        )

    def __str__(self):
        return f"{self.store} / {timezone.localtime(self.delivered_at):%Y-%m-%d %H:%M}"


class DeliveryAsset(TimeStampedModel):
    class Category(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        BOXES = "boxes", "Boxes"
        DAMAGE = "damage", "Damage"
        NOTES = "notes", "Notes"

    class UploadStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        UPLOADED = "uploaded", "Uploaded"
        FAILED = "failed", "Failed"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    delivery = models.ForeignKey(Delivery, related_name="assets", on_delete=models.CASCADE)
    category = models.CharField(max_length=16, choices=Category.choices, db_index=True)
    object_key = models.CharField(max_length=900, unique=True)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    size_bytes = models.PositiveBigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    upload_status = models.CharField(
        max_length=16, choices=UploadStatus.choices, default=UploadStatus.PENDING
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="delivery_uploads", on_delete=models.PROTECT
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["category", "position", "created_at"]
        indexes = [models.Index(fields=["delivery", "category", "position"])]


class DeliveryKeyword(TimeStampedModel):
    name = models.CharField(max_length=64)
    normalized_name = models.CharField(max_length=64, unique=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="delivery_keywords_created", on_delete=models.PROTECT
    )
    deliveries = models.ManyToManyField(Delivery, related_name="keywords", blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DeliveryEvent(TimeStampedModel):
    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        PHOTO_ADDED = "photo_added", "Photo added"
        SUBMITTED = "submitted", "Submitted"
        REVIEWED = "reviewed", "Reviewed"
        KEYWORDS_CHANGED = "keywords_changed", "Keywords changed"
        DOWNLOADED = "downloaded", "Downloaded"

    delivery = models.ForeignKey(Delivery, related_name="events", on_delete=models.CASCADE)
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="delivery_events", null=True, on_delete=models.SET_NULL
    )
    message = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["delivery", "created_at"])]


class DeliveryBackup(TimeStampedModel):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    object_key = models.CharField(max_length=900, blank=True)
    delivery_count = models.PositiveIntegerField(default=0)
    asset_count = models.PositiveIntegerField(default=0)
    size_bytes = models.PositiveBigIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="delivery_backups",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ["-created_at"]
