from django.db import migrations, models
from django.db.models import Q


def keep_latest_global_cursor(apps, schema_editor):
    SyncState = apps.get_model("orders", "SyncState")
    duplicates = (
        SyncState.objects.filter(store__isnull=True)
        .values("entity")
        .annotate(latest=models.Max("last_revision"))
    )
    for row in duplicates:
        states = SyncState.objects.filter(entity=row["entity"], store__isnull=True).order_by(
            "-last_revision", "-updated_at", "-pk"
        )
        states.exclude(pk=states.first().pk).delete()


class Migration(migrations.Migration):
    dependencies = [("orders", "0021_delivery_recovery_exports")]

    operations = [
        migrations.RunPython(keep_latest_global_cursor, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="syncstate",
            constraint=models.UniqueConstraint(
                condition=Q(store__isnull=True),
                fields=("entity",),
                name="orders_syncstate_global_entity_unique",
            ),
        ),
    ]
