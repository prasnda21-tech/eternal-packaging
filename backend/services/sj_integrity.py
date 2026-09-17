"""Global Surat Jalan identity, freeze, allocation, and correction helpers.

GET paths must not call mutators. Writers use immutable shipment_id.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from quantity_rules import (
    SJ_STATUS_SUPERSEDED,
    SJ_STATUS_CANCELLED,
    _safe_int,
    active_shipments_stored,
    canonical_shipment_id,
    legacy_loading_allocations_stored,
    loaded_qty_for_sj_line,
    loading_confirmations_stored,
    loading_sj_identity_keys,
    match_po_line_idx,
    packing_required_total,
    qty_delivered,
    qty_loaded,
    qty_shipped,
    shipment_is_active,
    shipment_is_delivered,
    shipment_lines_fingerprint,
    shipment_qty_frozen_reason,
    shipment_qty_total,
    shipments_stored,
)


NUMANI_PO_ID = "258b4eac-d91a-4623-b206-d38c2c2e4d2f"
NUMANI_PO_NUMBER = "PO/2026/09/12/000002"
NUMANI_CUSTOMER = "ES TEH NUMANI"
NUMANI_ITEM = "22 oz Datar 8 gr Berlian"
NUMANI_SJ1 = "c992dfa4-8c67-496c-87e7-bf7eb22a0ea6"
NUMANI_SJ2 = "ee1e3067-33f2-4623-8796-161c76db6690"
NUMANI_SJ3 = "26c9e4f0-8616-4c06-ab68-c11c4bbca3c5"
NUMANI_SJ1_NUMBER = "SJ/2026/09/12/000002"
NUMANI_SJ2_NUMBER = "SJ/2026/09/12/000002_2"
NUMANI_SJ3_NUMBER = "SJ/2026/09/12/000002_3"

CAS_FIELDS = (
    "id",
    "updated_at",
    "qty_delivered",
    "delivery_status",
    "shipments",
    "surat_jalan_files",
    "loading_confirmations",
    "legacy_loading_allocations",
    "loading_snapshots",
    "loading_discrepancies",
    "delivery_confirmations",
    "sales_delivery_ids",
)


def _json_default(value: Any):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _json_default(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_default(v) for v in value]
    return str(value)


def cas_token(order: dict) -> str:
    """Deterministic compare-and-swap token of SJ integrity state. No photo bytes."""
    payload = {}
    for key in CAS_FIELDS:
        if key == "sales_delivery_ids":
            continue
        raw = order.get(key)
        if key == "surat_jalan_files" and isinstance(raw, list):
            payload[key] = [
                {
                    "id": f.get("id"),
                    "shipment_id": f.get("shipment_id"),
                    "sj_number": f.get("sj_number"),
                    "name": f.get("name"),
                    "lines": f.get("lines"),
                    "status": f.get("status"),
                    "uploaded_at": f.get("uploaded_at"),
                }
                for f in raw if isinstance(f, dict)
            ]
        elif key == "shipments" and isinstance(raw, list):
            payload[key] = [
                {
                    "id": sh.get("id"),
                    "number": sh.get("number"),
                    "status": sh.get("status"),
                    "lines": sh.get("lines"),
                    "total_qty": sh.get("total_qty"),
                    "delivered_at": sh.get("delivered_at"),
                    "delivery_status": sh.get("delivery_status"),
                    "supersedes_shipment_id": sh.get("supersedes_shipment_id"),
                    "superseded_by_shipment_id": sh.get("superseded_by_shipment_id"),
                    "created_at": sh.get("created_at"),
                    "updated_at": sh.get("updated_at"),
                }
                for sh in raw if isinstance(sh, dict)
            ]
        else:
            payload[key] = raw
    encoded = json.dumps(_json_default(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def find_shipment_exact(order: dict, shipment_id: str) -> Optional[dict]:
    want = str(shipment_id or "").strip()
    if not want:
        return None
    for sh in shipments_stored(order):
        if canonical_shipment_id(sh) == want:
            return sh
    return None


def supersede_shipment(
    order: dict,
    *,
    target_id: str,
    successor_id: Optional[str] = None,
    actor: Optional[dict] = None,
    reason: str = "",
    now: Optional[str] = None,
) -> dict:
    """Mark an existing SJ superseded/cancelled. Never hard-deletes documents."""
    sh = find_shipment_exact(order, target_id)
    if sh is None:
        raise ValueError(f"shipment_id {target_id} tidak ditemukan.")
    actor = actor or {}
    sh["status"] = SJ_STATUS_SUPERSEDED
    sh["cancelled"] = True
    sh["superseded_at"] = now
    sh["superseded_reason"] = reason or "Koreksi Surat Jalan — audit trail"
    sh["superseded_by"] = actor.get("email")
    if successor_id:
        sh["superseded_by_shipment_id"] = successor_id
        succ = find_shipment_exact(order, successor_id)
        if succ is not None:
            succ["supersedes_shipment_id"] = target_id
    for f in order.get("surat_jalan_files") or []:
        if not isinstance(f, dict):
            continue
        if str(f.get("shipment_id") or f.get("id") or "").strip() == str(target_id).strip():
            f["status"] = SJ_STATUS_SUPERSEDED
            f["active"] = False
    return sh


def restore_shipment_qty_from_snapshot(order: dict, shipment_id: str, lines: list) -> dict:
    sh = find_shipment_exact(order, shipment_id)
    if sh is None:
        raise ValueError(f"shipment_id {shipment_id} tidak ditemukan.")
    sh["lines"] = copy.deepcopy(lines)
    sh["total_qty"] = shipment_qty_total(sh)
    for f in order.get("surat_jalan_files") or []:
        if not isinstance(f, dict):
            continue
        if str(f.get("shipment_id") or f.get("id") or "").strip() != str(shipment_id).strip():
            continue
        f["lines"] = copy.deepcopy(lines)
        f["total_qty"] = sh["total_qty"]
    return sh


def po_qty_total(order: dict) -> int:
    total = packing_required_total(order)
    if total > 0:
        return total
    return max(0, _safe_int(order.get("total_quantity") or order.get("quantity")))


def active_sj_qty(order: dict) -> int:
    return sum(shipment_qty_total(sh) for sh in active_shipments_stored(order))


def loaded_qty_for_shipment(order: dict, sh: dict) -> int:
    sid = canonical_shipment_id(sh)
    total = 0
    seen_lines = set()
    for ln in sh.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        line_idx, _err = match_po_line_idx(order, ln)
        if line_idx is None or int(line_idx) in seen_lines:
            continue
        seen_lines.add(int(line_idx))
        total += loaded_qty_for_sj_line(order, sid, int(line_idx))
    if seen_lines:
        return total

    # Preserve legacy shipments without a line breakdown for audit purposes.
    target_ids = loading_sj_identity_keys(order, sid)
    for record in [
        *loading_confirmations_stored(order),
        *legacy_loading_allocations_stored(order),
    ]:
        record_sid = str(record.get("shipment_id") or record.get("surat_jalan_id") or "").strip()
        if record_sid not in target_ids:
            continue
        total += sum(
            max(0, _safe_int(row.get("qty") if row.get("qty") is not None else row.get("quantity")))
            for row in (record.get("lines") or [])
            if isinstance(row, dict)
        )
    return total


def delivered_qty_for_shipment_records(order: dict, sh: dict) -> int:
    sid = canonical_shipment_id(sh)
    total = 0
    for rec in order.get("delivery_confirmations") or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("shipment_id") or "").strip() == sid:
            total += max(0, _safe_int(rec.get("qty")))
    if total:
        return total
    if shipment_is_delivered(order, sh):
        return shipment_qty_total(sh)
    return 0


def snapshot_qty_for_shipment(order: dict, sh: dict) -> Optional[int]:
    sid = canonical_shipment_id(sh)
    snaps = [
        s for s in (order.get("loading_snapshots") or [])
        if isinstance(s, dict) and str(s.get("shipment_id") or s.get("surat_jalan_id") or "").strip() == sid
        and s.get("status") not in (SJ_STATUS_CANCELLED, SJ_STATUS_SUPERSEDED, "deleted")
    ]
    if snaps:
        last = snaps[-1]
        lines = last.get("lines") or []
        if lines:
            return sum(max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity"))) for ln in lines if isinstance(ln, dict))
    for conf in loading_confirmations_stored(order):
        if str(conf.get("shipment_id") or conf.get("surat_jalan_id") or "").strip() != sid:
            continue
        if conf.get("qty_sj_snapshot") not in (None, ""):
            return max(0, _safe_int(conf.get("qty_sj_snapshot")))
        lines = conf.get("lines") or []
        return sum(max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity"))) for ln in lines if isinstance(ln, dict))
    return None


def alias_occupancy(order: dict) -> Dict[str, List[str]]:
    """Map alias token → canonical shipment ids. Filename is recorded but not primary."""
    occ: Dict[str, List[str]] = {}

    def add(token: str, sid: str):
        t = str(token or "").strip()
        s = str(sid or "").strip()
        if not t or not s:
            return
        occ.setdefault(t, [])
        if s not in occ[t]:
            occ[t].append(s)

    for sh in shipments_stored(order):
        sid = canonical_shipment_id(sh)
        add(sid, sid)
        add(sh.get("sales_delivery_id"), sid)
        add(sh.get("number"), sid)
        add(sh.get("sj_number"), sid)
        add(sh.get("accurate_shipment_id"), sid)
    for f in order.get("surat_jalan_files") or []:
        if not isinstance(f, dict):
            continue
        sid = str(f.get("shipment_id") or f.get("id") or "").strip()
        add(f.get("id"), sid)
        add(f.get("shipment_id"), sid)
        add(f.get("sj_number"), sid)
        add(f.get("number"), sid)
        add(f.get("name"), sid)
    return occ


def expected_numani_ids() -> Tuple[str, str, str]:
    return NUMANI_SJ1, NUMANI_SJ2, NUMANI_SJ3


def _sj_entry(sid: str, number: str, qty: int, *, created_at: str, extra: Optional[dict] = None) -> dict:
    row = {
        "id": sid,
        "number": number,
        "sj_number": number,
        "sales_delivery_id": sid,
        "shipment_index": 1 if sid == NUMANI_SJ1 else (2 if sid == NUMANI_SJ2 else 3),
        "lines": [{"line_idx": 0, "line_id": "L0", "qty": qty, "name": NUMANI_ITEM}],
        "total_qty": qty,
        "created_at": created_at,
        "status": "pending",
    }
    if extra:
        row.update(extra)
    return row


def _file_entry(sid: str, number: str, qty: int, *, uploaded_at: str, name: Optional[str] = None) -> dict:
    return {
        "id": sid,
        "shipment_id": sid,
        "sj_number": number,
        "name": name or f"Surat Jalan {number}.pdf",
        "source": "SYSTEM_GENERATED",
        "storage_path": f"preview/surat-jalan/{sid}.pdf",
        "uploaded_at": uploaded_at,
        "lines": [{"line_idx": 0, "line_id": "L0", "qty": qty, "name": NUMANI_ITEM}],
        "total_qty": qty,
    }


def build_numani_broken_order() -> dict:
    """Reconstructed pre-repair state: SJ2 mutated 4000→3000, SJ3 extra 1000, open discrepancy."""
    sj1 = _sj_entry(NUMANI_SJ1, NUMANI_SJ1_NUMBER, 1000, created_at="2026-09-16T09:30:00+07:00")
    sj2 = _sj_entry(
        NUMANI_SJ2, NUMANI_SJ2_NUMBER, 3000, created_at="2026-09-16T09:33:00+07:00",
        extra={"updated_at": "2026-09-16T21:30:00+07:00", "delivered_at": "2026-09-16T21:30:00+07:00", "delivery_status": "Terkirim", "qty_delivered": 4000},
    )
    sj3 = _sj_entry(NUMANI_SJ3, NUMANI_SJ3_NUMBER, 1000, created_at="2026-09-17T13:21:00+07:00")
    return {
        "id": NUMANI_PO_ID,
        "po_number": NUMANI_PO_NUMBER,
        "customer": NUMANI_CUSTOMER,
        "customer_name": NUMANI_CUSTOMER,
        "updated_at": "2026-09-17T13:21:00+07:00",
        "line_items": [{"name": NUMANI_ITEM, "quantity": 5000, "line_id": "L0", "production_process": "print"}],
        "total_quantity": 5000,
        "packing_info": {"qty_packed": 5000, "lines": [{"line_idx": 0, "packed_qty": 5000}]},
        "shipments": [sj1, sj2, sj3],
        "surat_jalan_files": [
            _file_entry(NUMANI_SJ1, NUMANI_SJ1_NUMBER, 1000, uploaded_at="2026-09-16T09:30:00+07:00"),
            _file_entry(NUMANI_SJ2, NUMANI_SJ2_NUMBER, 3000, uploaded_at="2026-09-16T09:33:00+07:00"),
            _file_entry(NUMANI_SJ3, NUMANI_SJ3_NUMBER, 1000, uploaded_at="2026-09-17T13:21:00+07:00"),
        ],
        "loading_confirmations": [
            {
                "id": "load-sj1",
                "surat_jalan_id": NUMANI_SJ1,
                "shipment_id": NUMANI_SJ1,
                "sj_number": NUMANI_SJ1_NUMBER,
                "lines": [{"line_idx": 0, "qty": 1000, "name": NUMANI_ITEM}],
                "qty_sj_snapshot": 1000,
                "qty_physical_snapshot": 5000,
                "confirmed_at": "2026-09-16T16:29:00+07:00",
                "confirmed_by": "loader@eternal",
                "action_id": "load-1",
            },
            {
                "id": "load-sj2",
                "surat_jalan_id": NUMANI_SJ2,
                "shipment_id": NUMANI_SJ2,
                "sj_number": NUMANI_SJ2_NUMBER,
                "lines": [{"line_idx": 0, "qty": 4000, "name": NUMANI_ITEM}],
                "qty_sj_snapshot": 4000,
                "qty_physical_snapshot": 5000,
                "confirmed_at": "2026-09-16T16:29:30+07:00",
                "confirmed_by": "loader@eternal",
                "action_id": "load-2",
            },
        ],
        "loading_snapshots": [
            {
                "id": "load-sj2",
                "surat_jalan_id": NUMANI_SJ2,
                "shipment_id": NUMANI_SJ2,
                "sj_number": NUMANI_SJ2_NUMBER,
                "lines": [{"line_idx": 0, "qty": 4000}],
                "status": "complete",
                "confirmed_at": "2026-09-16T16:29:30+07:00",
            }
        ],
        "loading_discrepancies": [
            {
                "id": "disc-numani-1",
                "surat_jalan_id": NUMANI_SJ2,
                "shipment_id": NUMANI_SJ2,
                "status": "open",
                "reported_at": "2026-09-17T13:20:00+07:00",
                "note": "struktur 3000 + 1000 setelah rekonsiliasi FIFO",
                "lines": [{"line_idx": 0, "actual_qty": 3000, "classification": "kurang"}],
            }
        ],
        "delivery_confirmations": [
            {
                "id": "del-sj2",
                "action_id": "del-2",
                "shipment_id": NUMANI_SJ2,
                "sj_number": NUMANI_SJ2_NUMBER,
                "qty": 4000,
                "items": [{"line_idx": 0, "qty": 4000, "name": NUMANI_ITEM}],
                "proof_ids": ["photo-a", "photo-b"],
                "photo_count": 2,
                "has_driver_signature": True,
                "has_recipient_signature": True,
                "recipient_name": "Penerima Numani",
                "confirmed_at": "2026-09-16T21:30:00+07:00",
                "confirmed_by": "driver@eternal",
            }
        ],
        "delivery_proofs": [
            {"id": "photo-a", "shipment_id": NUMANI_SJ2, "storage_path": "preview/proof/a.jpg"},
            {"id": "photo-b", "shipment_id": NUMANI_SJ2, "storage_path": "preview/proof/b.jpg"},
        ],
        "delivery_driver_signature": {
            "id": "dsig-2",
            "shipment_id": NUMANI_SJ2,
            "driver_signature_storage_path": "preview/sig/driver.png",
            "driver_signed_at": "2026-09-16T21:29:00+07:00",
        },
        "delivery_recipient_signature": {
            "id": "rsig-2",
            "shipment_id": NUMANI_SJ2,
            "recipient_name": "Penerima Numani",
            "signature_storage_path": "preview/sig/recipient.png",
            "recipient_signed_at": "2026-09-16T21:29:30+07:00",
        },
        "qty_delivered": 4000,
        "delivery_status": "Sebagian Terkirim",
        "current_stage": 13,
    }


def build_numani_repaired_order() -> dict:
    order = build_numani_broken_order()
    restore_shipment_qty_from_snapshot(
        order, NUMANI_SJ2,
        [{"line_idx": 0, "line_id": "L0", "qty": 4000, "name": NUMANI_ITEM}],
    )
    supersede_shipment(
        order,
        target_id=NUMANI_SJ3,
        successor_id=NUMANI_SJ2,
        actor={"email": "audit.sj.integrity"},
        reason="Duplikat/koreksi setelah FIFO reconcile memotong SJ kedua. Audit trail dipertahankan.",
        now="2026-09-17T14:00:00+07:00",
    )
    for d in order.get("loading_discrepancies") or []:
        if str(d.get("id") or "") == "disc-numani-1":
            d["status"] = "superseded"
            d["resolved_at"] = "2026-09-17T14:00:00+07:00"
            d["resolved_reason"] = "Discrepancy 3000+1000 berasal dari mutate SJ setelah Loading; SJ2 dikembalikan 4000, SJ3 superseded."
    order["updated_at"] = "2026-09-17T14:00:00+07:00"
    return order


def apply_repair_ops(
    order: dict,
    ops: List[dict],
    *,
    actor: Optional[dict] = None,
    now: Optional[str] = None,
) -> List[dict]:
    applied = []
    actor = actor or {"email": "sj.integrity"}
    for op in ops:
        kind = str(op.get("op") or "").strip()
        if kind == "supersede_shipment":
            supersede_shipment(
                order,
                target_id=op["shipment_id"],
                successor_id=op.get("successor_id"),
                actor=actor,
                reason=op.get("reason") or "",
                now=now,
            )
        elif kind == "restore_qty":
            restore_shipment_qty_from_snapshot(order, op["shipment_id"], op["lines"])
        elif kind == "resolve_discrepancy":
            for d in order.get("loading_discrepancies") or []:
                if not isinstance(d, dict):
                    continue
                if str(d.get("id") or "") != str(op.get("discrepancy_id") or ""):
                    continue
                d["status"] = op.get("status") or "superseded"
                d["resolved_at"] = now
                d["resolved_reason"] = op.get("reason") or ""
        elif kind == "stamp_shipment_id":
            target = str(op.get("record_id") or "")
            sid = str(op.get("shipment_id") or "")
            field = str(op.get("collection") or "")
            for rec in order.get(field) or []:
                if isinstance(rec, dict) and str(rec.get("id") or "") == target:
                    rec["shipment_id"] = sid
        elif kind == "allocate_legacy_loading":
            if order.get("legacy_loading_allocations"):
                raise ValueError("Legacy Loading allocation sudah ada; CAS review ulang diperlukan.")
            allocations = copy.deepcopy(op.get("allocations") or [])
            if not allocations:
                raise ValueError("Legacy Loading allocation kosong.")
            for row in allocations:
                row["allocated_at"] = now
                row["allocated_by"] = actor.get("email")
                row["reason"] = op.get("reason") or ""
            order["legacy_loading_allocations"] = allocations
        else:
            raise ValueError(f"Operasi tidak dikenal: {kind}")
        applied.append(op)
    return applied


def build_loading_confirm_snapshot(
    order: dict,
    *,
    identity: dict,
    lines: List[dict],
    actor: Optional[dict] = None,
    confirmed_at: Optional[str] = None,
    action_id: Optional[str] = None,
    confirmation_id: Optional[str] = None,
) -> dict:
    actor = actor or {}
    sid = str(identity.get("shipment_id") or identity.get("surat_jalan_id") or "").strip()
    qty = 0
    for ln in lines or []:
        if isinstance(ln, dict):
            qty += max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
    return {
        "id": confirmation_id,
        "shipment_id": sid,
        "surat_jalan_id": sid,
        "sj_number": identity.get("sj_number"),
        "lines": list(lines or []),
        "qty_sj_at_confirm": qty,
        "physical_qty": qty,
        "confirmed_at": confirmed_at,
        "confirmed_by": actor.get("email"),
        "confirmed_by_name": actor.get("name") or actor.get("email"),
        "action_id": action_id,
        "status": "complete",
        "source": "loading_confirm",
    }


def order_integrity_summary(order: Optional[dict]) -> Dict[str, Any]:
    """Read-only derived snapshot for GET. Never persists."""
    order = order or {}
    active = active_shipments_stored(order)
    inactive = [sh for sh in shipments_stored(order) if not shipment_is_active(sh)]
    po_qty = po_qty_total(order)
    shipped = qty_shipped(order)
    loaded = qty_loaded(order)
    delivered = qty_delivered(order)
    leftover = []
    for sh in active:
        if shipment_is_delivered(order, sh):
            continue
        if loaded_qty_for_shipment(order, sh) > 0:
            leftover.append(canonical_shipment_id(sh))
    severity = None
    finding_count = 0
    try:
        from services.sj_integrity_audit import audit_order
        report = audit_order(order)
        severity = report.get("severity")
        finding_count = len(report.get("findings") or [])
    except Exception:
        severity = None
        finding_count = 0
    return {
        "po_qty": po_qty,
        "active_sj_count": len(active),
        "inactive_sj_count": len(inactive),
        "active_sj_qty": shipped,
        "loaded_qty": loaded,
        "delivered_qty": delivered,
        "remaining_qty": max(0, po_qty - delivered),
        "remaining_delivery": max(0, po_qty - delivered),
        "actionable_delivery": leftover,
        "inactive_shipment_ids": [canonical_shipment_id(sh) for sh in inactive],
        "severity": severity,
        "finding_count": finding_count,
    }


def expected_es_teh_ids() -> Dict[str, str]:
    return {
        "po_id": NUMANI_PO_ID,
        "po_number": NUMANI_PO_NUMBER,
        "sj1": NUMANI_SJ1,
        "sj2": NUMANI_SJ2,
        "sj3": NUMANI_SJ3,
        "customer": NUMANI_CUSTOMER,
        "item": NUMANI_ITEM,
    }
