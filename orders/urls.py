from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.HealthAPIView.as_view(), name="api-health"),
    path("stores/", views.StoreListAPIView.as_view(), name="api-stores"),
    path("orders/", views.OrderListAPIView.as_view(), name="api-orders"),
    path("orders/<int:pk>/", views.OrderListDetailAPIView.as_view(), name="api-order-detail"),
    path("orders/<int:pk>/finalize/", views.OrderListFinalizeAPIView.as_view(), name="api-order-finalize"),
    path("product-categories/", views.ProductCategoryPreferenceAPIView.as_view(), name="api-product-categories"),
    path("orders/<int:pk>/items/", views.OrderItemCreateAPIView.as_view(), name="api-order-item-create"),
    path("orders/<int:pk>/items/bulk/", views.OrderItemBulkCreateAPIView.as_view(), name="api-order-item-bulk-create"),
    path("items/<int:pk>/", views.OrderItemDetailAPIView.as_view(), name="api-order-item-detail"),
    path("products/search/", views.ProductSearchAPIView.as_view(), name="api-product-search"),
    path("products/<int:pk>/availability/", views.ProductAvailabilityAPIView.as_view(), name="api-product-availability"),
    path("products/<int:pk>/preferred-supplier/", views.ProductPreferredSupplierAPIView.as_view(), name="api-product-preferred-supplier"),
    path("inventory/compare/", views.InventoryComparisonAPIView.as_view(), name="api-inventory-compare"),
    path("bulk-orders/", views.BulkOrderListAPIView.as_view(), name="api-bulk-orders"),
    path("bulk-orders/<int:pk>/", views.BulkOrderDetailAPIView.as_view(), name="api-bulk-order-detail"),
    path("bulk-orders/<int:pk>/items/", views.BulkOrderItemsAPIView.as_view(), name="api-bulk-order-items"),
    path("bulk-order-items/<int:pk>/", views.BulkOrderItemAPIView.as_view(), name="api-bulk-order-item"),
    path("bulk-orders/<int:pk>/clear/", views.BulkOrderClearAPIView.as_view(), name="api-bulk-order-clear"),
    path("bulk-orders/<int:pk>/export.<str:file_format>", views.BulkOrderExportAPIView.as_view(), name="api-bulk-order-export"),
    path("grid-preferences/<slug:grid_key>/", views.GridPreferenceAPIView.as_view(), name="api-grid-preference"),
    path("orders/<int:pk>/export/<str:kind>.<str:file_format>", views.ExportAPIView.as_view(), name="api-export"),
    path("orders/<int:pk>/export-grid.pdf", views.GridPDFExportAPIView.as_view(), name="api-grid-pdf-export"),
    path("orders/<int:pk>/export-grid.xlsx", views.GridXLSXExportAPIView.as_view(), name="api-grid-xlsx-export"),
    path("operations/services/", views.ServicesAPIView.as_view(), name="api-services"),
    path("operations/services/<str:service_name>/run/", views.ServiceRunAPIView.as_view(), name="api-service-run"),
    path("users/", views.UserListAPIView.as_view(), name="api-users"),
    path("users/<int:pk>/", views.UserDetailAPIView.as_view(), name="api-user-detail"),
]
