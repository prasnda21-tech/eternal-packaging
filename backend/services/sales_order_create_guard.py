"""Hard guard: CRM must not create operational Sales Orders directly.

CRM creates Pra PO; Admin verifies → materialize SO. Applies to create API
and import/persist paths regardless of permission overrides.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi import HTTPException

CRM_DIRECT_SO_FORBIDDEN = (
    "Role CRM hanya boleh membuat Pra PO. "
    "Pesanan Penjualan dibuat setelah Admin memverifikasi."
)


def reject_crm_direct_sales_order_create(user: Optional[Mapping[str, Any]]) -> None:
    role = str((user or {}).get("role") or "").strip().lower()
    if role == "crm":
        raise HTTPException(403, CRM_DIRECT_SO_FORBIDDEN)
