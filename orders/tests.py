import uuid
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import load_workbook

from .models import (
    OrderItemTransfer,
    FullSyncJob,
    OrderList,
    OrderListItem,
    Product,
    ProductCode,
    ProductStock,
    ReceiptSaleLine,
    SalesDailySummary,
    Store,
)
from .services import _apply_receipt, recalculate_monthly_need, recalculate_stale_monthly_needs
from .full_sync import initialize_full_sync, run_full_sync_step
from .serializers import supplier_short_name


class ReceiptSyncTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(korona_id=uuid.uuid4(), number="1", name="Main")
        self.product = Product.objects.create(
            korona_id=uuid.uuid4(), number="100", name="Test Bottle", normalized_name="testbottle"
        )
        self.receipt_id = uuid.uuid4()

    def receipt(self, quantity, revision=1, cancelled=False):
        return {
            "id": str(self.receipt_id),
            "revision": revision,
            "bookingTime": "2026-07-10T12:00:00-04:00",
            "organizationalUnit": {"id": str(self.store.korona_id)},
            "cancelled": cancelled,
            "items": [{"product": {"id": str(self.product.korona_id)}, "quantity": quantity}],
        }

    def test_receipt_revision_applies_only_delta(self):
        affected, counts = set(), {"created": 0, "updated": 0}
        _apply_receipt(self.receipt(2), affected, counts)
        _apply_receipt(self.receipt(5, revision=2), affected, counts)
        summary = SalesDailySummary.objects.get(store=self.store, product=self.product)
        self.assertEqual(summary.quantity_sold, Decimal("5"))
        self.assertEqual(summary.receipts_count, 1)
        self.assertEqual(ReceiptSaleLine.objects.get().quantity, Decimal("5"))

    def test_cancelled_receipt_removes_previous_contribution(self):
        affected, counts = set(), {"created": 0, "updated": 0}
        _apply_receipt(self.receipt(3), affected, counts)
        _apply_receipt(self.receipt(3, revision=2, cancelled=True), affected, counts)
        summary = SalesDailySummary.objects.get(store=self.store, product=self.product)
        self.assertEqual(summary.quantity_sold, Decimal("0"))
        self.assertEqual(summary.receipts_count, 0)
        self.assertFalse(ReceiptSaleLine.objects.exists())

    def test_supplier_names_use_compact_catalog_aliases(self):
        self.assertEqual(supplier_short_name("Southern Glazer's Wine & Spirits of FL"), "Southern")
        self.assertEqual(supplier_short_name("Republic National Dist - Tampa"), "RNDC")
        self.assertEqual(supplier_short_name("Green Light Distribution Florida"), "GLDF")

    def test_older_backfill_revision_cannot_replace_newer_receipt(self):
        affected, counts = set(), {"created": 0, "updated": 0}
        _apply_receipt(self.receipt(5, revision=10), affected, counts)
        _apply_receipt(self.receipt(2, revision=9), affected, counts)
        summary = SalesDailySummary.objects.get(store=self.store, product=self.product)
        line = ReceiptSaleLine.objects.get()
        self.assertEqual(summary.quantity_sold, Decimal("5"))
        self.assertEqual(line.quantity, Decimal("5"))
        self.assertEqual(line.receipt_revision, 10)

    def test_monthly_need_is_exact_trailing_30_day_sales(self):
        today = timezone.localdate()
        SalesDailySummary.objects.create(
            store=self.store, product=self.product, sales_date=today, quantity_sold=Decimal("12.5")
        )
        SalesDailySummary.objects.create(
            store=self.store, product=self.product, sales_date=today - timedelta(days=29), quantity_sold=4
        )
        SalesDailySummary.objects.create(
            store=self.store, product=self.product, sales_date=today - timedelta(days=30), quantity_sold=100
        )
        SalesDailySummary.objects.create(
            store=self.store, product=self.product, sales_date=today + timedelta(days=1), quantity_sold=200
        )

        total = recalculate_monthly_need(self.store.id, self.product.id, today.replace(day=1))

        self.assertEqual(total.needed_quantity, Decimal("16.5"))
        self.assertEqual(total.avg_daily_sales_90, 0)
        self.assertEqual(total.seasonal_quantity, 0)
        self.assertEqual(total.calculation_version, "trailing-30-v1")

    def test_stale_monthly_totals_are_refreshed_in_bulk(self):
        today = timezone.localdate()
        SalesDailySummary.objects.create(
            store=self.store, product=self.product, sales_date=today, quantity_sold=7
        )
        cached = self.product.productmonthlyneed_set.create(
            store=self.store,
            month=today.replace(day=1),
            needed_quantity=999,
            calculation_version="weighted-v1",
        )

        self.assertEqual(recalculate_stale_monthly_needs(), 1)
        cached.refresh_from_db()
        self.assertEqual(cached.needed_quantity, 7)
        self.assertEqual(cached.calculation_version, "trailing-30-v1")


class OrderApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("buyer", password="secret")
        self.client.force_login(self.user)
        self.store = Store.objects.create(korona_id=uuid.uuid4(), number="1", name="Main")

    def test_create_order_list_is_idempotent(self):
        payload = {"store_id": self.store.id, "order_date": "2026-07-11"}
        first = self.client.post("/api/orders/", payload, content_type="application/json")
        second = self.client.post("/api/orders/", payload, content_type="application/json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(OrderList.objects.count(), 1)

    def test_product_suggestions_include_current_store_stock(self):
        product = Product.objects.create(
            korona_id=uuid.uuid4(),
            number="1027",
            name="BUCHANAN'S",
            normalized_name="buchanans",
            stock_last_synced_at=timezone.now(),
        )
        ProductStock.objects.create(product=product, store=self.store, actual=7)
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 12), created_by=self.user
        )
        response = self.client.get(f"/api/products/search/?q=buchanans&order_id={order_list.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["current_stock"], 7.0)

    def test_order_list_includes_supplier_fields_and_admin_can_choose_preferred(self):
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        product = Product.objects.create(
            korona_id=uuid.uuid4(),
            number="SUP-1",
            name="Supplier Test",
            normalized_name="suppliertest",
            raw_data={
                "commodityGroup": {"name": "Whiskey", "number": "17"},
                "supplierPrices": [
                    {
                        "supplier": {"id": str(first_id), "name": "Alpha Supply", "number": "10"},
                        "orderCode": "A-100",
                        "containerSize": 6,
                        "value": 12.5,
                    },
                    {
                        "supplier": {"id": str(second_id), "name": "Beta Supply", "number": "20"},
                        "orderCode": "B-200",
                        "containerSize": 12,
                        "value": 22.75,
                    },
                ]
            },
        )
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 25), created_by=self.user
        )
        OrderListItem.objects.create(
            order_list=order_list, product=product, created_by=self.user, updated_by=self.user
        )

        initial = self.client.get(f"/api/orders/{order_list.id}/").json()["items"][0]
        self.assertEqual(initial["supplier_name"], "Alpha Supply")
        self.assertEqual(initial["supplier_full_name"], "Alpha Supply")
        self.assertEqual(initial["supplier_order_code"], "A-100")
        self.assertEqual(initial["supplier_pack_size"], 6.0)
        self.assertEqual(initial["supplier_purchase_price"], 12.5)
        self.assertEqual(initial["commodity_group"], "Whiskey")
        self.assertEqual(initial["commodity_group_number"], "17")

        self.assertEqual(
            self.client.patch(
                f"/api/products/{product.id}/preferred-supplier/",
                {"supplier_id": str(second_id)},
                content_type="application/json",
            ).status_code,
            403,
        )
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        selected = self.client.patch(
            f"/api/products/{product.id}/preferred-supplier/",
            {"supplier_id": str(second_id)},
            content_type="application/json",
        )
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["supplier_name"], "Beta Supply")
        self.assertEqual(selected.json()["supplier_number"], "20")
        self.assertEqual(selected.json()["supplier_order_code"], "B-200")
        self.assertEqual(selected.json()["supplier_pack_size"], 12.0)
        self.assertEqual(selected.json()["supplier_purchase_price"], 22.75)
        product.refresh_from_db()
        self.assertEqual(product.preferred_supplier_id, second_id)

    def test_exact_product_number_ranks_before_fuzzy_matches(self):
        exact = Product.objects.create(
            korona_id=uuid.uuid4(),
            number="10001",
            name="Exact Product",
            normalized_name="exactproduct",
            stock_last_synced_at=timezone.now(),
        )
        Product.objects.create(
            korona_id=uuid.uuid4(),
            number="99999",
            name="$10,001 Holiday Cash",
            normalized_name="10001holidaycash",
            stock_last_synced_at=timezone.now(),
        )
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 15), created_by=self.user
        )
        response = self.client.get(f"/api/products/search/?q=10001&order_id={order_list.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["id"], exact.id)

    def test_barcode_search_tolerates_one_leading_or_trailing_zero(self):
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="BC-1", name="Barcode Product", normalized_name="barcodeproduct"
        )
        ProductCode.objects.create(product=product, code="123456789012", normalized_code="123456789012")

        for scanned in ["0123456789012", "1234567890120", "123456789012"]:
            response = self.client.get(f"/api/products/search/?q={scanned}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row["id"] for row in response.json()], [product.id])

    def test_product_search_returns_all_literal_matches_without_fuzzy_noise(self):
        for index in range(25):
            Product.objects.create(
                korona_id=uuid.uuid4(),
                number=f"4PK-{index}",
                name=f"Canned Cocktail 4PK Flavor {index}",
                normalized_name=f"cannedcocktail4pkflavor{index}",
                stock_last_synced_at=timezone.now(),
            )
        Product.objects.create(
            korona_id=uuid.uuid4(),
            number="RYAN-1",
            name="RYAN'S 1.75L",
            normalized_name="ryans175l",
            stock_last_synced_at=timezone.now(),
        )
        rye = Product.objects.create(
            korona_id=uuid.uuid4(),
            number="RYE-1",
            name="STRAIGHT RYE 750ML",
            normalized_name="straightrye750ml",
            stock_last_synced_at=timezone.now(),
        )

        four_packs = self.client.get("/api/products/search/?q=4pk")
        rye_results = self.client.get("/api/products/search/?q=rye")

        self.assertEqual(len(four_packs.json()), 25)
        self.assertEqual([row["id"] for row in rye_results.json()], [rye.id])

    def test_numeric_name_search_does_not_partially_match_product_number(self):
        ninety_nine = Product.objects.create(
            korona_id=uuid.uuid4(),
            number="3330",
            name="99 50ML VODKA",
            normalized_name="9950mlvodka",
        )
        Product.objects.create(
            korona_id=uuid.uuid4(),
            number="3499",
            name="SMIRNOFF 50ML RASPBERRY",
            normalized_name="smirnoff50mlraspberry",
        )

        response = self.client.get("/api/products/search/?q=99")

        self.assertEqual([row["id"] for row in response.json()], [ninety_nine.id])

    def test_category_keyword_overrides_are_saved(self):
        response = self.client.put(
            "/api/product-categories/",
            {"hidden": [], "custom": [], "overrides": {"vodka": "vodka,neutral spirit"}},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overrides"]["vodka"], "vodka,neutral spirit")
        self.assertEqual(self.client.get("/api/product-categories/").json()["overrides"]["vodka"], "vodka,neutral spirit")

    def test_product_search_corrects_pinot_nior_typo(self):
        pinot = Product.objects.create(
            korona_id=uuid.uuid4(),
            number="PN-1",
            name="HOUSE PINOT NOIR 750ML",
            normalized_name="housepinotnoir750ml",
            stock_last_synced_at=timezone.now(),
        )

        response = self.client.get("/api/products/search/?q=pinot%20nior")

        self.assertEqual([row["id"] for row in response.json()], [pinot.id])

    def test_bulk_add_products_preserves_existing_item_values(self):
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 19), created_by=self.user
        )
        products = [
            Product.objects.create(
                korona_id=uuid.uuid4(),
                number=str(900 + index),
                name=f"Batch Product {index}",
                normalized_name=f"batchproduct{index}",
            )
            for index in range(3)
        ]
        existing = OrderListItem.objects.create(
            order_list=order_list,
            product=products[0],
            on_shelf_quantity=7,
            joe_quantity=3,
            row_order=4,
            created_by=self.user,
            updated_by=self.user,
        )

        response = self.client.post(
            f"/api/orders/{order_list.id}/items/bulk/",
            {"product_ids": [products[0].id, products[1].id, products[2].id, products[1].id]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.json()["created"], response.json()["existing"]), (2, 1))
        existing.refresh_from_db()
        self.assertEqual(existing.on_shelf_quantity, 7)
        self.assertEqual(existing.joe_quantity, 3)
        added = list(order_list.items.exclude(pk=existing.pk).order_by("row_order"))
        self.assertEqual([item.product_id for item in added], [products[1].id, products[2].id])
        self.assertEqual([item.row_order for item in added], [5, 6])
        self.assertEqual([item.on_shelf_quantity for item in added], [0, 0])

    def test_pdf_and_xlsx_exports(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 13), created_by=self.user
        )
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="55", name="Test Spirit", normalized_name="testspirit"
        )
        ProductCode.objects.create(product=product, code="012345678901", normalized_code="012345678901")
        item = OrderListItem.objects.create(
            order_list=order_list,
            product=product,
            joe_quantity=2,
            bt_quantity=3,
            sqw_quantity=4,
            created_by=self.user,
            updated_by=self.user,
        )
        other_store = Store.objects.create(korona_id=uuid.uuid4(), number="2", name="Other")
        OrderItemTransfer.objects.create(item=item, from_store=other_store, quantity=5)
        cases = []
        for kind in ["joe", "bt", "sqw", "transfers"]:
            cases.extend(
                [
                    (f"/api/orders/{order_list.id}/export/{kind}.pdf", "application/pdf"),
                    (
                        f"/api/orders/{order_list.id}/export/{kind}.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ]
            )
        for url, content_type in cases:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], content_type)
        self.assertEqual(self.client.get(f"/api/orders/{order_list.id}/export/order.pdf").status_code, 404)
        self.assertEqual(self.client.get(f"/api/orders/{order_list.id}/export/order.xlsx").status_code, 404)

        joe_response = self.client.get(f"/api/orders/{order_list.id}/export/joe.xlsx")
        joe_sheet = load_workbook(BytesIO(joe_response.content)).active
        self.assertEqual([cell.value for cell in joe_sheet[4]], ["Product Name", "Product #", "Barcode", "JOE"])
        self.assertEqual([cell.value for cell in joe_sheet[5]], ["Test Spirit", "55", "012345678901", 2])

        grid_payload = {
            "orientation": "landscape",
            "title": "Monday Transfers",
            "columns": [
                {"id": "product_number", "label": "Product #", "width": 110},
                {"id": "product_name", "label": "Product name", "width": 210},
                {"id": "supplier_name", "label": "Supplier", "width": 120},
            ],
            "rows": [["55", "Test Spirit", "Southern"], ["56", "Filtered & sorted", "RNDC"]],
        }
        grid_response = self.client.post(
            f"/api/orders/{order_list.id}/export-grid.pdf",
            grid_payload,
            content_type="application/json",
            HTTP_ACCEPT="*/*",
        )
        self.assertEqual(grid_response.status_code, 200)
        self.assertEqual(grid_response["Content-Type"], "application/pdf")
        self.assertTrue(grid_response.content.startswith(b"%PDF"))
        self.assertIn("landscape", grid_response["X-PDF-Filename"])
        self.assertIn("Monday_Transfers", grid_response["X-PDF-Filename"])

        grid_xlsx_response = self.client.post(
            f"/api/orders/{order_list.id}/export-grid.xlsx",
            grid_payload,
            content_type="application/json",
            HTTP_ACCEPT="*/*",
        )
        self.assertEqual(grid_xlsx_response.status_code, 200)
        self.assertEqual(
            grid_xlsx_response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        grid_sheet = load_workbook(BytesIO(grid_xlsx_response.content)).active
        self.assertEqual(grid_sheet["A1"].value, "Monday Transfers")
        self.assertEqual([cell.value for cell in grid_sheet[4]], ["Product #", "Product name", "Supplier"])
        self.assertEqual([cell.value for cell in grid_sheet[5]], ["55", "Test Spirit", "Southern"])

        grid_payload["orientation"] = "portrait"
        portrait_response = self.client.post(
            f"/api/orders/{order_list.id}/export-grid.pdf",
            grid_payload,
            content_type="application/json",
        )
        self.assertEqual(portrait_response.status_code, 200)
        self.assertIn("portrait", portrait_response["X-PDF-Filename"])

    def test_transfer_export_sorts_and_colors_rows_by_store(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 17), created_by=self.user
        )
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="500", name="Grouped Transfer", normalized_name="groupedtransfer"
        )
        item = OrderListItem.objects.create(
            order_list=order_list, product=product, created_by=self.user, updated_by=self.user
        )
        store_20 = Store.objects.create(korona_id=uuid.uuid4(), number="20", name="Twenty")
        store_3 = Store.objects.create(korona_id=uuid.uuid4(), number="3", name="Three")
        OrderItemTransfer.objects.create(item=item, from_store=store_20, quantity=2)
        OrderItemTransfer.objects.create(item=item, from_store=store_3, quantity=3)

        response = self.client.get(f"/api/orders/{order_list.id}/export/transfers.xlsx")
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual([sheet.cell(row=row, column=3).value for row in (5, 6)], ["#3", "#20"])
        self.assertNotEqual(sheet["A5"].fill.fgColor.rgb, sheet["A6"].fill.fgColor.rgb)

    def test_order_item_uses_only_distributor_quantities(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 14), created_by=self.user
        )
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="200", name="Distributor Item", normalized_name="distributoritem"
        )
        item = OrderListItem.objects.create(
            order_list=order_list, product=product, created_by=self.user, updated_by=self.user
        )
        response = self.client.patch(
            f"/api/items/{item.id}/",
            {"joe_quantity": 1, "bt_quantity": 2, "sqw_quantity": 3},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual((payload["joe_quantity"], payload["bt_quantity"], payload["sqw_quantity"]), ("1.000", "2.000", "3.000"))
        self.assertNotIn("final_quantity", payload)
        self.assertNotIn("suggested_quantity", payload)

    def test_order_item_accepts_multiple_transfer_stores(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 16), created_by=self.user
        )
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="300", name="Transfer Item", normalized_name="transferitem"
        )
        item = OrderListItem.objects.create(
            order_list=order_list, product=product, created_by=self.user, updated_by=self.user
        )
        first = Store.objects.create(korona_id=uuid.uuid4(), number="2", name="Second")
        second = Store.objects.create(korona_id=uuid.uuid4(), number="3", name="Third")
        response = self.client.patch(
            f"/api/items/{item.id}/",
            {
                "transfers": [
                    {"from_store_id": first.id, "quantity": 4},
                    {"from_store_id": second.id, "quantity": 7},
                ]
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OrderItemTransfer.objects.filter(item=item).count(), 2)
        self.assertEqual(
            {(row["from_store_number"], row["quantity"]) for row in response.json()["transfers"]},
            {("2", 4.0), ("3", 7.0)},
        )

    @override_settings(
        STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    )
    def test_normal_user_cannot_view_operations_or_run_jobs(self):
        self.assertEqual(self.client.get("/ops/").status_code, 403)
        response = self.client.get("/api/operations/services/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/api/operations/services/stores/run/").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/operations/full-sync/",
                {"confirmation": "FULL RESYNC"},
                content_type="application/json",
            ).status_code,
            403,
        )

    def test_normal_user_gets_only_the_restricted_working_columns(self):
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 20), created_by=self.user
        )
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="901", name="Restricted", normalized_name="restricted"
        )
        item = OrderListItem.objects.create(
            order_list=order_list,
            product=product,
            joe_quantity=8,
            created_by=self.user,
            updated_by=self.user,
        )

        payload = self.client.get(f"/api/orders/{order_list.id}/").json()

        self.assertEqual(payload["stores"], [])
        self.assertFalse(payload["permissions"]["is_admin"])
        self.assertEqual(
            set(payload["items"][0]),
            {
                "id", "product", "product_number", "product_name", "on_shelf_quantity",
                "current_store_stock", "current_store_monthly_needed", "notes", "updated_at",
                "preferred_supplier_id", "supplier_name", "supplier_number", "supplier_order_code",
                "supplier_full_name", "supplier_pack_size", "supplier_purchase_price", "supplier_names",
                "commodity_group", "commodity_group_number",
            },
        )
        self.assertNotIn("joe_quantity", payload["items"][0])
        self.assertEqual(
            self.client.patch(
                f"/api/items/{item.id}/", {"joe_quantity": 2}, content_type="application/json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/items/{item.id}/", {"on_shelf_quantity": 2}, content_type="application/json"
            ).status_code,
            200,
        )

    def test_normal_user_cannot_open_bulk_orders(self):
        self.assertEqual(self.client.get("/bulk-orders/").status_code, 403)
        self.assertEqual(self.client.get("/api/bulk-orders/").status_code, 403)

    def test_normal_user_can_delete_draft_items_but_not_finalized_items(self):
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="904", name="Delete rule", normalized_name="deleterule"
        )
        draft = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 23), created_by=self.user
        )
        draft_item = OrderListItem.objects.create(
            order_list=draft, product=product, created_by=self.user, updated_by=self.user
        )
        self.assertEqual(self.client.delete(f"/api/items/{draft_item.id}/").status_code, 204)

        finalized = OrderList.objects.create(
            store=self.store,
            order_date=date(2026, 7, 24),
            status=OrderList.Status.FINALIZED,
            created_by=self.user,
        )
        finalized_item = OrderListItem.objects.create(
            order_list=finalized, product=product, created_by=self.user, updated_by=self.user
        )
        self.assertEqual(self.client.delete(f"/api/items/{finalized_item.id}/").status_code, 403)
        self.assertTrue(OrderListItem.objects.filter(pk=finalized_item.pk).exists())

    def test_finalized_list_is_read_only_and_shows_transfers_to_normal_users(self):
        order_list = OrderList.objects.create(
            store=self.store,
            order_date=date(2026, 7, 21),
            status=OrderList.Status.FINALIZED,
            created_by=self.user,
        )
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="902", name="Final", normalized_name="final"
        )
        item = OrderListItem.objects.create(
            order_list=order_list, product=product, created_by=self.user, updated_by=self.user
        )
        source = Store.objects.create(korona_id=uuid.uuid4(), number="2", name="Source")
        OrderItemTransfer.objects.create(item=item, from_store=source, quantity=3)

        payload = self.client.get(f"/api/orders/{order_list.id}/").json()

        self.assertFalse(payload["permissions"]["can_edit"])
        self.assertEqual(payload["items"][0]["transfers"][0]["from_store_number"], "2")
        self.assertEqual(
            self.client.patch(
                f"/api/items/{item.id}/", {"notes": "changed"}, content_type="application/json"
            ).status_code,
            403,
        )

    def test_admin_can_finalize_edit_and_delete_a_list(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 22), created_by=self.user
        )
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="903", name="Admin Final", normalized_name="adminfinal"
        )
        item = OrderListItem.objects.create(
            order_list=order_list, product=product, created_by=self.user, updated_by=self.user
        )

        self.assertEqual(self.client.post(f"/api/orders/{order_list.id}/finalize/").status_code, 200)
        self.assertEqual(
            self.client.patch(
                f"/api/items/{item.id}/", {"joe_quantity": 4}, content_type="application/json"
            ).status_code,
            200,
        )
        self.assertEqual(self.client.delete(f"/api/orders/{order_list.id}/").status_code, 204)
        self.assertFalse(OrderList.objects.filter(pk=order_list.pk).exists())

    def test_admin_can_manage_users_but_cannot_change_superuser(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        superuser = get_user_model().objects.create_superuser("root-user", password="strong-pass-123")
        regular = get_user_model().objects.create_user("clerk", password="strong-pass-123")

        promoted = self.client.patch(
            f"/api/users/{regular.id}/", {"is_admin": True}, content_type="application/json"
        )

        self.assertEqual(promoted.status_code, 200)
        regular.refresh_from_db()
        self.assertTrue(regular.is_staff)
        self.assertEqual(
            self.client.patch(
                f"/api/users/{superuser.id}/", {"is_admin": False}, content_type="application/json"
            ).status_code,
            403,
        )
        self.assertEqual(self.client.delete(f"/api/users/{superuser.id}/").status_code, 403)

    @patch("orders.tasks.full_resync_step_task.delay")
    def test_superuser_can_start_only_one_full_sync(self, delay):
        delay.return_value.id = "task-1"
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        first = self.client.post(
            "/api/operations/full-sync/",
            {"confirmation": "FULL RESYNC"},
            content_type="application/json",
        )
        second = self.client.post(
            "/api/operations/full-sync/",
            {"confirmation": "FULL RESYNC"},
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(FullSyncJob.objects.count(), 1)

    def test_full_sync_initialization_preserves_orders_and_users(self):
        order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 18), created_by=self.user
        )
        product = Product.objects.create(
            korona_id=uuid.uuid4(), number="800", name="Preserved", normalized_name="preserved"
        )
        item = OrderListItem.objects.create(
            order_list=order_list, product=product, created_by=self.user, updated_by=self.user
        )
        ProductCode.objects.create(product=product, code="800000", normalized_code="800000")
        ProductStock.objects.create(product=product, store=self.store, actual=4)
        job = FullSyncJob.objects.create(initiated_by=self.user, active_lock="full-sync")

        initialize_full_sync(job)

        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertTrue(OrderList.objects.filter(pk=order_list.pk).exists())
        self.assertTrue(OrderListItem.objects.filter(pk=item.pk).exists())
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertTrue(Store.objects.filter(pk=self.store.pk).exists())
        self.assertFalse(ProductStock.objects.exists())
        self.assertFalse(ProductCode.objects.exists())

    @patch("orders.full_sync.KoronaClient")
    def test_full_sync_catalog_batch_saves_progress_and_advances(self, client_class):
        korona_id = uuid.uuid4()
        client_class.return_value.account_path.return_value = "accounts/test/organizationalUnits"
        client_class.return_value.request.return_value = {
            "results": [{"id": str(korona_id), "number": "44", "name": "Store 44", "revision": 12}],
            "resultsTotal": 1,
            "pagesTotal": 1,
            "maxRevision": 12,
            "links": {},
        }
        job = FullSyncJob.objects.create(
            initiated_by=self.user,
            active_lock="full-sync",
            status=FullSyncJob.Status.RUNNING,
            stage=FullSyncJob.Stage.STORES,
        )

        self.assertTrue(run_full_sync_step(job))

        job.refresh_from_db()
        self.assertEqual(job.stage, FullSyncJob.Stage.PRODUCTS)
        self.assertEqual(job.status, FullSyncJob.Status.QUEUED)
        self.assertEqual(job.stage_progress["stores"]["processed"], 1)
        self.assertEqual(job.stage_progress["stores"]["status"], "complete")
        self.assertTrue(Store.objects.filter(korona_id=korona_id, number="44").exists())
