from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0009_bulk_orders")]
    operations = [migrations.AddField(model_name="bulkorderlist", name="notes", field=models.TextField(blank=True))]
