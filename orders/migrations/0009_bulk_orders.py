from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("orders", "0008_orderlist_notes"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name="BulkOrderList", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("name", models.CharField(max_length=160)), ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="bulk_order_lists", to=settings.AUTH_USER_MODEL))], options={"ordering": ["-updated_at"]}),
        migrations.CreateModel(name="BulkOrderItem", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("row_order", models.PositiveIntegerField(default=0)), ("bulk_order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="orders.bulkorderlist")), ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="orders.product"))], options={"ordering": ["row_order", "id"], "unique_together": {("bulk_order", "product")}}),
        migrations.CreateModel(name="BulkOrderQuantity", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("cases", models.DecimalField(decimal_places=3, default=0, max_digits=12)), ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="quantities", to="orders.bulkorderitem")), ("store", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="orders.store"))], options={"unique_together": {("item", "store")}}),
    ]
