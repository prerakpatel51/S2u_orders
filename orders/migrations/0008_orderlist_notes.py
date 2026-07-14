from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0007_fullsyncjob")]
    operations = [migrations.AddField(model_name="orderlist", name="notes", field=models.TextField(blank=True))]
