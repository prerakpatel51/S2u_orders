from django.db import migrations


def enable_postgres_search(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS orders_product_name_trgm "
            "ON orders_product USING gin (normalized_name gin_trgm_ops)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS orders_productcode_code_trgm "
            "ON orders_productcode USING gin (normalized_code gin_trgm_ops)"
        )


def disable_postgres_search(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS orders_product_name_trgm")
        cursor.execute("DROP INDEX IF EXISTS orders_productcode_code_trgm")


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial")]
    operations = [migrations.RunPython(enable_postgres_search, disable_postgres_search)]
