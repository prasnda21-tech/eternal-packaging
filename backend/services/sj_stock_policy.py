"""Immutable policy selection for newly created PO; historical PO remain legacy.

Do not infer a new policy from creation timestamps or edited PO dates. This
module is deliberately policy-only: no stock mutations or database writes.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

POLICY_V2 = 2
LEGACY_POLICY = 1


def policy_version(order: Optional[Mapping[str, Any]]) -> int:
    """Only an explicitly stamped V2 order uses new SJ-scoped stock behavior."""
    if not isinstance(order, Mapping):
        return LEGACY_POLICY
    raw = order.get("stock_business_rule_version")
    return POLICY_V2 if type(raw) is int and raw == POLICY_V2 else LEGACY_POLICY


def is_sj_stock_v2(order: Optional[Mapping[str, Any]]) -> bool:
    return policy_version(order) == POLICY_V2


def canonical_sj_id(shipment: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return the stable business delivery ID, never an order ID or SJ number."""
    if not isinstance(shipment, Mapping):
        return None
    sales_id = str(shipment.get("sales_delivery_id") or "").strip()
    shipment_id = str(shipment.get("id") or "").strip()
    if sales_id and shipment_id and sales_id != shipment_id:
        # A shipment row may have its own identity; sales_delivery_id wins.
        return sales_id
    return sales_id or shipment_id or None


def sj_stock_reference(order: Mapping[str, Any], shipment: Optional[Mapping[str, Any]]) -> str:
    """Fail closed for V2 if the delivery identity is missing."""
    if not is_sj_stock_v2(order):
        return str(order.get("id") or "").strip()
    sid = canonical_sj_id(shipment)
    if not sid:
        raise ValueError("SJ_STOCK_ID_REQUIRED")
    return sid
