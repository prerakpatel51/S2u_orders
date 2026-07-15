from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("orders", "0012_stock_sync_controls")]

    operations = [
        migrations.RemoveField(model_name="productmonthlyneed", name="avg_daily_sales_90"),
        migrations.RemoveField(model_name="productmonthlyneed", name="seasonal_quantity"),
        migrations.RemoveField(model_name="productmonthlyneed", name="confidence"),
        migrations.RemoveField(model_name="productmonthlyneed", name="calculation_version"),
    ]
