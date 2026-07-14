from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0003_product_stock_last_synced_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="syncstate",
            name="cursor_data",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
