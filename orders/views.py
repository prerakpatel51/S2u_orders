from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.password_validation import validate_password
from django.contrib.postgres.search import TrigramSimilarity
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.db.models import Avg, Count, F, Max, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .exports import (
    EXPORT_KINDS,
    bulk_order_export_response,
    grid_pdf_response,
    grid_xlsx_response,
    pdf_response,
    xlsx_response,
)
from .models import (
    ApiRequestLog,
    BulkOrderItem,
    BulkOrderList,
    BulkOrderQuantity,
    OrderList,
    OrderListItem,
    Product,
    ProductMonthlyNeed,
    ProductStock,
    ServiceControl,
    Store,
    SyncState,
    SyncRun,
    SystemSetting,
    SystemLog,
    UserGridPreference,
)
from .serializers import (
    GridPreferenceSerializer,
    OrderListItemSerializer,
    OrderListSummarySerializer,
    ProductSearchSerializer,
    StoreSerializer,
    product_supplier_data,
    product_supplier_options,
)
from .services import month_start, refresh_product_stocks
from .tasks import SERVICES, recover_interrupted_runs
from .utils import decimal_value, normalize_search_text


def is_admin(user):
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


def can_edit_order(user, order_list):
    return is_admin(user) or order_list.status == OrderList.Status.DRAFT


def available_products():
    """Products currently active in KORONA and available for new order workflows."""
    show_inactive = SystemSetting.objects.filter(
        key="show_inactive_products", value=True
    ).exists()
    return Product.objects.all() if show_inactive else Product.objects.filter(active=True)


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return is_admin(self.request.user)


class SuperuserRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_superuser


class IsSuperuser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class IsOrderAdmin(BasePermission):
    def has_permission(self, request, view):
        return is_admin(request.user)


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "orders/home.html"


class OrderListPageView(LoginRequiredMixin, TemplateView):
    template_name = "orders/order_list.html"

    def dispatch(self, request, *args, **kwargs):
        self.order_list = get_object_or_404(OrderList.objects.select_related("store"), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["order_list"] = self.order_list
        context["is_order_admin"] = is_admin(self.request.user)
        context["can_edit_order"] = can_edit_order(self.request.user, self.order_list)
        return context


class OperationsView(LoginRequiredMixin, SuperuserRequiredMixin, TemplateView):
    template_name = "orders/operations.html"


class InventoryView(LoginRequiredMixin, TemplateView):
    template_name = "orders/inventory.html"


class BulkOrdersView(LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = "orders/bulk_orders.html"


class BulkOrderDetailView(LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = "orders/bulk_order_detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.bulk_order = get_object_or_404(BulkOrderList, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs); context["bulk_order"] = self.bulk_order; return context


class UsersView(LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = "orders/users.html"


class StoreListAPIView(APIView):
    def get(self, request):
        stores = Store.objects.filter(active=True).annotate(order_count=Count("order_lists"))
        data = StoreSerializer(stores, many=True).data
        counts = {row["id"]: row["order_count"] for row in stores.values("id", "order_count")}
        for item in data:
            item["order_count"] = counts[item["id"]]
        return Response(data)


class OrderListAPIView(APIView):
    def get(self, request):
        lists = OrderList.objects.select_related("store").annotate(item_count=Count("items"))
        if request.query_params.get("store"):
            lists = lists.filter(store_id=request.query_params["store"])
        return Response(OrderListSummarySerializer(lists[:200], many=True).data)

    def post(self, request):
        store = get_object_or_404(Store, pk=request.data.get("store_id"), active=True)
        try:
            order_date = date.fromisoformat(request.data.get("order_date", ""))
        except (TypeError, ValueError):
            return Response({"order_date": "Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        order_list, created = OrderList.objects.get_or_create(
            store=store, order_date=order_date, defaults={"created_by": request.user}
        )
        return Response(
            OrderListSummarySerializer(order_list).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def item_context(order_list, request):
    product_ids = list(order_list.items.values_list("product_id", flat=True))
    stores = list(Store.objects.filter(active=True).order_by("number", "name"))
    stock_map = {
        (row.product_id, row.store_id): row.actual
        for row in ProductStock.objects.filter(product_id__in=product_ids, store__in=stores)
    }
    need_map = {
        (row.product_id, row.store_id): row.needed_quantity
        for row in ProductMonthlyNeed.objects.filter(product_id__in=product_ids, store__in=stores, month=month_start())
    }
    return {"request": request, "stores": stores, "stock_map": stock_map, "need_map": need_map}


class OrderListDetailAPIView(APIView):
    def get(self, request, pk):
        order_list = get_object_or_404(OrderList.objects.select_related("store"), pk=pk)
        items = order_list.items.select_related("product", "order_list__store").prefetch_related("transfers__from_store")
        admin = is_admin(request.user)
        serialized_items = OrderListItemSerializer(
            items, many=True, context=item_context(order_list, request)
        ).data
        if not admin:
            visible = {
                "id", "product", "product_number", "product_name", "on_shelf_quantity",
                "current_store_stock", "current_store_monthly_needed", "notes", "updated_at",
                "preferred_supplier_id", "supplier_name", "supplier_full_name", "supplier_number", "supplier_order_code",
                "supplier_pack_size", "supplier_purchase_price", "supplier_names",
                "commodity_group", "commodity_group_number",
            }
            if order_list.status == OrderList.Status.FINALIZED:
                visible.add("transfers")
            serialized_items = [
                {key: value for key, value in item.items() if key in visible}
                for item in serialized_items
            ]
        return Response({
            "order": OrderListSummarySerializer(order_list).data,
            "stores": StoreSerializer(Store.objects.filter(active=True), many=True).data if admin else [],
            "items": serialized_items,
            "permissions": {
                "is_admin": admin,
                "can_edit": can_edit_order(request.user, order_list),
                "can_finalize": admin and order_list.status == OrderList.Status.DRAFT,
                "can_delete": admin,
            },
        })

    def patch(self, request, pk):
        order_list = get_object_or_404(OrderList, pk=pk)
        if not can_edit_order(request.user, order_list):
            return Response({"detail": "This finalized list is read-only."}, status=403)
        serializer = OrderListSummarySerializer(order_list, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not is_admin(request.user):
            return Response({"detail": "Administrator access required."}, status=403)
        get_object_or_404(OrderList, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrderListFinalizeAPIView(APIView):
    def post(self, request, pk):
        if not is_admin(request.user):
            return Response({"detail": "Administrator access required."}, status=403)
        order_list = get_object_or_404(OrderList, pk=pk)
        if order_list.status != OrderList.Status.DRAFT:
            return Response({"detail": "Only a draft list can be finalized."}, status=409)
        order_list.status = OrderList.Status.FINALIZED
        order_list.save(update_fields=["status", "updated_at"])
        return Response(OrderListSummarySerializer(order_list).data)


class ProductCategoryPreferenceAPIView(APIView):
    grid_key = "product-filter-categories"

    def get(self, request):
        preference = UserGridPreference.objects.filter(user=request.user, grid_key=self.grid_key).first()
        return Response(preference.filter_state if preference else {"hidden": [], "custom": [], "overrides": {}})

    def put(self, request):
        hidden = request.data.get("hidden", [])
        custom = request.data.get("custom", [])
        overrides = request.data.get("overrides", {})
        if not isinstance(hidden, list) or not isinstance(custom, list) or not isinstance(overrides, dict) or len(custom) > 50:
            return Response({"detail": "Invalid category settings."}, status=400)
        cleaned = []
        for index, category in enumerate(custom):
            if not isinstance(category, dict):
                return Response({"detail": "Invalid category."}, status=400)
            label = str(category.get("label", "")).strip()[:60]
            terms = str(category.get("terms", "")).strip()[:200]
            group = str(category.get("group", "Custom")).strip()[:60] or "Custom"
            if not label or not terms:
                return Response({"detail": "Category name and matching words are required."}, status=400)
            cleaned.append({"id": str(category.get("id") or f"custom-{index}")[:80], "label": label, "group": group, "terms": terms})
        cleaned_overrides = {}
        for category_id, terms in list(overrides.items())[:100]:
            category_id = str(category_id).strip()[:80]
            terms = str(terms).strip()[:500]
            if category_id and terms:
                cleaned_overrides[category_id] = terms
        payload = {"hidden": [str(value)[:80] for value in hidden[:100]], "custom": cleaned, "overrides": cleaned_overrides}
        preference, _ = UserGridPreference.objects.get_or_create(user=request.user, grid_key=self.grid_key)
        preference.filter_state = payload
        preference.save(update_fields=["filter_state", "updated_at"])
        return Response(payload)


class OrderItemCreateAPIView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        order_list = get_object_or_404(OrderList, pk=pk)
        if not can_edit_order(request.user, order_list):
            return Response({"detail": "This finalized list is read-only."}, status=403)
        product = get_object_or_404(available_products(), pk=request.data.get("product_id"))
        if request.data.get("refresh_stock", True):
            try:
                refresh_product_stocks(product)
            except Exception:
                pass
        stock = ProductStock.objects.filter(product=product, store=order_list.store).first()
        need = ProductMonthlyNeed.objects.filter(
            product=product, store=order_list.store, month=month_start()
        ).first()
        stock_value = stock.actual if stock else 0
        need_value = need.needed_quantity if need else 0
        on_shelf = decimal_value(request.data.get("on_shelf_quantity", 0))
        if on_shelf < 0 or on_shelf != on_shelf.to_integral_value():
            return Response({"detail": "On-shelf quantity must be a whole, non-negative number."}, status=400)
        on_shelf = int(on_shelf)
        item = order_list.items.filter(product=product).first()
        created = item is None
        if item:
            order_list.items.exclude(pk=item.pk).update(row_order=F("row_order") + 1)
            item.current_stock_snapshot = stock_value
            item.monthly_needed_snapshot = need_value
            item.on_shelf_quantity = on_shelf
            item.row_order = 0
            item.updated_by = request.user
            item.save(update_fields=["current_stock_snapshot", "monthly_needed_snapshot", "on_shelf_quantity", "row_order", "updated_by", "updated_at"])
        else:
            # Make space for a newly captured product at the top of the list.
            order_list.items.update(row_order=F("row_order") + 1)
            item = OrderListItem.objects.create(
                order_list=order_list,
                product=product,
                current_stock_snapshot=stock_value,
                monthly_needed_snapshot=need_value,
                on_shelf_quantity=on_shelf,
                row_order=0,
                created_by=request.user,
                updated_by=request.user,
            )
        serializer = OrderListItemSerializer(item, context=item_context(order_list, request))
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class OrderItemBulkCreateAPIView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        order_list = get_object_or_404(OrderList.objects.select_for_update(), pk=pk)
        if not can_edit_order(request.user, order_list):
            return Response({"detail": "This finalized list is read-only."}, status=403)
        raw_ids = request.data.get("product_ids")
        if not isinstance(raw_ids, list):
            return Response({"product_ids": "Use a list of product IDs."}, status=400)
        try:
            product_ids = list(dict.fromkeys(int(value) for value in raw_ids))
        except (TypeError, ValueError):
            return Response({"product_ids": "Every product ID must be an integer."}, status=400)
        if not product_ids or len(product_ids) > 100:
            return Response({"product_ids": "Select between 1 and 100 products."}, status=400)

        products = {product.id: product for product in available_products().filter(id__in=product_ids)}
        missing = [product_id for product_id in product_ids if product_id not in products]
        if missing:
            return Response({"product_ids": f"Unavailable products: {', '.join(map(str, missing))}."}, status=400)

        existing_ids = set(
            order_list.items.filter(product_id__in=product_ids).values_list("product_id", flat=True)
        )
        new_ids = [product_id for product_id in product_ids if product_id not in existing_ids]
        stocks = dict(
            ProductStock.objects.filter(store=order_list.store, product_id__in=new_ids).values_list(
                "product_id", "actual"
            )
        )
        needs = dict(
            ProductMonthlyNeed.objects.filter(
                store=order_list.store, product_id__in=new_ids, month=month_start()
            ).values_list("product_id", "needed_quantity")
        )
        # Batch additions use the same newest-first ordering as single scans.
        order_list.items.update(row_order=F("row_order") + len(new_ids))
        OrderListItem.objects.bulk_create(
            [
                OrderListItem(
                    order_list=order_list,
                    product=products[product_id],
                    current_stock_snapshot=stocks.get(product_id, 0),
                    monthly_needed_snapshot=needs.get(product_id, 0),
                    on_shelf_quantity=0,
                    row_order=index,
                    created_by=request.user,
                    updated_by=request.user,
                )
                for index, product_id in enumerate(new_ids)
            ]
        )
        items = order_list.items.filter(product_id__in=product_ids).select_related(
            "product", "order_list__store"
        ).prefetch_related("transfers__from_store")
        return Response(
            {
                "created": len(new_ids),
                "existing": len(existing_ids),
                "items": OrderListItemSerializer(
                    items, many=True, context=item_context(order_list, request)
                ).data,
            },
            status=status.HTTP_201_CREATED if new_ids else status.HTTP_200_OK,
        )


class OrderItemDetailAPIView(APIView):
    def patch(self, request, pk):
        item = get_object_or_404(OrderListItem.objects.select_related("order_list", "product"), pk=pk)
        if not can_edit_order(request.user, item.order_list):
            return Response({"detail": "This finalized list is read-only."}, status=403)
        if not is_admin(request.user):
            allowed = {"on_shelf_quantity", "notes"}
            disallowed = set(request.data) - allowed
            if disallowed:
                return Response({"detail": "Only shelf quantity and notes can be changed."}, status=403)
        serializer = OrderListItemSerializer(
            item, data=request.data, partial=True, context=item_context(item.order_list, request)
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        item = get_object_or_404(OrderListItem.objects.select_related("order_list"), pk=pk)
        if not can_edit_order(request.user, item.order_list):
            return Response({"detail": "This finalized list is read-only."}, status=403)
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductSearchAPIView(APIView):
    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        if not query:
            return Response([])
        # Correct common catalog shorthand/typos before doing a literal search.
        # Search each word independently so queries such as "99 bananas" also
        # match catalog names with words between them and every bottle-size suffix.
        normalized = normalize_search_text(query).replace("pinotnior", "pinotnoir")
        query_words = [
            normalize_search_text(word)
            for word in query.split()
            if normalize_search_text(word)
        ]
        if normalized == "pinotnoir":
            query_words = [normalized]
        if not query_words:
            query_words = [normalized]
        query_word_variants = [_search_word_variants(word) for word in query_words]
        barcode_variants = {normalized}
        if normalized.isdigit() and 7 <= len(normalized) <= 15:
            stripped = normalized.lstrip("0") or "0"
            barcode_variants.update({stripped, f"0{normalized}", f"0{stripped}"})
            if normalized.endswith("0") and len(normalized) > 7:
                without_trailing = normalized[:-1]
                barcode_variants.update({without_trailing, without_trailing.lstrip("0") or "0"})
            if len(normalized) < 15:
                barcode_variants.add(f"{normalized}0")
        products = available_products().prefetch_related("codes")
        exact = Q(number__iexact=query) | Q(codes__normalized_code__in=barcode_variants)
        name_words = Q()
        for variants in query_word_variants:
            word_match = Q()
            for variant in variants:
                word_match |= Q(normalized_name__icontains=variant)
            name_words &= word_match
        literal_query = exact | name_words
        if not query.isdigit():
            literal_query |= Q(number__icontains=query)
        literal = products.filter(literal_query).distinct()
        if literal.exists():
            result_products = list(literal[:5000])
            result_products.sort(
                key=lambda product: _product_search_rank(
                    product, normalized, query_word_variants, barcode_variants
                )
            )
            result_products = result_products[:1000]
        elif connection.vendor == "postgresql" and len(normalized) >= 3:
            result_products = list(
                products.annotate(similarity=TrigramSimilarity("normalized_name", normalized))
                .filter(similarity__gte=0.3)
                .distinct()
                .order_by("-similarity", "name")[:50]
            )
        else:
            result_products = []
        stock_map = {}
        need_map = {}
        order_id = request.query_params.get("order_id")
        if order_id:
            store_id = OrderList.objects.filter(pk=order_id).values_list("store_id", flat=True).first()
            if store_id:
                stale_before = timezone.now() - timedelta(seconds=90)
                stale_products = [
                    product
                    for product in result_products[:12]
                    if not product.stock_last_synced_at or product.stock_last_synced_at < stale_before
                ]
                if stale_products:
                    with ThreadPoolExecutor(max_workers=min(6, len(stale_products))) as executor:
                        list(executor.map(_refresh_search_stock, stale_products))
                stock_map = dict(
                    ProductStock.objects.filter(
                        store_id=store_id, product_id__in=[product.id for product in result_products]
                    ).values_list("product_id", "actual")
                )
                need_map = dict(
                    ProductMonthlyNeed.objects.filter(
                        store_id=store_id,
                        product_id__in=[product.id for product in result_products],
                        month=month_start(),
                    ).values_list("product_id", "needed_quantity")
                )
        return Response(
            ProductSearchSerializer(
                result_products,
                many=True,
                context={"stock_map": stock_map, "need_map": need_map},
            ).data
        )


def _search_word_variants(word):
    """Return conservative singular alternatives while retaining the typed word."""
    variants = {word}
    if word.isalpha() and len(word) > 3:
        if word.endswith("ies") and len(word) > 4:
            variants.add(f"{word[:-3]}y")
        if word.endswith("es") and len(word) > 4:
            variants.add(word[:-2])
        if word.endswith("s"):
            variants.add(word[:-1])
    return tuple(sorted(variants, key=lambda value: (-len(value), value)))


def _product_search_rank(product, normalized_query, query_word_variants, barcode_variants):
    """Put exact and naturally ordered name matches ahead of loose token matches."""
    name = product.normalized_name or normalize_search_text(product.name)
    number = normalize_search_text(product.number)
    codes = {code.normalized_code or normalize_search_text(code.code) for code in product.codes.all()}
    exact_identifier = number == normalized_query or bool(codes & barcode_variants)
    phrase_at = name.find(normalized_query)
    word_positions = [
        min((position for word in variants if (position := name.find(word)) >= 0), default=-1)
        for variants in query_word_variants
    ]
    words_in_order = all(
        left <= right
        for left, right in zip(word_positions, word_positions[1:])
    )
    first_word_at = word_positions[0] if word_positions else len(name)
    return (
        0 if exact_identifier else 1,
        0 if name == normalized_query else 1,
        0 if phrase_at == 0 else 1,
        0 if phrase_at >= 0 else 1,
        0 if words_in_order else 1,
        first_word_at if first_word_at >= 0 else len(name),
        len(name),
        product.name.casefold(),
    )


def _refresh_search_stock(product):
    close_old_connections()
    try:
        refresh_product_stocks(product)
    except Exception:
        return False
    finally:
        close_old_connections()
    return True


class ProductAvailabilityAPIView(APIView):
    def get(self, request, pk):
        product = get_object_or_404(available_products(), pk=pk)
        order_list = get_object_or_404(OrderList.objects.select_related("store"), pk=request.query_params.get("order_id"))
        refresh_error = ""
        try:
            refresh_product_stocks(product)
        except Exception as exc:
            refresh_error = str(exc)
        stores = list(Store.objects.filter(active=True).order_by("number", "name"))
        stocks = {row.store_id: float(row.actual) for row in ProductStock.objects.filter(product=product)}
        needs = {
            row.store_id: float(row.needed_quantity)
            for row in ProductMonthlyNeed.objects.filter(product=product, month=month_start())
        }
        payload = {
                "product_id": product.id,
                "current_stock": stocks.get(order_list.store_id, 0),
                "monthly_needed": needs.get(order_list.store_id, 0),
                "stale": bool(refresh_error),
                "refresh_error": refresh_error,
        }
        if is_admin(request.user):
            payload["stores"] = [
                    {
                        "store_id": store.id,
                        "store_name": store.name,
                        "stock": stocks.get(store.id, 0),
                        "monthly_needed": needs.get(store.id, 0),
                    }
                    for store in stores
                ]
        return Response(payload)


class ProductPreferredSupplierAPIView(APIView):
    def patch(self, request, pk):
        if not is_admin(request.user):
            return Response({"detail": "Only an admin can choose a preferred supplier."}, status=403)
        product = get_object_or_404(available_products(), pk=pk)
        supplier_id = str(request.data.get("supplier_id") or "")
        valid_ids = {row["id"] for row in product_supplier_options(product)}
        if supplier_id not in valid_ids:
            return Response({"supplier_id": "Choose a supplier available for this product."}, status=400)
        product.preferred_supplier_id = supplier_id
        product.save(update_fields=["preferred_supplier_id", "updated_at"])
        return Response(product_supplier_data(product))


class InventoryComparisonAPIView(APIView):
    def post(self, request):
        raw_ids = request.data.get("product_ids", [])
        if not isinstance(raw_ids, list):
            return Response({"product_ids": "Use a list of product IDs."}, status=400)
        try:
            product_ids = list(dict.fromkeys(int(value) for value in raw_ids))
        except (TypeError, ValueError):
            return Response({"product_ids": "Every product ID must be an integer."}, status=400)
        if not product_ids or len(product_ids) > 100:
            return Response({"product_ids": "Select between 1 and 100 products."}, status=400)
        products = list(available_products().filter(id__in=product_ids))
        product_map = {product.id: product for product in products}
        products = [product_map[product_id] for product_id in product_ids if product_id in product_map]
        stores = list(Store.objects.filter(active=True).order_by("number", "name"))
        stocks = {
            (row.product_id, row.store_id): float(row.actual)
            for row in ProductStock.objects.filter(product_id__in=product_ids, store__in=stores)
        }
        needs = {
            (row.product_id, row.store_id): float(row.needed_quantity)
            for row in ProductMonthlyNeed.objects.filter(
                product_id__in=product_ids, store__in=stores, month=month_start()
            )
        }
        return Response({
            "stores": StoreSerializer(stores, many=True).data,
            "products": [{
                "id": product.id,
                "number": product.number,
                "name": product.name,
                "stores": [{
                    "store_id": store.id,
                    "stock": stocks.get((product.id, store.id), 0),
                    "monthly_needed": needs.get((product.id, store.id), 0),
                } for store in stores],
            } for product in products],
        })


def bulk_order_summary(row):
    return {"id": row.id, "name": row.name, "notes": row.notes, "item_count": row.item_count if hasattr(row, "item_count") else row.items.count(), "total_cases": float(getattr(row, "total_cases", 0) or 0), "updated_at": row.updated_at}


class BulkOrderListAPIView(APIView):
    permission_classes = [IsOrderAdmin]

    def get(self, request):
        rows = BulkOrderList.objects.annotate(item_count=Count("items", distinct=True), total_cases=Sum("items__quantities__cases"))
        return Response([bulk_order_summary(row) for row in rows])

    def post(self, request):
        name = str(request.data.get("name", "")).strip()[:160]
        if not name: return Response({"name": "Enter a list name."}, status=400)
        row = BulkOrderList.objects.create(name=name, created_by=request.user)
        return Response(bulk_order_summary(row), status=201)


class BulkOrderDetailAPIView(APIView):
    permission_classes = [IsOrderAdmin]

    def get(self, request, pk):
        row = get_object_or_404(BulkOrderList, pk=pk)
        stores = list(Store.objects.filter(active=True).order_by("number", "name"))
        items = list(row.items.select_related("product").prefetch_related("quantities"))
        product_ids = [item.product_id for item in items]
        stocks = {(value.product_id, value.store_id): float(value.actual) for value in ProductStock.objects.filter(product_id__in=product_ids, store__in=stores)}
        needs = {(value.product_id, value.store_id): float(value.needed_quantity) for value in ProductMonthlyNeed.objects.filter(product_id__in=product_ids, store__in=stores, month=month_start())}
        return Response({"bulk_order": bulk_order_summary(row), "stores": StoreSerializer(stores, many=True).data, "items": [{"id": item.id, "product_id": item.product_id, "product_number": item.product.number, "product_name": item.product.name, "stores": [{"store_id": store.id, "stock": stocks.get((item.product_id, store.id), 0), "monthly_needed": needs.get((item.product_id, store.id), 0), "cases": next((float(q.cases) for q in item.quantities.all() if q.store_id == store.id), 0)} for store in stores]} for item in items]})

    def delete(self, request, pk):
        get_object_or_404(BulkOrderList, pk=pk).delete(); return Response(status=204)

    def patch(self, request, pk):
        row = get_object_or_404(BulkOrderList, pk=pk)
        if "notes" in request.data: row.notes = str(request.data["notes"])[:5000]
        row.save(); return Response(bulk_order_summary(row))


class BulkOrderItemsAPIView(APIView):
    permission_classes = [IsOrderAdmin]

    @transaction.atomic
    def post(self, request, pk):
        row = get_object_or_404(BulkOrderList.objects.select_for_update(), pk=pk)
        try: product_ids = list(dict.fromkeys(int(value) for value in request.data.get("product_ids", [])))
        except (TypeError, ValueError): return Response({"product_ids": "Use product IDs."}, status=400)
        if not product_ids or len(product_ids) > 100: return Response({"product_ids": "Select between 1 and 100 products."}, status=400)
        existing = set(row.items.filter(product_id__in=product_ids).values_list("product_id", flat=True))
        valid = set(available_products().filter(id__in=product_ids).values_list("id", flat=True))
        start = (row.items.aggregate(value=Max("row_order"))["value"] or 0) + 1
        BulkOrderItem.objects.bulk_create([BulkOrderItem(bulk_order=row, product_id=product_id, row_order=start + index) for index, product_id in enumerate(product_ids) if product_id in valid and product_id not in existing])
        row.save(update_fields=["updated_at"]); return Response({"created": len(valid - existing)})


class BulkOrderItemAPIView(APIView):
    permission_classes = [IsOrderAdmin]

    def patch(self, request, pk):
        item = get_object_or_404(BulkOrderItem, pk=pk)
        store = get_object_or_404(Store, pk=request.data.get("store_id"), active=True)
        cases = max(0, decimal_value(request.data.get("cases", 0)))
        quantity, _ = BulkOrderQuantity.objects.update_or_create(item=item, store=store, defaults={"cases": cases})
        item.bulk_order.save(update_fields=["updated_at"]); return Response({"store_id": store.id, "cases": float(quantity.cases)})

    def delete(self, request, pk):
        item = get_object_or_404(BulkOrderItem, pk=pk); bulk_order = item.bulk_order; item.delete(); bulk_order.save(update_fields=["updated_at"]); return Response(status=204)


class BulkOrderClearAPIView(APIView):
    permission_classes = [IsOrderAdmin]

    def post(self, request, pk):
        row = get_object_or_404(BulkOrderList, pk=pk); BulkOrderQuantity.objects.filter(item__bulk_order=row).update(cases=0); row.save(update_fields=["updated_at"]); return Response({"cleared": True})


class BulkOrderExportAPIView(APIView):
    permission_classes = [IsOrderAdmin]

    def get(self, request, pk, file_format):
        if file_format not in {"xlsx", "pdf"}: return Response(status=404)
        return bulk_order_export_response(get_object_or_404(BulkOrderList, pk=pk), file_format)


class GridPreferenceAPIView(APIView):
    def get(self, request, grid_key):
        preference, _ = UserGridPreference.objects.get_or_create(user=request.user, grid_key=grid_key)
        return Response(GridPreferenceSerializer(preference).data)

    def put(self, request, grid_key):
        preference, _ = UserGridPreference.objects.get_or_create(user=request.user, grid_key=grid_key)
        serializer = GridPreferenceSerializer(preference, data={**request.data, "grid_key": grid_key})
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data)


class ExportAPIView(APIView):
    def get(self, request, pk, kind, file_format):
        if kind not in EXPORT_KINDS or file_format not in {"xlsx", "pdf"}:
            return Response({"detail": "Unsupported export."}, status=404)
        order_list = get_object_or_404(OrderList.objects.select_related("store"), pk=pk)
        if not is_admin(request.user):
            return Response({"detail": "Administrator access required for exports."}, status=403)
        item_ids = None
        if "item_ids" in request.query_params:
            try:
                item_ids = {
                    int(value)
                    for value in request.query_params.get("item_ids", "").split(",")
                    if value.strip()
                }
            except ValueError:
                return Response({"detail": "Invalid item filter."}, status=400)
        exporter = xlsx_response if file_format == "xlsx" else pdf_response
        return exporter(order_list, kind, request.user.get_username(), item_ids)


def validated_grid_export_payload(data):
    raw_columns = data.get("columns")
    raw_rows = data.get("rows")
    if not isinstance(raw_columns, list) or not 1 <= len(raw_columns) <= 80:
        return None, None, None, Response({"columns": "Choose between 1 and 80 visible columns."}, status=400)
    if not isinstance(raw_rows, list) or len(raw_rows) > 5000:
        return None, None, None, Response({"rows": "The export supports up to 5,000 filtered rows."}, status=400)
    columns = []
    for column in raw_columns:
        if not isinstance(column, dict):
            return None, None, None, Response({"columns": "Every column must include an ID and label."}, status=400)
        try:
            width = max(45, min(float(column.get("width") or 100), 260))
        except (TypeError, ValueError):
            width = 100
        columns.append({
            "id": str(column.get("id") or "")[:80],
            "label": str(column.get("label") or column.get("id") or "")[:120],
            "width": width,
        })
    rows = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, list):
            return None, None, None, Response({"rows": "Every export row must be a list of cell values."}, status=400)
        rows.append([str(value if value is not None else "")[:1000] for value in raw_row[:len(columns)]])
    title = str(data.get("title") or "").strip()[:160]
    return columns, rows, title, None


class GridPDFExportAPIView(APIView):
    def post(self, request, pk):
        if not is_admin(request.user):
            return Response({"detail": "Administrator access required for exports."}, status=403)
        order_list = get_object_or_404(OrderList.objects.select_related("store"), pk=pk)
        orientation = request.data.get("orientation", "landscape")
        if orientation not in {"portrait", "landscape"}:
            return Response({"orientation": "Choose portrait or landscape."}, status=400)
        columns, rows, title, error = validated_grid_export_payload(request.data)
        if error:
            return error
        return grid_pdf_response(
            order_list, columns, rows, orientation, request.user.get_username(), title
        )


class GridXLSXExportAPIView(APIView):
    def post(self, request, pk):
        if not is_admin(request.user):
            return Response({"detail": "Administrator access required for exports."}, status=403)
        order_list = get_object_or_404(OrderList.objects.select_related("store"), pk=pk)
        columns, rows, title, error = validated_grid_export_payload(request.data)
        if error:
            return error
        return grid_xlsx_response(order_list, columns, rows, request.user.get_username(), title)


class ServicesAPIView(APIView):
    permission_classes = [IsSuperuser]

    def get(self, request):
        recover_interrupted_runs(expired_only=True)
        for name, (_, interval) in SERVICES.items():
            ServiceControl.objects.get_or_create(service_name=name, defaults={"interval_seconds": interval})
        controls = list(ServiceControl.objects.filter(service_name__in=SERVICES).order_by("service_name"))
        control_by_name = {row.service_name: row for row in controls}
        runs = list(
            SyncRun.objects.filter(job_name__in=SERVICES)
            .exclude(status=SyncRun.Status.SKIPPED)
            .order_by("-started_at")[:60]
        )
        latest_run_by_service = {
            name: SyncRun.objects.filter(job_name=name)
            .exclude(status=SyncRun.Status.SKIPPED)
            .order_by("-started_at")
            .first()
            for name in SERVICES
        }
        warehouse_stores = list(Store.objects.filter(active=True, is_warehouse=True).order_by("number", "name"))
        store_ids = [store.id for store in warehouse_stores]
        stock_states = {
            row.store_id: row
            for row in SyncState.objects.filter(entity="stocks_by_store", store_id__in=store_ids)
        }
        stock_counts = {
            row["store_id"]: row["total"]
            for row in ProductStock.objects.filter(store_id__in=store_ids)
            .values("store_id")
            .annotate(total=Count("id"))
        }
        stock_control = control_by_name["stocks"]
        stale_after_seconds = max(stock_control.interval_seconds * 2 + 60, 300)
        now = timezone.now()
        stock_stores = []
        for store in warehouse_stores:
            state = stock_states.get(store.id)
            age_seconds = (
                max(0, int((now - state.last_synced_at).total_seconds()))
                if state and state.last_synced_at
                else None
            )
            store_status = (
                "missing"
                if not state or not state.last_revision
                else "stale"
                if age_seconds is None or age_seconds > stale_after_seconds
                else "current"
            )
            stock_stores.append(
                {
                    "id": store.id,
                    "number": store.number,
                    "name": store.name,
                    "status": store_status,
                    "last_revision": state.last_revision if state else 0,
                    "last_synced_at": state.last_synced_at if state else None,
                    "age_seconds": age_seconds,
                    "stock_records": stock_counts.get(store.id, 0),
                }
            )
        stock_status_counts = {
            status_name: sum(row["status"] == status_name for row in stock_stores)
            for status_name in ("current", "stale", "missing")
        }
        latest_stock_run = SyncRun.objects.filter(
            job_name="stocks", status=SyncRun.Status.SUCCESS
        ).order_by("-started_at").first()
        latest_reconciliation = SyncRun.objects.filter(
            job_name="stock_reconciliation", status=SyncRun.Status.SUCCESS
        ).order_by("-started_at").first()
        stock_health = (
            "error"
            if stock_control.status == ServiceControl.Status.ERROR
            else "warning"
            if stock_status_counts["stale"] or stock_status_counts["missing"]
            else "healthy"
        )
        show_inactive_products = SystemSetting.objects.filter(
            key="show_inactive_products", value=True
        ).exists()
        active_product_count = Product.objects.filter(active=True).count()
        inactive_product_count = Product.objects.filter(active=False).count()
        since = now - timedelta(hours=24)
        daily_run_totals = SyncRun.objects.filter(
            job_name__in=SERVICES,
            status=SyncRun.Status.SUCCESS,
            finished_at__gte=since,
        ).aggregate(
            seen=Sum("records_seen"),
        )
        non_stock_changes = SyncRun.objects.filter(
            job_name__in=set(SERVICES) - {"stocks", "stock_reconciliation"},
            status=SyncRun.Status.SUCCESS,
            finished_at__gte=since,
        ).aggregate(created=Sum("records_created"), updated=Sum("records_updated"))
        # Older stock jobs counted every verified row as an update. Only include
        # stock runs produced by the change-aware importer so this dashboard does
        # not describe a cache verification as tens of thousands of changes.
        stock_changes = SyncRun.objects.filter(
            job_name__in={"stocks", "stock_reconciliation"},
            status=SyncRun.Status.SUCCESS,
            finished_at__gte=since,
            metrics__has_key="unchanged",
        ).aggregate(created=Sum("records_created"), updated=Sum("records_updated"))
        records_changed_24h = sum(
            (totals["created"] or 0) + (totals["updated"] or 0)
            for totals in (non_stock_changes, stock_changes)
        )
        service_statuses = [row.status for row in controls]
        attention = [
            {
                "name": row.service_name,
                "status": row.status,
                "message": row.last_error or "This job needs attention.",
                "latest_run": serialize_sync_run(latest_run_by_service.get(row.service_name)),
            }
            for row in controls
            if row.status == ServiceControl.Status.ERROR
        ]
        api_requests_24h = ApiRequestLog.objects.filter(created_at__gte=since)
        api_summary = api_requests_24h.aggregate(
            requests=Count("id"),
            errors=Count("id", filter=Q(status_code__gte=400)),
            average_ms=Avg("latency_ms"),
            slowest_ms=Max("latency_ms"),
        )
        endpoint_health = list(
            api_requests_24h.values("method", "url_path")
            .annotate(
                requests=Count("id"),
                errors=Count("id", filter=Q(status_code__gte=400)),
                average_ms=Avg("latency_ms"),
                slowest_ms=Max("latency_ms"),
            )
            .order_by("-requests", "url_path")[:20]
        )
        return Response(
            {
                "can_manage": True,
                "overview": {
                    "services_total": len(controls),
                    "healthy": service_statuses.count(ServiceControl.Status.IDLE),
                    "disabled": service_statuses.count(ServiceControl.Status.DISABLED),
                    "running": sum(status in {ServiceControl.Status.QUEUED, ServiceControl.Status.RUNNING} for status in service_statuses),
                    "errors": service_statuses.count(ServiceControl.Status.ERROR),
                    "records_checked_24h": daily_run_totals["seen"] or 0,
                    "records_changed_24h": records_changed_24h,
                    "api_errors_24h": ApiRequestLog.objects.filter(created_at__gte=since, status_code__gte=400).count(),
                    "attention": attention,
                },
                "counts": {
                    "active_stores": len(warehouse_stores),
                    "products": Product.objects.count(),
                    "stock_records": ProductStock.objects.count(),
                    "thirty_day_totals": ProductMonthlyNeed.objects.filter(month=month_start()).count(),
                },
                "product_visibility": {
                    "show_inactive": show_inactive_products,
                    "active_count": active_product_count,
                    "inactive_count": inactive_product_count,
                },
                "stock_sync": {
                    "health": stock_health,
                    "enabled": stock_control.enabled,
                    "interval_seconds": stock_control.interval_seconds,
                    "stale_after_seconds": stale_after_seconds,
                    "page_size": settings.KORONA_STOCK_PAGE_SIZE,
                    "stores_total": len(stock_stores),
                    **stock_status_counts,
                    "latest_incremental": {
                        "finished_at": latest_stock_run.finished_at,
                        "duration_ms": latest_stock_run.duration_ms,
                        "seen": latest_stock_run.records_seen,
                        "changed": latest_stock_run.records_created + latest_stock_run.records_updated
                        if "unchanged" in (latest_stock_run.metrics or {})
                        else None,
                        "unchanged": (latest_stock_run.metrics or {}).get("unchanged"),
                        "change_breakdown_available": "unchanged" in (latest_stock_run.metrics or {}),
                    }
                    if latest_stock_run
                    else None,
                    "latest_reconciliation": {
                        "finished_at": latest_reconciliation.finished_at,
                        "duration_ms": latest_reconciliation.duration_ms,
                        "seen": latest_reconciliation.records_seen,
                        "changed": latest_reconciliation.records_created + latest_reconciliation.records_updated
                        if "unchanged" in (latest_reconciliation.metrics or {})
                        else None,
                        "unchanged": (latest_reconciliation.metrics or {}).get("unchanged"),
                        "change_breakdown_available": "unchanged" in (latest_reconciliation.metrics or {}),
                    }
                    if latest_reconciliation
                    else None,
                    "nightly_schedule": (
                        f"{settings.KORONA_STOCK_RECONCILE_HOUR:02d}:"
                        f"{settings.KORONA_STOCK_RECONCILE_MINUTE:02d} {settings.TIME_ZONE}"
                    ),
                    "stores": stock_stores,
                },
                "services": [
                    {
                        "name": row.service_name,
                        "enabled": row.enabled,
                        "status": row.status,
                        "interval_seconds": row.interval_seconds,
                        "last_run_at": row.last_run_at,
                        "next_run_at": row.next_run_at,
                        "last_error": row.last_error,
                        "fixed_schedule": row.service_name in {"stock_reconciliation", "monthly_reconciliation"},
                        "manual_only": False,
                        "schedule_label": (
                            f"Daily at {settings.KORONA_STOCK_RECONCILE_HOUR:02d}:"
                            f"{settings.KORONA_STOCK_RECONCILE_MINUTE:02d} "
                            f"{settings.TIME_ZONE}"
                            if row.service_name == "stock_reconciliation"
                            else f"Daily at {settings.KORONA_MONTHLY_RECONCILE_HOUR:02d}:"
                            f"{settings.KORONA_MONTHLY_RECONCILE_MINUTE:02d} "
                            f"{settings.TIME_ZONE}"
                            if row.service_name == "monthly_reconciliation"
                            else ""
                        ),
                        "description": {
                            "stocks": "Polls each store revision cursor and applies only changed stock rows.",
                            "stock_reconciliation": "Downloads every store stock record and repairs cache drift.",
                            "receipts": "Imports receipt revisions and recalculates affected rolling 30-day totals.",
                            "monthly_reconciliation": "Downloads the complete rolling 30-day receipt window from KORONA, replaces stale daily data, then recalculates every product total once.",
                            "products": "Imports changed products, barcodes, suppliers and commodity groups.",
                            "stores": "Imports changed organizational units and warehouse settings.",
                        }.get(row.service_name, ""),
                        "latest_run": serialize_sync_run(latest_run_by_service.get(row.service_name)),
                    }
                    for row in controls
                ],
                "runs": [serialize_sync_run(row) for row in runs],
                "api_health": {
                    "requests_24h": api_summary["requests"] or 0,
                    "errors_24h": api_summary["errors"] or 0,
                    "average_ms": round(api_summary["average_ms"] or 0),
                    "slowest_ms": api_summary["slowest_ms"] or 0,
                    "slow_requests_24h": api_requests_24h.filter(latency_ms__gte=1000).count(),
                    "endpoints": endpoint_health,
                },
                "api_latency": list(
                    ApiRequestLog.objects.order_by("-created_at").values(
                        "method", "url_path", "status_code", "latency_ms", "created_at"
                    )[:60]
                ),
                "logs": list(
                    SystemLog.objects.values("level", "source", "message", "context", "created_at")[:60]
                ),
            }
        )

    def patch(self, request):
        if "show_inactive_products" in request.data:
            show_inactive = request.data["show_inactive_products"]
            if not isinstance(show_inactive, bool):
                return Response(
                    {"show_inactive_products": "Use true or false."}, status=400
                )
            SystemSetting.objects.update_or_create(
                key="show_inactive_products",
                defaults={"value": show_inactive, "updated_by": request.user},
            )
            return Response({"show_inactive_products": show_inactive})
        service_name = request.data.get("service_name")
        if service_name not in SERVICES:
            return Response({"detail": "Unknown service."}, status=404)
        control, _ = ServiceControl.objects.get_or_create(
            service_name=service_name,
            defaults={"interval_seconds": SERVICES[service_name][1]},
        )
        if "enabled" in request.data:
            if not isinstance(request.data["enabled"], bool):
                return Response({"enabled": "Use true or false."}, status=400)
            control.enabled = bool(request.data["enabled"])
            control.status = ServiceControl.Status.IDLE if control.enabled else ServiceControl.Status.DISABLED
        if "interval_seconds" in request.data:
            if control.service_name in {"stock_reconciliation", "monthly_reconciliation"}:
                return Response({"interval_seconds": "This service uses a fixed schedule."}, status=400)
            try:
                interval_seconds = int(request.data["interval_seconds"])
            except (TypeError, ValueError):
                return Response({"interval_seconds": "Enter a whole number of seconds."}, status=400)
            if not 30 <= interval_seconds <= 86400:
                return Response({"interval_seconds": "Choose between 30 and 86400 seconds."}, status=400)
            control.interval_seconds = interval_seconds
        control.updated_by = request.user
        control.save()
        return Response({"ok": True})


class ServiceRunAPIView(APIView):
    permission_classes = [IsSuperuser]

    def post(self, request, service_name):
        if service_name not in SERVICES:
            return Response({"detail": "Unknown service."}, status=404)
        control, _ = ServiceControl.objects.get_or_create(
            service_name=service_name,
            defaults={"interval_seconds": SERVICES[service_name][1]},
        )
        if control.status in {ServiceControl.Status.QUEUED, ServiceControl.Status.RUNNING}:
            return Response({"detail": "This job is already queued or running."}, status=409)
        from .tasks import (
            reconcile_stocks_task,
            sync_products_task,
            sync_receipts_task,
            reconcile_monthly_totals_task,
            sync_stocks_task,
            sync_stores_task,
        )

        control.status = ServiceControl.Status.QUEUED
        control.last_error = ""
        control.save(update_fields=["status", "last_error", "updated_at"])
        try:
            task = {
                "stores": sync_stores_task,
                "products": sync_products_task,
                "stocks": sync_stocks_task,
                "stock_reconciliation": reconcile_stocks_task,
                "receipts": sync_receipts_task,
                "monthly_reconciliation": reconcile_monthly_totals_task,
            }[service_name].delay(force=True)
        except Exception as exc:
            control.status = ServiceControl.Status.ERROR
            control.last_error = f"Could not queue job: {exc}"[:4000]
            control.save(update_fields=["status", "last_error", "updated_at"])
            return Response({"detail": control.last_error}, status=503)
        return Response({"queued": True, "task_id": task.id}, status=202)


def serialize_sync_run(row):
    if row is None:
        return None
    metrics = row.metrics or {
        "seen": row.records_seen,
        "created": row.records_created,
        "updated": row.records_updated,
    }
    return {
        "id": row.id,
        "job_name": row.job_name,
        "status": row.status,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "duration_ms": row.duration_ms,
        "seen": row.records_seen,
        "created": row.records_created,
        "updated": row.records_updated,
        "metrics": metrics,
        "error": row.error_message,
    }


def serialize_user(user):
    return {
        "id": user.id,
        "username": user.get_username(),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "is_admin": user.is_staff or user.is_superuser,
        "is_superuser": user.is_superuser,
        "is_active": user.is_active,
        "last_login": user.last_login,
    }


class UserListAPIView(APIView):
    permission_classes = [IsOrderAdmin]

    def get(self, request):
        users = get_user_model().objects.order_by("-is_superuser", "-is_staff", "username")
        return Response([serialize_user(user) for user in users])

    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        password = str(request.data.get("password", ""))
        if not username:
            return Response({"username": "Enter a username."}, status=400)
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            return Response({"username": "That username is already in use."}, status=400)
        candidate = User(username=username)
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            return Response({"password": list(exc.messages)}, status=400)
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=str(request.data.get("first_name", "")).strip()[:150],
            last_name=str(request.data.get("last_name", "")).strip()[:150],
            email=str(request.data.get("email", "")).strip(),
            is_staff=bool(request.data.get("is_admin", False)),
        )
        return Response(serialize_user(user), status=201)


class UserDetailAPIView(APIView):
    permission_classes = [IsOrderAdmin]

    def patch(self, request, pk):
        user = get_object_or_404(get_user_model(), pk=pk)
        if user.is_superuser:
            return Response({"detail": "The super user is protected and cannot be changed here."}, status=403)
        if user.pk == request.user.pk and any(key in request.data for key in ("is_admin", "is_active")):
            return Response({"detail": "You cannot remove your own access."}, status=403)
        if "is_admin" in request.data:
            user.is_staff = bool(request.data["is_admin"])
        if "is_active" in request.data:
            user.is_active = bool(request.data["is_active"])
        for field in ("first_name", "last_name", "email"):
            if field in request.data:
                setattr(user, field, str(request.data[field]).strip())
        if request.data.get("password"):
            try:
                validate_password(str(request.data["password"]), user=user)
            except ValidationError as exc:
                return Response({"password": list(exc.messages)}, status=400)
            user.set_password(str(request.data["password"]))
        user.save()
        return Response(serialize_user(user))

    def delete(self, request, pk):
        user = get_object_or_404(get_user_model(), pk=pk)
        if user.is_superuser:
            return Response({"detail": "The super user cannot be removed."}, status=403)
        if user.pk == request.user.pk:
            return Response({"detail": "You cannot remove your own account."}, status=403)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class HealthAPIView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        Store.objects.exists()
        return Response({"status": "ok"})
