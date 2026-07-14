from django.db import migrations


def configure_stock_services(apps, schema_editor):
    ServiceControl = apps.get_model("orders", "ServiceControl")
    ServiceControl.objects.update_or_create(
        service_name="stocks",
        defaults={"interval_seconds": 120, "enabled": True, "status": "idle"},
    )
    ServiceControl.objects.update_or_create(
        service_name="stock_reconciliation",
        defaults={"interval_seconds": 86400, "enabled": True, "status": "idle"},
    )


def restore_stock_service(apps, schema_editor):
    ServiceControl = apps.get_model("orders", "ServiceControl")
    ServiceControl.objects.filter(service_name="stock_reconciliation").delete()
    ServiceControl.objects.filter(service_name="stocks").update(interval_seconds=300)


class Migration(migrations.Migration):
    dependencies = [("orders", "0011_product_preferred_supplier")]
    operations = [migrations.RunPython(configure_stock_services, restore_stock_service)]
