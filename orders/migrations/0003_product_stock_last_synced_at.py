from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0002_postgres_search")]
    operations = [
        migrations.AddField(
            model_name="product",
            name="stock_last_synced_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        )
    ]
