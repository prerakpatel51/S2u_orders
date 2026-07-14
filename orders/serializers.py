import re

from django.db import transaction
from rest_framework import serializers

from .models import (
    OrderItemTransfer,
    OrderList,
    OrderListItem,
    Product,
    ProductMonthlyNeed,
    ProductStock,
    Store,
    UserGridPreference,
)
from .utils import decimal_value


def number(value):
    return float(value or 0)


def supplier_short_name(name):
    name = " ".join(str(name or "").split())
    normalized = name.lower().replace("–", "-")
    aliases = [
        ("southern glazer", "Southern"),
        ("republic national", "RNDC"),
        ("green light", "GLDF"),
        ("blurack", "GLDF"),
        ("breakthru", "Breakthru"),
        ("premier beverage", "Breakthru"),
        ("florida distribution", "FDC"),
        ("carroll distributing", "Carroll"),
        ("southern eagle", "Southern Eagle"),
        ("united wholesale", "United"),
        ("jj taylor", "JJ Taylor"),
        ("mexcor", "Mexcor"),
        ("gold coast", "Gold Coast"),
        ("tuscany", "Tuscany"),
        ("coca cola", "Coca-Cola"),
        ("7 up", "7UP"),
        ("johnson brothers", "Johnson Bros"),
        ("consortium", "Consortium"),
        ("lottory", "Lottery"),
        ("international wine & spirits", "IWS"),
        ("cavalier", "Cavalier"),
    ]
    if normalized == "fdc":
        return "FDC"
    if normalized == "iws":
        return "IWS"
    if normalized in {"none", ""}:
        return "—"
    for phrase, alias in aliases:
        if phrase in normalized:
            return alias
    cleaned = re.sub(r"\s+(?:llc|inc\.?|company|co\.?)$", "", name, flags=re.IGNORECASE).strip()
    return cleaned if len(cleaned) <= 22 else cleaned[:21].rstrip() + "…"


def product_supplier_options(product):
    """Normalize KORONA supplierPrices into stable grid-friendly choices."""
    options = []
    seen = set()
    for row in (product.raw_data or {}).get("supplierPrices") or []:
        supplier = row.get("supplier") or {}
        supplier_id = str(supplier.get("id") or "")
        if not supplier_id or supplier_id in seen:
            continue
        seen.add(supplier_id)
        options.append(
            {
                "id": supplier_id,
                "name": supplier.get("name") or supplier.get("number") or "Unknown supplier",
                "short_name": supplier_short_name(
                    supplier.get("name") or supplier.get("number") or "Unknown supplier"
                ),
                "number": str(supplier.get("number") or ""),
                "order_code": str(row.get("orderCode") or ""),
                "pack_size": number(row.get("containerSize")),
                "purchase_price": number(row.get("value")),
            }
        )
    return options


def product_supplier_data(product):
    options = product_supplier_options(product)
    preferred_id = str(product.preferred_supplier_id or "")
    selected = next((row for row in options if row["id"] == preferred_id), None)
    selected = selected or (options[0] if options else {})
    return {
        "supplier_options": options,
        "preferred_supplier_id": selected.get("id", ""),
        "supplier_name": selected.get("short_name", ""),
        "supplier_full_name": selected.get("name", ""),
        "supplier_number": selected.get("number", ""),
        "supplier_order_code": selected.get("order_code", ""),
        "supplier_pack_size": selected.get("pack_size", 0),
        "supplier_purchase_price": selected.get("purchase_price", 0),
        "supplier_names": ", ".join(dict.fromkeys(row["short_name"] for row in options)),
    }


class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "korona_id", "number", "name", "active", "is_warehouse", "last_synced_at"]


class ProductSearchSerializer(serializers.ModelSerializer):
    codes = serializers.SerializerMethodField()
    current_stock = serializers.SerializerMethodField()
    monthly_needed = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "korona_id", "number", "name", "codes", "current_stock", "monthly_needed"]

    def get_codes(self, obj):
        return list(obj.codes.values_list("code", flat=True))

    def get_current_stock(self, obj):
        return number(self.context.get("stock_map", {}).get(obj.id, 0))

    def get_monthly_needed(self, obj):
        return number(self.context.get("need_map", {}).get(obj.id, 0))


class OrderListSummarySerializer(serializers.ModelSerializer):
    store = StoreSerializer(read_only=True)
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrderList
        fields = ["id", "store", "order_date", "status", "notes", "item_count", "created_at", "updated_at"]
        read_only_fields = ["id", "store", "order_date", "status", "item_count", "created_at", "updated_at"]


class OrderListItemSerializer(serializers.ModelSerializer):
    product_number = serializers.CharField(source="product.number", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    current_store_stock = serializers.SerializerMethodField()
    current_store_monthly_needed = serializers.SerializerMethodField()
    other_stores = serializers.SerializerMethodField()
    transfers = serializers.SerializerMethodField()
    supplier_options = serializers.SerializerMethodField()
    preferred_supplier_id = serializers.SerializerMethodField()
    supplier_name = serializers.SerializerMethodField()
    supplier_full_name = serializers.SerializerMethodField()
    supplier_number = serializers.SerializerMethodField()
    supplier_order_code = serializers.SerializerMethodField()
    supplier_pack_size = serializers.SerializerMethodField()
    supplier_purchase_price = serializers.SerializerMethodField()
    supplier_names = serializers.SerializerMethodField()
    commodity_group = serializers.SerializerMethodField()
    commodity_group_number = serializers.SerializerMethodField()

    class Meta:
        model = OrderListItem
        fields = [
            "id",
            "product",
            "product_number",
            "product_name",
            "supplier_options",
            "preferred_supplier_id",
            "supplier_name",
            "supplier_full_name",
            "supplier_number",
            "supplier_order_code",
            "supplier_pack_size",
            "supplier_purchase_price",
            "supplier_names",
            "commodity_group",
            "commodity_group_number",
            "on_shelf_quantity",
            "current_store_stock",
            "current_store_monthly_needed",
            "joe_quantity",
            "bt_quantity",
            "sqw_quantity",
            "notes",
            "row_order",
            "other_stores",
            "transfers",
            "updated_at",
        ]
        read_only_fields = ["product", "row_order"]

    def _stock_map(self):
        return self.context.get("stock_map", {})

    def _need_map(self):
        return self.context.get("need_map", {})

    def _supplier_data(self, obj):
        if not hasattr(self, "_supplier_cache"):
            self._supplier_cache = {}
        if obj.product_id not in self._supplier_cache:
            self._supplier_cache[obj.product_id] = product_supplier_data(obj.product)
        return self._supplier_cache[obj.product_id]

    def get_supplier_options(self, obj):
        return self._supplier_data(obj)["supplier_options"]

    def get_preferred_supplier_id(self, obj):
        return self._supplier_data(obj)["preferred_supplier_id"]

    def get_supplier_name(self, obj):
        return self._supplier_data(obj)["supplier_name"]

    def get_supplier_full_name(self, obj):
        return self._supplier_data(obj)["supplier_full_name"]

    def get_supplier_number(self, obj):
        return self._supplier_data(obj)["supplier_number"]

    def get_supplier_order_code(self, obj):
        return self._supplier_data(obj)["supplier_order_code"]

    def get_supplier_pack_size(self, obj):
        return self._supplier_data(obj)["supplier_pack_size"]

    def get_supplier_purchase_price(self, obj):
        return self._supplier_data(obj)["supplier_purchase_price"]

    def get_supplier_names(self, obj):
        return self._supplier_data(obj)["supplier_names"]

    def get_commodity_group(self, obj):
        group = (obj.product.raw_data or {}).get("commodityGroup") or {}
        return group.get("name") or group.get("number") or ""

    def get_commodity_group_number(self, obj):
        group = (obj.product.raw_data or {}).get("commodityGroup") or {}
        return str(group.get("number") or "")

    def get_current_store_stock(self, obj):
        return number(self._stock_map().get((obj.product_id, obj.order_list.store_id), obj.current_stock_snapshot))

    def get_current_store_monthly_needed(self, obj):
        return number(
            self._need_map().get((obj.product_id, obj.order_list.store_id), obj.monthly_needed_snapshot)
        )

    def get_other_stores(self, obj):
        current_id = obj.order_list.store_id
        return [
            {
                "store_id": store.id,
                "store_number": store.number,
                "store_name": store.name,
                "stock": number(self._stock_map().get((obj.product_id, store.id), 0)),
                "monthly_needed": number(self._need_map().get((obj.product_id, store.id), 0)),
            }
            for store in self.context.get("stores", [])
            if store.id != current_id
        ]

    def get_transfers(self, obj):
        return [
            {
                "id": transfer.id,
                "from_store_id": transfer.from_store_id,
                "from_store_number": transfer.from_store.number,
                "from_store_name": transfer.from_store.name,
                "quantity": number(transfer.quantity),
            }
            for transfer in obj.transfers.all()
        ]

    @transaction.atomic
    def update(self, instance, validated_data):
        request = self.context["request"]
        transfers = request.data.get("transfers")
        instance = super().update(instance, validated_data)
        instance.updated_by = request.user
        instance.save()
        if transfers is not None:
            valid_store_ids = set(Store.objects.filter(active=True).values_list("id", flat=True))
            instance.transfers.all().delete()
            rows = []
            for transfer in transfers:
                store_id = int(transfer.get("from_store_id", 0))
                quantity = decimal_value(transfer.get("quantity", 0))
                if store_id in valid_store_ids and store_id != instance.order_list.store_id and quantity > 0:
                    rows.append(OrderItemTransfer(item=instance, from_store_id=store_id, quantity=quantity))
            OrderItemTransfer.objects.bulk_create(rows)
        return instance


class GridPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserGridPreference
        fields = ["grid_key", "column_state", "filter_state", "sort_state"]
