from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0013_remove_legacy_monthly_need_fields")]

    operations = [
        migrations.CreateModel(
            name="DeferredReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("receipt_id", models.UUIDField(unique=True)),
                ("receipt_revision", models.BigIntegerField(db_index=True, default=0)),
                ("raw_data", models.JSONField(default=dict)),
                ("reason", models.CharField(blank=True, max_length=255)),
            ],
        ),
    ]
