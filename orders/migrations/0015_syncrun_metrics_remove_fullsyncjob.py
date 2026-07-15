from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0014_deferred_receipt"),
    ]

    operations = [
        migrations.AddField(
            model_name="syncrun",
            name="metrics",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.DeleteModel(
            name="FullSyncJob",
        ),
    ]
