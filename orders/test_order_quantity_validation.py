import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from .constants import MAX_ON_SHELF_QUANTITY
from .models import OrderList, OrderListItem, Product, Store


class OrderQuantityValidationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("quantity-buyer", password="secret")
        self.client.force_login(self.user)
        self.store = Store.objects.create(
            korona_id=uuid.uuid4(), number="quantity-store", name="Quantity Store"
        )
        self.order_list = OrderList.objects.create(
            store=self.store, order_date=date(2026, 7, 26), created_by=self.user
        )
        self.product = Product.objects.create(
            korona_id=uuid.uuid4(),
            number="QUANTITY-1",
            name="Quantity Test Product",
            normalized_name="quantitytestproduct",
        )

    def create_item(self, quantity):
        return self.client.post(
            f"/api/orders/{self.order_list.id}/items/",
            {
                "product_id": self.product.id,
                "on_shelf_quantity": quantity,
                "refresh_stock": False,
            },
            content_type="application/json",
        )

    def test_create_accepts_maximum_quantity(self):
        response = self.create_item(MAX_ON_SHELF_QUANTITY)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            OrderListItem.objects.get(order_list=self.order_list).on_shelf_quantity,
            MAX_ON_SHELF_QUANTITY,
        )

    def test_create_rejects_barcode_sized_quantity_without_writing(self):
        response = self.create_item(12_345_678_901)

        self.assertEqual(response.status_code, 400)
        self.assertIn("between 0 and 100,000", response.json()["detail"])
        self.assertFalse(OrderListItem.objects.filter(order_list=self.order_list).exists())

    def test_patch_rejects_quantity_above_limit_without_changing_item(self):
        item = OrderListItem.objects.create(
            order_list=self.order_list,
            product=self.product,
            on_shelf_quantity=4,
            created_by=self.user,
            updated_by=self.user,
        )

        response = self.client.patch(
            f"/api/items/{item.id}/",
            {"on_shelf_quantity": MAX_ON_SHELF_QUANTITY + 1},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.on_shelf_quantity, 4)
