"""Decimal amounts shared by reconciliation and controlled SOP validation."""

from decimal import Decimal, InvalidOperation


def parse_amount(value: str | int | float | Decimal) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("金额必须是有限十进制数")
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("金额必须是有限十进制数") from exc
    if not number.is_finite():
        raise ValueError("金额必须是有限十进制数")
    return number


def json_amounts(value):
    """Preserve existing numeric JSON contracts; calculations stay Decimal."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_amounts(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_amounts(item) for item in value]
    return value
