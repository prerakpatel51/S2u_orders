from django.contrib import admin

from .models import (
    ApiRequestLog,
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
        SystemLog,
        ApiRequestLog,
    ]
)
