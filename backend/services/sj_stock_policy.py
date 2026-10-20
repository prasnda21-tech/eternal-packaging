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


def resolve_v2_sj_stock_identity(order: Mapping[str, Any], selected_shipment_id: str) -> str:
    """Resolve exactly one SJ for a V2 stock event; never fall back to the PO.

    Matching aliases are allowed only when they identify the same shipment.
    Ambiguous/missing mappings must stop the stock mutation, not reassign it.
    """
    if not is_sj_stock_v2(order):
        raise ValueError("SJ_STOCK_V2_REQUIRED")
    want = str(selected_shipment_id or "").strip()
    if not want:
        raise ValueError("SJ_STOCK_ID_REQUIRED")
    matches = []
    for row in order.get("shipments") or []:
        if not isinstance(row, Mapping):
            continue
        keys = {
            str(row.get("id") or "").strip(),
            str(row.get("sales_delivery_id") or "").strip(),
        }
        keys.discard("")
        if want in keys:
            matches.append(row)
    if len(matches) != 1:
        raise ValueError("SJ_STOCK_ID_AMBIGUOUS_OR_MISSING")
    result = canonical_sj_id(matches[0])
    if not result:
        raise ValueError("SJ_STOCK_ID_REQUIRED")
    return result
