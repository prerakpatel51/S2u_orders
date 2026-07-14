from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0005_distributor_quantities")]

    operations = [
        migrations.AlterField(
            model_name="productmonthlyneed",
            name="calculation_version",
            field=models.CharField(default="trailing-30-v1", max_length=32),
        ),
    ]
