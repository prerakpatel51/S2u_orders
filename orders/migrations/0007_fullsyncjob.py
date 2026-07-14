import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0006_monthly_need_trailing_30"),
    ]

    operations = [
        migrations.CreateModel(
            name="FullSyncJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("success", "Success"), ("error", "Error")], db_index=True, default="queued", max_length=20)),
                ("stage", models.CharField(choices=[("initialize", "Initialize"), ("stores", "Stores"), ("products", "Products"), ("stocks", "Stocks"), ("receipts", "Receipts"), ("totals", "30-day totals"), ("complete", "Complete")], default="initialize", max_length=20)),
                ("processed", models.PositiveIntegerField(default=0)),
                ("total", models.PositiveIntegerField(default=0)),
                ("current_batch", models.PositiveIntegerField(default=0)),
                ("total_batches", models.PositiveIntegerField(default=0)),
                ("stage_progress", models.JSONField(blank=True, default=dict)),
                ("checkpoint", models.JSONField(blank=True, default=dict)),
                ("step_number", models.PositiveIntegerField(default=0)),
                ("active_lock", models.CharField(blank=True, max_length=20, null=True, unique=True)),
                ("celery_task_id", models.CharField(blank=True, max_length=255)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True)),
                ("initiated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
