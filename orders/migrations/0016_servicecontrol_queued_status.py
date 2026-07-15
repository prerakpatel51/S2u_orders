from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0015_syncrun_metrics_remove_fullsyncjob"),
    ]

    operations = [
        migrations.AlterField(
            model_name="servicecontrol",
            name="status",
            field=models.CharField(
                choices=[
                    ("idle", "Idle"),
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("disabled", "Disabled"),
                    ("error", "Error"),
                ],
                default="idle",
                max_length=20,
            ),
        ),
    ]
