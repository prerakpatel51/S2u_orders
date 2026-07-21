from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0022_syncstate_global_entity_unique")]

    operations = [
        migrations.CreateModel(
            name="TaskExecutionMetric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("task_id", models.CharField(max_length=255, unique=True)),
                ("task_name", models.CharField(db_index=True, max_length=255)),
                ("status", models.CharField(db_index=True, max_length=32)),
                ("duration_ms", models.PositiveIntegerField(default=0)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["created_at", "task_name", "status"],
                        name="orders_task_created_5707db_idx",
                    )
                ]
            },
        )
    ]
