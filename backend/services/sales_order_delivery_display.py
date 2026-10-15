"""Display-only confirmed delivery progress for the Pesanan Penjualan list.

Shipped qty is confirmed delivery, not Surat Jalan allocation.
This module does not decide Surat Jalan eligibility.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

_EXCLUDED_STATUSES = frozenset({"cancelled", "deleted", "superseded", "dibatalkan"})
_IDENTITY_FIELDS = ("id", "sales_delivery_id", "shipment_id")


def shipment_is_valid_for_display(doc: Optional[dict]) -> bool:
    """True when a shipment row is still an active allocation."""
    if not isinstance(doc, dict):
        return False
    if doc.get("is_cancelled") is True or doc.get("cancelled") is True:
        return False
    if doc.get("deleted") or doc.get("is_deleted") or doc.get("superseded"):
        return False
    if str(doc.get("superseded_by_shipment_id") or "").strip():
        return False
    status = str(doc.get("status") or doc.get("sj_status") or "").strip().lower().replace("_", " ")
    if status in _EXCLUDED_STATUSES:
        return False
    return True


def _whole_qty(value: Any) -> Optional[int]:
    """Whole-number quantity. None means the value is not safe to display."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0 or value != value or value != int(value):
            return None
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        if text.isdigit():
            return int(text)
        return None
    return None


def _positive_header_qty(shipment: dict) -> bool:
    for field in ("total_qty", "qty"):
        qty = _whole_qty(shipment.get(field))
        if qty is not None and qty > 0:
            return True
    return False


def _explicit_line_qty(item: dict) -> Optional[int]:
    """Parsed line qty. None means this line has no usable quantity field."""
    has_qty = "qty" in item and item.get("qty") is not None
    has_quantity = "quantity" in item and item.get("quantity") is not None
    if not has_qty and not has_quantity:
        return None
    raw = item.get("qty") if has_qty else item.get("quantity")
    return _whole_qty(raw)


def _explicit_line_index(value: Any) -> Optional[int]:
    """PO line index. Missing is unknown, not line 0."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0 or value != int(value):
            return None
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _legacy_confirmed(doc: dict) -> bool:
    if _text(doc.get("delivery_status")) == "Terkirim":
        return True
    return bool(doc.get("delivered_at"))


def _confirmation_inactive(rec: dict) -> bool:
    return bool(rec.get("is_cancelled") or rec.get("cancelled") or rec.get("is_deleted"))


def _line_rows(doc: dict) -> List[dict]:
    for field in ("line_items", "lines"):
        rows = doc.get(field)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, dict)]
    return []


def _identity_keys(doc: dict, number_owners: Dict[str, int]) -> set:
    keys = set()
    for field in _IDENTITY_FIELDS:
        token = _text(doc.get(field))
        if token:
            keys.add(token)
    number = _text(doc.get("number") or doc.get("sj_number"))
    if number and number_owners.get(number) == 1:
        keys.add(number)
    return keys


def _clusters(docs: Sequence[dict]) -> List[Tuple[List[dict], set]]:
    number_owners: Dict[str, int] = {}
    for doc in docs:
        number = _text(doc.get("number") or doc.get("sj_number"))
        if number:
            number_owners[number] = number_owners.get(number, 0) + 1
    parent = list(range(len(docs)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    seen: Dict[str, int] = {}
    keys_by_doc: List[set] = []
    for index, doc in enumerate(docs):
        keys = _identity_keys(doc, number_owners)
        keys_by_doc.append(keys)
        for key in keys:
            prior = seen.get(key)
            if prior is None:
                seen[key] = index
            else:
                union(index, prior)
    grouped: Dict[int, List[int]] = {}
    for index in range(len(docs)):
        grouped.setdefault(find(index), []).append(index)
    clusters = []
    for indexes in grouped.values():
        members = [docs[index] for index in indexes]
        keys = set()
        for index in indexes:
            keys |= keys_by_doc[index]
        clusters.append((members, keys))
    return clusters


def _attribute_lines(
    items: Sequence[Any],
    line_units: Sequence[Optional[str]],
) -> Tuple[Dict[str, int], int, bool]:
    """Map explicit line qty onto PO units. Unmapped qty is not assigned."""
    mapped: Dict[str, int] = {}
    total = 0
    partial = False
    for item in items or []:
        if not isinstance(item, dict):
            partial = True
            continue
        qty = _explicit_line_qty(item)
        if qty is None:
            partial = True
            continue
        total += qty
        idx = _explicit_line_index(item.get("line_idx"))
        if idx is None or idx >= len(line_units) or line_units[idx] is None:
            partial = True
            continue
        unit = line_units[idx]
        mapped[unit] = mapped.get(unit, 0) + qty
    return mapped, total, partial


def _snapshot_rows(members: Sequence[dict]) -> Tuple[List[dict], bool]:
    chosen: Optional[List[dict]] = None
    conflict = False
    for member in members:
        rows = _line_rows(member)
        if not rows:
            continue
        if chosen is None:
            chosen = rows
            continue
        if _line_fingerprint(chosen) != _line_fingerprint(rows):
            conflict = True
    return chosen or [], conflict


def _line_fingerprint(items: Sequence[dict]) -> tuple:
    rows = []
    for item in items:
        rows.append((
            _explicit_line_index(item.get("line_idx")),
            _explicit_line_qty(item),
        ))
    return tuple(rows)


def _confirmation_fingerprint(rec: dict) -> tuple:
    return (
        _whole_qty(rec.get("qty")),
        _line_fingerprint([item for item in (rec.get("items") or []) if isinstance(item, dict)]),
    )


def _dedup_confirmations(records: Sequence[dict]) -> List[dict]:
    seen_action = set()
    seen_id = set()
    kept = []
    for rec in records:
        action_id = _text(rec.get("action_id"))
        rec_id = _text(rec.get("id"))
        if action_id:
            if action_id in seen_action:
                continue
            seen_action.add(action_id)
        elif rec_id:
            if rec_id in seen_id:
                continue
            seen_id.add(rec_id)
        kept.append(rec)
    return kept


def _linked_confirmations(records: Sequence[dict], keys: set) -> List[dict]:
    linked = []
    for rec in records:
        if not isinstance(rec, dict) or _confirmation_inactive(rec):
            continue
        if _text(rec.get("shipment_id")) in keys:
            linked.append(rec)
    return _dedup_confirmations(linked)


def build_sales_order_delivery_progress(
    order_lines: Optional[list],
    shipments: Optional[list],
    *,
    cancelled: bool = False,
    confirmations: Optional[list] = None,
    embedded_shipments: Optional[list] = None,
) -> Dict[str, Any]:
    """Column progress from confirmed shipment qty, grouped by PO unit.

    Totals are exposed only for one clean unit. Surat Jalan rows that were
    created but not confirmed contribute zero.
    """
    raw_lines: List[Any] = list(order_lines or [])
    line_units: List[Optional[str]] = []
    ordered_by_unit: Dict[str, int] = {}
    unit_order: List[str] = []
    ordered_bad_units = set()
    order_ambiguous = False

    for line in raw_lines:
        if not isinstance(line, dict):
            line_units.append(None)
            order_ambiguous = True
            continue
        qty = _whole_qty(line.get("quantity"))
        unit = str(line.get("unit") or "").strip() or "pcs"
        line_units.append(unit)
        if unit not in ordered_by_unit:
            ordered_by_unit[unit] = 0
            unit_order.append(unit)
        if qty is None:
            order_ambiguous = True
            ordered_bad_units.add(unit)
            continue
        ordered_by_unit[unit] += qty

    shipped_by_unit = {unit: 0 for unit in unit_order}
    confirmed_unverified = False
    supplied = [doc for doc in [*(shipments or []), *(embedded_shipments or [])] if isinstance(doc, dict)]
    active_docs = [doc for doc in supplied if shipment_is_valid_for_display(doc)]
    inactive_keys = set()
    for doc in supplied:
        if doc not in active_docs:
            inactive_keys |= _identity_keys(doc, {})
    confirmation_rows = _dedup_confirmations(
        [rec for rec in (confirmations or []) if isinstance(rec, dict)]
    )
    consumed = set()

    for members, keys in _clusters(active_docs):
        linked = _linked_confirmations(confirmation_rows, keys)
        for rec in linked:
            consumed.add(id(rec))
        legacy = any(_legacy_confirmed(member) for member in members)
        if not legacy and not linked:
            continue
        snapshot, snapshot_conflict = _snapshot_rows(members)
        if legacy:
            if snapshot_conflict or not snapshot:
                confirmed_unverified = True
                continue
            mapped, _total, partial = _attribute_lines(snapshot, line_units)
            if partial or not mapped:
                confirmed_unverified = True
            for unit, qty in mapped.items():
                shipped_by_unit[unit] = shipped_by_unit.get(unit, 0) + qty
            continue
        fingerprints = {_confirmation_fingerprint(rec) for rec in linked}
        if len(fingerprints) != 1:
            confirmed_unverified = True
            continue
        rec = linked[0]
        expected = _whole_qty(rec.get("qty"))
        items = [item for item in (rec.get("items") or []) if isinstance(item, dict)]
        if expected is None or expected <= 0:
            confirmed_unverified = True
            continue
        if items:
            mapped, total, partial = _attribute_lines(items, line_units)
            if total != expected:
                confirmed_unverified = True
                continue
            if partial:
                confirmed_unverified = True
            for unit, qty in mapped.items():
                shipped_by_unit[unit] = shipped_by_unit.get(unit, 0) + qty
            continue
        if snapshot_conflict or not snapshot:
            confirmed_unverified = True
            continue
        mapped, total, partial = _attribute_lines(snapshot, line_units)
        if partial or total != expected or not mapped:
            confirmed_unverified = True
            continue
        for unit, qty in mapped.items():
            shipped_by_unit[unit] = shipped_by_unit.get(unit, 0) + qty

    for rec in confirmation_rows:
        if _confirmation_inactive(rec) or id(rec) in consumed:
            continue
        if _text(rec.get("shipment_id")) in inactive_keys:
            continue
        qty = _whole_qty(rec.get("qty"))
        if qty is not None and qty > 0:
            confirmed_unverified = True

    any_shipped = any(amount > 0 for amount in shipped_by_unit.values())
    clean_units = [unit for unit in unit_order if unit not in ordered_bad_units]
    positive_units = [unit for unit in clean_units if ordered_by_unit[unit] > 0]
    unverified_remainder = confirmed_unverified or order_ambiguous or bool(ordered_bad_units)
    units = [
        {
            "unit": unit,
            "ordered": ordered_by_unit[unit],
            "shipped": shipped_by_unit.get(unit, 0),
        }
        for unit in clean_units
    ]
    if not cancelled and confirmed_unverified and not any_shipped:
        return {
            "total_ordered": None,
            "total_shipped": None,
            "total_remaining": None,
            "status": "UNVERIFIED",
            "verified": False,
            "needs_verification": False,
            "units": [],
            "unverified_remainder": False,
        }
    if cancelled:
        status = "DIBATALKAN"
    elif not any_shipped:
        status = "BELUM DIKIRIM"
    elif positive_units and all(
        shipped_by_unit.get(unit, 0) >= ordered_by_unit[unit] for unit in positive_units
    ) and not confirmed_unverified:
        status = "SELESAI"
    else:
        status = "SEBAGIAN"

    if len(clean_units) == 1 and not unverified_remainder:
        unit = clean_units[0]
        ordered = ordered_by_unit[unit]
        shipped = shipped_by_unit.get(unit, 0)
        return {
            "total_ordered": ordered,
            "total_shipped": shipped,
            "total_remaining": max(0, ordered - shipped),
            "status": status,
            "verified": True,
            "needs_verification": False,
            "units": units,
            "unverified_remainder": False,
        }
    if not units and unverified_remainder and not cancelled:
        status = "UNVERIFIED"
    return {
        "total_ordered": None,
        "total_shipped": None,
        "total_remaining": None,
        "status": status,
        "verified": False,
        "needs_verification": False,
        "units": units,
        "unverified_remainder": unverified_remainder and bool(units),
    }
