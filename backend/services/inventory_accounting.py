"""Phase 4B.1 Inventory accounting foundation.

Read-only helpers for settings scope, item identity, UOM snapshots, and
Goods Receipt financial readiness. Does not post journals or mutate stock.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services.product_unit_conversion import normalize_uom
from services.purchase_common import is_cancelled, money_round, qty_round

COST_BASIS_UOM = "BASE_UOM"
FINANCIAL_INVENTORY_MODEL_VERSION = 1

REASON_LEGACY_BEFORE_EFFECTIVE_DATE = "LEGACY_BEFORE_EFFECTIVE_DATE"
REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED = "PURCHASE_ACCOUNTING_NOT_CONFIGURED"
REASON_NON_INVENTORY_ITEM = "NON_INVENTORY_ITEM"
REASON_UNKNOWN_ITEM_TYPE = "UNKNOWN_ITEM_TYPE"
REASON_MISSING_MASTER_ITEM = "MISSING_MASTER_ITEM"
REASON_MISSING_PO_LINE = "MISSING_PO_LINE"
REASON_INVALID_UNIT_CONVERSION = "INVALID_UNIT_CONVERSION"
REASON_ALLOCATION_NOT_FINANCIAL = "ALLOCATION_NOT_FINANCIAL"
REASON_PURCHASE_ORDER_RECOGNITION = "PURCHASE_ORDER_RECOGNITION"
REASON_CANCELLED = "CANCELLED"

ITEM_TYPE_INVENTORY = "INVENTORY"
ITEM_TYPE_SERVICE = "SERVICE"

CANONICAL_GR = "purchase_goods_receipt"
CANONICAL_GR_REVERSAL = "purchase_goods_receipt_reversal"


class InvalidUnitConversionError(ValueError):
    code = REASON_INVALID_UNIT_CONVERSION


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def parse_business_date(raw: Any) -> Optional[str]:
    """Return YYYY-MM-DD when the value is an explicit valid calendar date."""
    text = str(raw or "").strip()[:10]
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def inventory_event_in_accounting_scope(event_date: Any, effective_date: Any) -> bool:
    """Pure date-scope check. Missing/invalid dates are not eligible."""
    event = parse_business_date(event_date)
    effective = parse_business_date(effective_date)
    if not event or not effective:
        return False
    return event >= effective


def financial_inventory_item_key(line: Optional[dict]) -> str:
    """Eternal Financial Inventory identity. Always prefer master_item_id."""
    mid = str((line or {}).get("master_item_id") or "").strip()
    if mid:
        return f"m:{mid}"
    return ""


def classify_inventory_item_type(item_type: Any) -> Tuple[bool, Optional[str]]:
    raw = str(item_type or "").strip().upper()
    if raw == ITEM_TYPE_INVENTORY:
        return True, None
    if raw == ITEM_TYPE_SERVICE:
        return False, REASON_NON_INVENTORY_ITEM
    return False, REASON_UNKNOWN_ITEM_TYPE


def financial_uom_snapshot(
    *,
    transaction_qty: Any,
    transaction_uom: Any,
    conversion_factor: Any,
    base_uom: Any,
    transaction_unit_cost: Any,
) -> Dict[str, Any]:
    """Snapshot transaction-unit cost into BASE UOM valuation. Decimal only."""
    tx_qty = _dec(transaction_qty)
    tx_cost = _dec(transaction_unit_cost)
    tx_uom = normalize_uom(transaction_uom) or "PCS"
    base = normalize_uom(base_uom) or tx_uom
    if tx_qty <= 0:
        raise InvalidUnitConversionError(REASON_INVALID_UNIT_CONVERSION)
    if tx_uom == base:
        factor = Decimal("1")
    else:
        try:
            factor = Decimal(str(conversion_factor if conversion_factor is not None else ""))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise InvalidUnitConversionError(REASON_INVALID_UNIT_CONVERSION) from exc
        if factor <= 0:
            raise InvalidUnitConversionError(REASON_INVALID_UNIT_CONVERSION)
    base_qty = tx_qty * factor
    if base_qty <= 0:
        raise InvalidUnitConversionError(REASON_INVALID_UNIT_CONVERSION)
    total_cost = tx_qty * tx_cost
    base_unit_cost = total_cost / base_qty
    factor_out: Any = int(factor) if factor == factor.to_integral_value() else float(factor)
    return {
        "transaction_qty": qty_round(tx_qty),
        "transaction_uom": tx_uom,
        "conversion_factor": factor_out,
        "base_qty": qty_round(base_qty),
        "base_uom": base,
        "transaction_unit_cost": money_round(tx_cost),
        "base_unit_cost": money_round(base_unit_cost),
        "total_cost": money_round(total_cost),
    }


def resolve_conversion_from_setting(
    *,
    master: Optional[dict],
    setting: Optional[dict],
    transaction_uom: Any,
) -> Tuple[Any, str, str, Optional[str]]:
    """Return (factor, tx_uom, base_uom, error_code)."""
    base = normalize_uom(
        (setting or {}).get("base_uom")
        or (master or {}).get("unit")
        or transaction_uom
        or "PCS"
    )
    tx_uom = normalize_uom(transaction_uom or (setting or {}).get("transaction_uom") or base)
    if tx_uom == base:
        return 1, tx_uom, base, None
    raw_factor = (setting or {}).get("conversion_factor")
    try:
        factor = Decimal(str(raw_factor if raw_factor is not None else ""))
    except (InvalidOperation, ValueError, TypeError):
        return None, tx_uom, base, REASON_INVALID_UNIT_CONVERSION
    if factor <= 0:
        return None, tx_uom, base, REASON_INVALID_UNIT_CONVERSION
    factor_out: Any = int(factor) if factor == factor.to_integral_value() else float(factor)
    return factor_out, tx_uom, base, None


def annotate_acquisition_event(
    event: dict,
    *,
    masters_by_id: Optional[Dict[str, dict]] = None,
    unit_settings_by_mid: Optional[Dict[str, dict]] = None,
    effective_date: Optional[str] = None,
    inventory_configured: bool = False,
) -> dict:
    """Attach financial eligibility metadata. Does not post or mutate sources."""
    out = dict(event)
    source = str(out.get("source_type") or "")
    mid = str(out.get("master_item_id") or "").strip()
    out["financial_inventory_item_key"] = financial_inventory_item_key(out)
    out["master_item_id"] = mid
    out["accurate_item_id"] = str(out.get("accurate_item_id") or "").strip()
    reasons: List[str] = []

    if source == "purchase_order":
        out["canonical_source_type"] = "purchase_order"
        out["canonical_source_id"] = out.get("source_id")
        out["financial_inventory_eligible"] = False
        out["financial_ineligibility_reason"] = REASON_PURCHASE_ORDER_RECOGNITION
        out["financial_ineligibility_reasons"] = [REASON_PURCHASE_ORDER_RECOGNITION]
        return out

    if source == "goods_receipt_allocation":
        out["canonical_source_type"] = "goods_receipt_allocation"
        out["canonical_source_id"] = out.get("source_id")
        out["financial_inventory_eligible"] = False
        out["financial_ineligibility_reason"] = REASON_ALLOCATION_NOT_FINANCIAL
        out["financial_ineligibility_reasons"] = [REASON_ALLOCATION_NOT_FINANCIAL]
        return out

    out["canonical_source_type"] = CANONICAL_GR
    out["canonical_source_id"] = out.get("source_id")

    masters_by_id = masters_by_id or {}
    unit_settings_by_mid = unit_settings_by_mid or {}
    master = masters_by_id.get(mid) or {}
    if not mid:
        reasons.append(REASON_MISSING_MASTER_ITEM)
        type_ok = False
    else:
        type_ok, type_reason = classify_inventory_item_type(master.get("item_type"))
        if not type_ok and type_reason:
            reasons.append(type_reason)

    if not inventory_configured:
        reasons.append(REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED)

    rec_date = parse_business_date(out.get("recognized_at"))
    if not inventory_event_in_accounting_scope(rec_date, effective_date):
        if parse_business_date(effective_date):
            reasons.append(REASON_LEGACY_BEFORE_EFFECTIVE_DATE)
        elif REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED not in reasons:
            reasons.append(REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED)

    setting = unit_settings_by_mid.get(mid) if mid else None
    factor, tx_uom, base_uom, conv_err = resolve_conversion_from_setting(
        master=master,
        setting=setting,
        transaction_uom=out.get("transaction_uom") or out.get("unit") or (master or {}).get("unit"),
    )
    if conv_err:
        reasons.append(conv_err)
    else:
        try:
            snap = financial_uom_snapshot(
                transaction_qty=out.get("recognized_qty"),
                transaction_uom=tx_uom,
                conversion_factor=factor,
                base_uom=base_uom,
                transaction_unit_cost=out.get("unit_cost"),
            )
            out.update(snap)
        except InvalidUnitConversionError:
            reasons.append(REASON_INVALID_UNIT_CONVERSION)

    # Unique reasons, keep first as primary.
    seen = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    out["financial_inventory_eligible"] = not seen
    out["financial_ineligibility_reason"] = None if not seen else seen[0]
    out["financial_ineligibility_reasons"] = seen
    return out


async def _load_master_and_units(db, master_item_ids: List[str]) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    mids = [str(mid).strip() for mid in master_item_ids if str(mid or "").strip()]
    masters: Dict[str, dict] = {}
    units: Dict[str, dict] = {}
    if not mids:
        return masters, units
    rows = await db.master_items.find({"id": {"$in": mids}}, {"_id": 0}).to_list(len(mids) + 10)
    for row in rows or []:
        mid = str(row.get("id") or "").strip()
        if mid:
            masters[mid] = row
    settings = await db.product_unit_settings.find(
        {"master_item_id": {"$in": mids}}, {"_id": 0}
    ).to_list(len(mids) + 10)
    for row in settings or []:
        mid = str(row.get("master_item_id") or "").strip()
        if mid:
            units[mid] = row
    return masters, units


async def get_goods_receipt_accounting_readiness(db, receipt_id: str) -> Dict[str, Any]:
    """Read-only GR financial readiness. No journal. No stock mutation."""
    from services.purchase_accounting_settings import get_purchase_accounting_settings
    from services.purchase_goods_receipts import COLLECTION as GR_COLLECTION

    rid = str(receipt_id or "").strip()
    doc = await db[GR_COLLECTION].find_one({"id": rid}, {"_id": 0})
    if not doc:
        doc = await db[GR_COLLECTION].find_one({"receipt_number": rid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Penerimaan Barang '{rid}' tidak ditemukan.")

    settings = await get_purchase_accounting_settings(db)
    effective = settings.get("inventory_accounting_effective_date")
    settings_ready = bool(settings.get("inventory_configured"))
    receipt_date = parse_business_date(doc.get("receipt_date") or doc.get("created_at"))
    in_scope = inventory_event_in_accounting_scope(receipt_date, effective)

    po = None
    po_id = str(doc.get("purchase_order_id") or "").strip()
    if po_id:
        po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    po_lines = {
        str(li.get("line_id") or ""): li
        for li in (po or {}).get("lines") or []
        if li.get("line_id")
    }

    mids = [str(li.get("master_item_id") or "").strip() for li in (doc.get("lines") or [])]
    for pli in po_lines.values():
        mids.append(str(pli.get("master_item_id") or "").strip())
    masters, units = await _load_master_and_units(db, mids)

    eligible_lines: List[dict] = []
    ineligible_lines: List[dict] = []
    amount = Decimal("0")

    if is_cancelled(doc):
        return {
            "receipt_id": doc.get("id"),
            "receipt_number": doc.get("receipt_number") or "",
            "receipt_date": receipt_date,
            "in_scope_by_date": in_scope,
            "settings_ready": settings_ready,
            "cancelled": True,
            "eligible_inventory_lines": [],
            "ineligible_lines": [{
                "reason": REASON_CANCELLED,
                "reasons": [REASON_CANCELLED],
            }],
            "financial_inventory_amount": 0.0,
            "financial_inventory_eligible": False,
        }

    for li in doc.get("lines") or []:
        po_line_id = str(li.get("purchase_order_line_id") or "").strip()
        po_line = po_lines.get(po_line_id)
        mid = str((po_line or li).get("master_item_id") or li.get("master_item_id") or "").strip()
        master = masters.get(mid) or {}
        reasons: List[str] = []
        if not po_line:
            reasons.append(REASON_MISSING_PO_LINE)
        if not mid:
            reasons.append(REASON_MISSING_MASTER_ITEM)
        else:
            type_ok, type_reason = classify_inventory_item_type(master.get("item_type"))
            if not type_ok and type_reason:
                reasons.append(type_reason)
        if not settings_ready:
            reasons.append(REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED)
        if not in_scope:
            if parse_business_date(effective):
                reasons.append(REASON_LEGACY_BEFORE_EFFECTIVE_DATE)
            elif REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED not in reasons:
                reasons.append(REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED)

        unit_price = (po_line or {}).get("unit_price") if po_line is not None else None
        if unit_price is None:
            unit_price = li.get("unit_price") or 0
        tx_qty = li.get("received_qty") or 0
        setting = units.get(mid) if mid else None
        factor, tx_uom, base_uom, conv_err = resolve_conversion_from_setting(
            master=master,
            setting=setting,
            transaction_uom=li.get("unit") or (po_line or {}).get("unit") or master.get("unit"),
        )
        snap = None
        if conv_err:
            reasons.append(conv_err)
        else:
            try:
                snap = financial_uom_snapshot(
                    transaction_qty=tx_qty,
                    transaction_uom=tx_uom,
                    conversion_factor=factor,
                    base_uom=base_uom,
                    transaction_unit_cost=unit_price,
                )
            except InvalidUnitConversionError:
                reasons.append(REASON_INVALID_UNIT_CONVERSION)

        seen = []
        for reason in reasons:
            if reason not in seen:
                seen.append(reason)
        line_out = {
            "line_id": li.get("line_id"),
            "purchase_order_line_id": po_line_id,
            "master_item_id": mid,
            "accurate_item_id": str((po_line or li).get("accurate_item_id") or master.get("accurate_item_id") or ""),
            "item_type": master.get("item_type"),
            "financial_inventory_item_key": financial_inventory_item_key({"master_item_id": mid}),
            "canonical_source_type": CANONICAL_GR,
            "canonical_source_id": doc.get("id"),
            "financial_inventory_eligible": not seen,
            "financial_ineligibility_reason": None if not seen else seen[0],
            "financial_ineligibility_reasons": seen,
            "transaction_qty": qty_round(tx_qty),
            "transaction_unit_cost": money_round(unit_price),
            "line_financial_amount": money_round(_dec(tx_qty) * _dec(unit_price)) if po_line else 0.0,
        }
        if snap:
            line_out.update(snap)
            line_out["line_financial_amount"] = snap["total_cost"]
        if not seen:
            eligible_lines.append(line_out)
            amount += _dec(line_out["line_financial_amount"])
        else:
            ineligible_lines.append(line_out)

    return {
        "receipt_id": doc.get("id"),
        "receipt_number": doc.get("receipt_number") or "",
        "receipt_date": receipt_date,
        "in_scope_by_date": in_scope,
        "settings_ready": settings_ready,
        "cancelled": False,
        "eligible_inventory_lines": eligible_lines,
        "ineligible_lines": ineligible_lines,
        "financial_inventory_amount": money_round(amount),
        "financial_inventory_eligible": bool(eligible_lines) and not ineligible_lines or bool(eligible_lines),
    }
