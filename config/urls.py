from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from orders import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.HomeView.as_view(), name="home"),
    path("orders/<int:pk>/", views.OrderListPageView.as_view(), name="order-list-page"),
    path("inventory/", views.InventoryView.as_view(), name="inventory"),
    path("bulk-orders/", views.BulkOrdersView.as_view(), name="bulk-orders"),
    path("bulk-orders/<int:pk>/", views.BulkOrderDetailView.as_view(), name="bulk-order-detail"),
    path("ops/", views.OperationsView.as_view(), name="operations"),
    path("users/", views.UsersView.as_view(), name="users"),
    path("api/", include("orders.urls")),
]
