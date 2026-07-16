from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0017_systemsetting"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderlistitem",
            name="on_shelf_quantity",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
