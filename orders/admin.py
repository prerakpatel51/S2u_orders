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
        DeliveryAsset,
        DeliveryAssetReplica,
        DeliveryBackup,
        DeliveryEvent,
        DeliveryKeyword,
        DeliveryRecoveryExport,
    ]
)
