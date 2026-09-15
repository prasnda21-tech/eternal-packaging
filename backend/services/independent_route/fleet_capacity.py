"""Persistent armada capacity (Qty Surat Jalan) + atomic SJ load.

Capacity is a hard constraint on top of existing geographic/priority partition.
Does not split a Surat Jalan across armadas. Does not use PO ordered qty.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from quantity_rules import shipments_stored
from services.surat_jalan_files import (
    entry_lines,
    file_for_shipment,
    shipment_is_schedule_ready,
)

UNASSIGNED_CAPACITY_REASON = "Kapasitas armada tidak mencukupi"
CAPACITY_UNIT = "qty_surat_jalan"

SJ_QTY_FIELD = "qty"  # fallback: quantity
SJ_LINE_IDENTITY_FIELDS = ("line_id", "line_idx")
SJ_IDENTITY_FIELDS = ("id", "sales_delivery_id", "accurate_shipment_id")


class CapacityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _text(v: Any) -> str:
    return str(v or "").strip()


def parse_capacity_qty(raw: Any, *, label: str = "Kapasitas") -> Optional[int]:
    """Parse optional capacity. None/empty → None. Invalid → CapacityError."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise CapacityError("INVALID_CAPACITY", f"{label} tidak valid.")
    if isinstance(raw, str):
        s = raw.strip()
        if s == "":
            return None
        if s.lower() in ("nan", "infinity", "+infinity", "-infinity", "inf", "+inf", "-inf"):
            raise CapacityError("INVALID_CAPACITY", f"{label} tidak valid.")
        if any(ch in s for ch in ("e", "E", ",", " ")):
            raise CapacityError("INVALID_CAPACITY", f"{label} harus bilangan bulat positif.")
        if s.startswith("-") or s.startswith("+"):
            raise CapacityError("INVALID_CAPACITY", f"{label} harus lebih dari 0.")
        if not s.isdigit():
            raise CapacityError("INVALID_CAPACITY", f"{label} harus bilangan bulat positif.")
        n = int(s)
    elif isinstance(raw, float):
        if not math.isfinite(raw) or not raw.is_integer():
            raise CapacityError("INVALID_CAPACITY", f"{label} harus bilangan bulat positif.")
        n = int(raw)
    elif isinstance(raw, int):
        n = raw
    else:
        raise CapacityError("INVALID_CAPACITY", f"{label} tidak valid.")
    if n <= 0:
        raise CapacityError("INVALID_CAPACITY", f"{label} harus lebih dari 0.")
    return n


def parse_required_capacity_qty(raw: Any, *, label: str = "Kapasitas") -> int:
    n = parse_capacity_qty(raw, label=label)
    if n is None:
        raise CapacityError("INVALID_CAPACITY", f"{label} wajib diisi.")
    return n


def require_saved_armada_capacities(settings: Optional[dict], fleet_count: int) -> List[int]:
    """Authoritative saved capacities. Both must be persisted; fleet_count selects which are active."""
    src = settings or {}
    cap1 = parse_capacity_qty(src.get("armada_1_capacity_qty_sj"), label="Kapasitas Armada 1")
    cap2 = parse_capacity_qty(src.get("armada_2_capacity_qty_sj"), label="Kapasitas Armada 2")
    if cap1 is None or cap2 is None:
        raise CapacityError(
            "CAPACITY_NOT_SAVED",
            "Kapasitas armada belum disimpan. Simpan Kapasitas Qty SJ Armada 1 dan Armada 2 terlebih dahulu.",
        )
    n = int(fleet_count)
    if n == 1:
        return [cap1]
    if n == 2:
        return [cap1, cap2]
    raise CapacityError("INVALID_FLEET_COUNT", "fleet_count harus 1 atau 2.")


def canonical_shipment_id(shipment: Optional[dict]) -> str:
    if not isinstance(shipment, dict):
        return ""
    for key in SJ_IDENTITY_FIELDS:
        val = _text(shipment.get(key))
        if val:
            return val
    return ""


def canonical_sj_number(shipment: Optional[dict]) -> str:
    if not isinstance(shipment, dict):
        return ""
    return _text(shipment.get("number") or shipment.get("sj_number"))


def _line_qty(ln: dict) -> int:
    raw = ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, float):
        if not math.isfinite(raw) or not raw.is_integer() or raw <= 0:
            return 0
        return int(raw)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _line_identity(shipment_id: str, ln: dict, pos: int) -> Tuple[str, str, str]:
    lid = _text(ln.get("line_id"))
    if lid:
        return (shipment_id, "id", lid)
    idx = ln.get("line_idx")
    if idx is not None and str(idx).strip() != "":
        try:
            return (shipment_id, "idx", str(int(idx)))
        except (TypeError, ValueError):
            return (shipment_id, "idx", _text(idx))
    return (shipment_id, "pos", str(pos))


def sj_line_qty_total(lines: Iterable[dict], *, shipment_id: str) -> int:
    """Sum canonical qty of each valid line exactly once (by line identity, never item name)."""
    total = 0
    seen = set()
    pos = 0
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        ident = _line_identity(shipment_id, ln, pos)
        pos += 1
        if ident in seen:
            continue
        seen.add(ident)
        total += _line_qty(ln)
    return total


def sj_load_for_shipment(order: dict, shipment: dict) -> int:
    """Canonical Qty Surat Jalan for one shipment. 0 if not schedule-ready."""
    if not isinstance(shipment, dict):
        return 0
    if not shipment_is_schedule_ready(order, shipment):
        return 0
    sid = canonical_shipment_id(shipment)
    file_entry = file_for_shipment(order, shipment)
    lines = entry_lines(shipment) or entry_lines(file_entry)
    return sj_line_qty_total(lines, shipment_id=sid)


def sj_units_for_order(order: dict) -> List[dict]:
    """Ready Surat Jalan atoms for one PO. Each shipment counted once. Ineligible SJ omitted (load 0)."""
    units: List[dict] = []
    seen = set()
    for sh in shipments_stored(order):
        sid = canonical_shipment_id(sh)
        if not sid or sid in seen:
            continue
        if not shipment_is_schedule_ready(order, sh):
            continue
        seen.add(sid)
        load = sj_load_for_shipment(order, sh)
        if load <= 0:
            continue
        units.append({
            "shipment_id": sid,
            "sj_number": canonical_sj_number(sh) or sid,
            "sj_load_qty": load,
        })
    return units


def order_sj_load_qty(order: dict) -> int:
    """Sum of ready SJ loads on this PO. Does not use PO ordered / packed / loaded qty."""
    return sum(int(u["sj_load_qty"]) for u in sj_units_for_order(order))


def row_sj_load_qty(row: dict) -> int:
    try:
        n = int(row.get("sj_load_qty") or 0)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else 0


def row_sj_ids(row: dict) -> List[str]:
    units = row.get("sj_units") if isinstance(row.get("sj_units"), list) else []
    out = []
    seen = set()
    for u in units:
        if not isinstance(u, dict):
            continue
        sid = _text(u.get("shipment_id"))
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    if out:
        return out
    sid = _text(row.get("shipment_id") or row.get("assignment_id"))
    if sid:
        out.append(sid)
        seen.add(sid)
    for extra in row.get("shipment_ids") or []:
        s = _text(extra)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def assignment_id(row: Optional[dict]) -> str:
    """Canonical capacity atom id: one Surat Jalan, never a whole multi-SJ PO."""
    if not isinstance(row, dict):
        return ""
    sid = _text(row.get("assignment_id") or row.get("shipment_id"))
    if sid:
        return sid
    ids = row_sj_ids(row)
    if len(ids) == 1:
        return ids[0]
    if len(ids) > 1:
        return ""
    return _text(row.get("sales_order_id") or row.get("id"))


def expand_eligible_to_sj_atoms(
    rows: Sequence[dict],
    *,
    skip_shipment_ids: Optional[Iterable[str]] = None,
    skip_sales_order_ids: Optional[Iterable[str]] = None,
) -> List[dict]:
    """Expand PO review rows into one capacity/persist atom per canonical SJ.

    Different SJ from the same PO become independent atoms (same customer coords).
    One SJ is never split. skip_shipment_ids omits SJ already on an active route.
    skip_sales_order_ids covers legacy PO-level stops that have no shipment_id.
    """
    skip = {_text(s) for s in (skip_shipment_ids or []) if _text(s)}
    skip_po = {_text(s) for s in (skip_sales_order_ids or []) if _text(s)}
    atoms: List[dict] = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        oid = _text(row.get("sales_order_id") or row.get("id"))
        if oid and oid in skip_po:
            continue
        units = [u for u in (row.get("sj_units") or []) if isinstance(u, dict)]
        ready = []
        for u in units:
            sid = _text(u.get("shipment_id"))
            load = 0
            try:
                load = int(u.get("sj_load_qty") or 0)
            except (TypeError, ValueError):
                load = 0
            if not sid or load <= 0:
                continue
            if sid in skip:
                continue
            ready.append({**u, "shipment_id": sid, "sj_load_qty": load})
        if not ready:
            if units:
                continue
            if not oid or oid in seen or oid in skip:
                continue
            seen.add(oid)
            atoms.append({
                **row,
                "id": oid,
                "assignment_id": oid,
                "shipment_id": _text(row.get("shipment_id")) or None,
                "sj_load_qty": 0,
                "sj_units": [],
                "shipment_ids": [],
            })
            continue
        for u in ready:
            sid = u["shipment_id"]
            if sid in seen:
                continue
            seen.add(sid)
            atoms.append({
                **row,
                "id": sid,
                "assignment_id": sid,
                "shipment_id": sid,
                "sj_number": _text(u.get("sj_number")) or sid,
                "sj_label": _text(u.get("sj_number")) or sid,
                "sj_load_qty": int(u["sj_load_qty"]),
                "sj_units": [u],
                "shipment_ids": [sid],
            })
    return atoms


def unassigned_payload(row: dict, *, reason: str = UNASSIGNED_CAPACITY_REASON) -> dict:
    units = row.get("sj_units") if isinstance(row.get("sj_units"), list) else []
    aid = assignment_id(row)
    return {
        "sales_order_id": _text(row.get("sales_order_id")),
        "order_number": _text(row.get("order_number")),
        "customer_name": _text(row.get("customer_name")),
        "sj_label": _text(row.get("sj_label") or row.get("sj_number")),
        "sj_number": _text(row.get("sj_number") or row.get("sj_label")),
        "sj_units": list(units),
        "sj_load_qty": row_sj_load_qty(row),
        "shipment_id": _text(row.get("shipment_id")) or aid,
        "assignment_id": aid,
        "shipment_ids": row_sj_ids(row) or ([aid] if aid else []),
        "urgent": bool(row.get("urgent")),
        "reason": reason,
        "unassigned_reason": reason,
    }


def apply_capacity_constraint(
    fleet_rows: Sequence[Sequence[dict]],
    capacities: Sequence[int],
) -> Tuple[List[List[dict]], List[dict]]:
    """Assign whole Surat Jalan atoms into fleets without exceeding capacity.

    Capacity atom is canonical SJ (shipment_id). Different SJ of the same PO may
    land on different armadas. One SJ is never split across armadas.
    Overflow tries the other active fleet (fits-second-vehicle). Else Belum Teralokasi.
    """
    caps = [int(c) for c in capacities]
    if not caps:
        raise CapacityError("INVALID_CAPACITY", "Kapasitas armada belum disimpan.")
    remaining = list(caps)
    assigned: List[List[dict]] = [[] for _ in caps]
    queued: List[Tuple[int, dict]] = []
    seen_ids = set()
    for fi, rows in enumerate(fleet_rows):
        if fi >= len(caps):
            break
        for row in expand_eligible_to_sj_atoms(list(rows or [])):
            aid = assignment_id(row)
            if not aid or aid in seen_ids:
                continue
            seen_ids.add(aid)
            queued.append((fi, row))

    overflows: List[dict] = []
    for preferred, row in queued:
        load = row_sj_load_qty(row)
        if load <= remaining[preferred]:
            assigned[preferred].append(row)
            remaining[preferred] -= load
        else:
            overflows.append(row)

    unassigned: List[dict] = []
    for row in overflows:
        load = row_sj_load_qty(row)
        placed = False
        for i, rem in enumerate(remaining):
            if load <= rem:
                assigned[i].append(row)
                remaining[i] -= load
                placed = True
                break
        if not placed:
            unassigned.append(unassigned_payload(row))

    _assert_assignment_invariants(queued, assigned, unassigned, caps)
    return assigned, unassigned


def _assert_assignment_invariants(
    queued: Sequence[Tuple[int, dict]],
    assigned: Sequence[Sequence[dict]],
    unassigned: Sequence[dict],
    capacities: Sequence[int],
) -> None:
    eligible_ids = [assignment_id(r) for _, r in queued]
    eligible_set = {i for i in eligible_ids if i}
    assigned_ids: List[str] = []
    sj_seen: Dict[str, int] = {}
    for fi, rows in enumerate(assigned):
        load = 0
        ids_here = set()
        for row in rows:
            aid = assignment_id(row)
            if not aid:
                continue
            if aid in ids_here:
                raise CapacityError("DUPLICATE", "Surat Jalan terduplikasi di dalam armada.")
            ids_here.add(aid)
            assigned_ids.append(aid)
            load += row_sj_load_qty(row)
            sids = row_sj_ids(row) or [aid]
            if len(sids) > 1:
                raise CapacityError("SJ_SPLIT", "Satu atom kapasitas tidak boleh berisi lebih dari satu Surat Jalan.")
            for sid in sids:
                if sid in sj_seen:
                    raise CapacityError("SJ_SPLIT", "Satu Surat Jalan muncul di lebih dari satu armada.")
                sj_seen[sid] = fi
        if load > capacities[fi]:
            raise CapacityError("OVERLOAD", "Kapasitas armada terlampaui.")
    if len(assigned_ids) != len(set(assigned_ids)):
        raise CapacityError("DUPLICATE", "Surat Jalan terduplikasi antar armada.")
    unassigned_ids = [assignment_id(r) for r in unassigned if assignment_id(r)]
    if len(unassigned_ids) != len(set(unassigned_ids)):
        raise CapacityError("DUPLICATE", "Surat Jalan terduplikasi di Belum Teralokasi.")
    a_set, u_set = set(assigned_ids), set(unassigned_ids)
    if a_set & u_set:
        raise CapacityError("DUPLICATE", "Surat Jalan muncul di rute dan Belum Teralokasi.")
    if a_set | u_set != eligible_set:
        raise CapacityError("DROP", "Surat Jalan eligible hilang dari hasil kapasitas.")
    for row in unassigned:
        sids = row_sj_ids(row) or ([assignment_id(row)] if assignment_id(row) else [])
        if len(sids) > 1:
            raise CapacityError("SJ_SPLIT", "Satu atom kapasitas tidak boleh berisi lebih dari satu Surat Jalan.")
        for sid in sids:
            if sid in sj_seen:
                raise CapacityError("SJ_SPLIT", "Satu Surat Jalan muncul di rute dan Belum Teralokasi.")
            sj_seen[sid] = -1
