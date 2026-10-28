"""Selling prices snapshotted onto a sales order line.

The only price used here is the published selling price (harga jual).
HPP, empty-cup cost, and inventory value are never read or returned.
A blank price stays null. It is never stored as zero.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services.customer_pricing import (
    GROUPS,
    STATUS_PUBLISHED,
    VERSIONS,
    resolve_quantity_tier,
)
from services.purchase_common import line_subtotal, money_round

SOURCE_PRICING = "pengaturan_harga"
SOURCE_MANUAL = "manual"


def parse_optional_unit_price(raw: Any, field_name: str = "Harga jual") -> Optional[float]:
    """Blank stays None. Explicit zero stays zero. Invalid or negative is rejected."""
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    if isinstance(raw, bool):
        raise HTTPException(400, f"{field_name} tidak valid.")
    try:
        amount = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field_name} '{raw}' tidak valid. Format harus angka.")
    if amount < 0:
        raise HTTPException(400, f"{field_name} tidak boleh negatif.")
    return money_round(amount)


def _same_money(left: Optional[float], right: Optional[float]) -> bool:
    if left is None or right is None:
        return False
    return Decimal(str(left)).quantize(Decimal("0.01")) == Decimal(str(right)).quantize(Decimal("0.01"))


async def quote_selling_price(
    db,
    *,
    accurate_item_id: Any,
    unit: Any,
    quantity: Any,
) -> Dict[str, Any]:
    """Resolve one published selling price. Ambiguous or missing results carry no amount."""
    aid = str(accurate_item_id or "").strip()
    line_unit = str(unit or "").strip()
    if not aid:
        return {"status": "missing", "unit_price": None, "source": None}
    try:
        qty = int(str(quantity).strip())
    except (TypeError, ValueError):
        return {"status": "missing", "unit_price": None, "source": None}
    if qty <= 0:
        return {"status": "missing", "unit_price": None, "source": None}

    groups = await db[GROUPS].find(
        {
            "enabled": {"$ne": False},
            "active_accurate_item_ids": aid,
            "current_published_version_id": {"$nin": [None, ""]},
        },
        {"_id": 0, "id": 1, "current_published_version_id": 1},
    ).to_list(20)
    matches: List[Tuple[dict, dict]] = []
    for group in groups:
        version = await db[VERSIONS].find_one(
            {"id": group.get("current_published_version_id"), "status": STATUS_PUBLISHED},
            {"_id": 0, "tiers": 1, "products": 1},
        )
        if not version:
            continue
        for product in version.get("products") or []:
            if str(product.get("accurate_item_id") or "").strip() == aid:
                matches.append((version, product))
    if not matches:
        return {"status": "missing", "unit_price": None, "source": None}
    if len(matches) != 1:
        return {"status": "ambiguous", "unit_price": None, "source": None}

    version, product = matches[0]
    product_unit = str(product.get("unit") or "").strip()
    if product_unit != line_unit:
        return {
            "status": "unit_mismatch",
            "unit_price": None,
            "source": None,
            "expected_unit": product_unit,
        }
    resolved = resolve_quantity_tier(version.get("tiers") or [], qty)
    if not resolved:
        return {"status": "ambiguous", "unit_price": None, "source": None}
    index, _tier = resolved
    prices = product.get("tier_prices") or []
    if index >= len(prices) or prices[index] is None:
        return {"status": "missing", "unit_price": None, "source": None}
    try:
        unit_price = parse_optional_unit_price(prices[index], "Harga jual")
    except HTTPException:
        return {"status": "missing", "unit_price": None, "source": None}
    if unit_price is None:
        return {"status": "missing", "unit_price": None, "source": None}
    return {
        "status": "matched",
        "unit_price": unit_price,
        "source": SOURCE_PRICING,
    }


def _remap_parent_line_ids(priced: List[dict], id_map: Dict[str, str]) -> None:
    """Rewrite parent_line_id through the explicit old→new line_id map only.

    Never invents parents by name or row order. Missing map entry → hard error
    (do not silently drop the relation). After remap, sync parent_line_idx so
    dual storage (id + idx) stays consistent for Add On warna 2/3.
    """
    from services.order_line_addon import (
        sync_parent_line_relations,
        validate_lines_parent_graph,
    )

    new_ids = {str(row.get("line_id") or "").strip() for row in priced if isinstance(row, dict)}
    for index, line in enumerate(priced):
        if not isinstance(line, dict):
            continue
        raw_parent = str(line.get("parent_line_id") or "").strip()
        if not raw_parent:
            continue
        mapped = id_map.get(raw_parent)
        if mapped:
            line["parent_line_id"] = mapped
            continue
        # Already a stored id in this batch (rare); keep as-is.
        if raw_parent in new_ids:
            continue
        raise HTTPException(
            400,
            f"Baris #{index + 1}: parent_line_id '{raw_parent}' tidak merujuk ke "
            "line induk yang valid.",
        )

    # Reject id/idx that resolve to different induk — never silently prefer one.
    graph_err = validate_lines_parent_graph(priced)
    if graph_err:
        raise HTTPException(400, graph_err)
    # Sync idx from resolved id (and fill id when only idx was provided).
    # Does not invent links for unresolved / historical orphans.
    try:
        sync_parent_line_relations(priced)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def price_order_lines(db, raw_lines: List[dict]) -> Tuple[List[dict], Optional[float], bool]:
    """Validate and snapshot selling prices. Does not read the price master again after return.

    Each line receives a fresh line_id UUID. parent_line_id values that pointed at
    the client/temp ids are remapped through the old→new identity map so SERVICE /
    Add On relations survive persistence.
    """
    priced: List[dict] = []
    subtotals: List[Optional[float]] = []
    id_map: Dict[str, str] = {}
    for index, raw in enumerate(raw_lines or []):
        claimed = str(raw.get("price_source") or "").strip()
        unit_price = parse_optional_unit_price(raw.get("unit_price"), f"Harga jual baris #{index + 1}")
        review = bool(raw.get("price_review"))
        source = None
        if unit_price is None:
            source = None
        elif claimed == SOURCE_PRICING:
            quote = await quote_selling_price(
                db,
                accurate_item_id=raw.get("accurate_item_id"),
                unit=raw.get("unit"),
                quantity=raw.get("quantity"),
            )
            if quote.get("status") == "matched" and _same_money(quote.get("unit_price"), unit_price):
                source = SOURCE_PRICING
                review = False
            else:
                source = SOURCE_MANUAL
                review = True
        else:
            source = SOURCE_MANUAL
        subtotal = None if unit_price is None else line_subtotal(raw.get("quantity"), unit_price)
        subtotals.append(subtotal)
        line = dict(raw)
        line.pop("price_lock", None)
        line.pop("rowKey", None)
        line.pop("entry_mode", None)
        old_id = str(raw.get("line_id") or "").strip()
        new_id = str(uuid.uuid4())
        if old_id:
            if old_id in id_map:
                raise HTTPException(400, f"Baris #{index + 1}: line_id duplikat '{old_id}'.")
            id_map[old_id] = new_id
        line["line_idx"] = index
        line["line_id"] = new_id
        line["unit_price"] = unit_price
        line["price_source"] = source
        line["price_review"] = review
        line["line_subtotal"] = subtotal
        priced.append(line)

    _remap_parent_line_ids(priced, id_map)

    known = [amount for amount in subtotals if amount is not None]
    selling_total = money_round(sum(Decimal(str(amount)) for amount in known)) if known else None
    complete = bool(priced) and all(amount is not None for amount in subtotals)
    return priced, selling_total, complete
