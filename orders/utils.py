import re
import unicodedata
from decimal import Decimal, InvalidOperation


def normalize_search_text(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def decimal_value(value, default="0"):
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def reference_id(value):
    return (value or {}).get("id")
