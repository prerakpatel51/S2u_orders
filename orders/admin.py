from django.contrib import admin

from .models import (
    ApiRequestLog,
    Delivery,
    DeliveryAsset,
    DeliveryAssetReplica,
    DeliveryBackup,
    DeliveryEvent,
    DeliveryKeyword,
    DeliveryRecoveryExport,
    DeferredReceipt,
    OrderItemTransfer,
    OrderList,
    OrderListItem,
    Product,
    ProductCode,
    ProductMonthlyNeed,
    ProductStock,
    ReceiptSaleLine,
    SalesDailySummary,
    ServiceControl,
    Store,
    SyncRun,
    SyncState,
    SystemSetting,
    SystemLog,
    UserGridPreference,
)


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "active", "is_warehouse", "last_synced_at")
    list_filter = ("active", "is_warehouse")
    search_fields = ("number", "name")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "active", "track_inventory", "last_synced_at")
    list_filter = ("active", "track_inventory")
    search_fields = ("number", "name", "codes__code")


@admin.register(OrderList)
class OrderListAdmin(admin.ModelAdmin):
    list_display = ("store", "order_date", "status", "created_by", "updated_at")
    list_filter = ("status", "store")


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("uuid", "store", "delivered_at", "status", "submitted_by", "reviewed_by")
    list_filter = ("status", "store")
    search_fields = (
        "uuid",
        "store__number",
        "store__name",
        "reference_number",
        "general_notes",
        "issue_notes",
        "keywords__name",
    )
    readonly_fields = ("uuid", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        # Confirmed evidence is intentionally immutable. Bucket removal is a
        # separate disaster-recovery operation, not a Django Admin action.
        return False


class ImmutableEvidenceAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(DeliveryAsset)
class DeliveryAssetAdmin(ImmutableEvidenceAdmin):
    list_display = (
        "uuid",
        "delivery",
        "category",
        "upload_status",
        "size_bytes",
        "uploaded_by",
    )
    list_filter = ("category", "upload_status")
    search_fields = ("uuid", "delivery__uuid", "object_key", "original_filename")


@admin.register(DeliveryAssetReplica)
class DeliveryAssetReplicaAdmin(ImmutableEvidenceAdmin):
    list_display = ("asset", "status", "size_bytes", "verified_at", "attempts")
    list_filter = ("status",)
    search_fields = ("asset__uuid", "object_key", "checksum_sha256")


admin.site.register(
    [
        ProductCode,
        ProductStock,
        SalesDailySummary,
        ReceiptSaleLine,
        DeferredReceipt,
        ProductMonthlyNeed,
        OrderListItem,
        OrderItemTransfer,
        UserGridPreference,
        ServiceControl,
        SyncState,
        SyncRun,
        SystemSetting,
        SystemLog,
        ApiRequestLog,
        DeliveryBackup,
        DeliveryEvent,
        DeliveryKeyword,
        DeliveryRecoveryExport,
    ]
)
