from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0004_syncstate_cursor_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderlistitem",
            name="joe_quantity",
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="orderlistitem",
            name="bt_quantity",
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="orderlistitem",
            name="sqw_quantity",
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12),
        ),
        migrations.RemoveField(model_name="orderlistitem", name="suggested_quantity"),
        migrations.RemoveField(model_name="orderlistitem", name="final_quantity"),
    ]
