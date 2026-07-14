from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0010_bulkorderlist_notes")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="preferred_supplier_id",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
