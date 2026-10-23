"""Deterministic financial calculations shared by tools and integrated apps."""
from __future__ import annotations

import math


def cash_conversion_cycle(revenue, cost, receivables, inventory, payables=None, *, days=365):
    """Closing balances / period revenue or cost; callers must supply matching periods."""
    def turnover(balance, denominator):
        if balance is None or denominator in (None, 0):
            return None
        value = days * float(balance) / float(denominator)
        return value if math.isfinite(value) else None

    dso = turnover(receivables, revenue)
    dio = turnover(inventory, cost)
    dpo = turnover(payables, cost)
    ccc = dso + dio - dpo if all(v is not None for v in (dso, dio, dpo)) else None
    missing = [name for name, value in (("应收周转天数", dso), ("存货周转天数", dio), ("应付周转天数", dpo)) if value is None]
    return {
        "DSO(应收周转天数)": round(dso, 2) if dso is not None else None,
        "DIO(存货周转天数)": round(dio, 2) if dio is not None else None,
        "DPO(应付周转天数)": round(dpo, 2) if dpo is not None else None,
        "现金周期CCC": round(ccc, 2) if ccc is not None else None,
        "说明": ("缺少有效的" + "、".join(missing) + "，无法计算完整现金周期；" if missing else "")
        + f"采用期末余额、同期间收入/成本，期间天数 {days}；CCC = DSO + DIO - DPO。",
    }
