"""Read-only global Surat Jalan integrity auditor.

Never writes. Deterministic JSON/CSV. Does not embed photo/signature bytes.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, Iterable, List, Optional

from quantity_rules import (
    _safe_int,
    _legacy_loaded_qty_for_line,
    _line_accurate_item_id,
    _po_line_identity_keys,
    active_shipments_stored,
    canonical_shipment_id,
    find_sj_file,
    loading_confirmations_stored,
    match_po_line_idx,
    qty_delivered,
    qty_loaded,
    shipment_is_active,
    shipment_qty_total,
    shipments_stored,
)
from services.sj_integrity import (
    NUMANI_PO_ID,
    NUMANI_SJ1,
    NUMANI_SJ2,
    NUMANI_SJ3,
    active_sj_qty,
    alias_occupancy,
    cas_token,
    delivered_qty_for_shipment_records,
    find_shipment_exact,
    loaded_qty_for_shipment,
    po_qty_total,
    snapshot_qty_for_shipment,
)

SEVERITY_CONSISTENT = "CONSISTENT"
SEVERITY_LEGACY_SNAPSHOT_DRIFT = "LEGACY_SNAPSHOT_DRIFT"
SEVERITY_SAFE_AUTO_REPAIR = "SAFE_AUTO_REPAIR"
SEVERITY_NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
SEVERITY_CRITICAL_BLOCKER = "CRITICAL_BLOCKER"

_RANK = {
    SEVERITY_CONSISTENT: 0,
    SEVERITY_LEGACY_SNAPSHOT_DRIFT: 1,
    SEVERITY_SAFE_AUTO_REPAIR: 2,
    SEVERITY_NEEDS_MANUAL_REVIEW: 3,
    SEVERITY_CRITICAL_BLOCKER: 4,
}

CSV_FIELDS = (
    "po_id",
    "po_number",
    "customer",
    "shipment_id",
    "sj_number",
    "current_qty",
    "snapshot_qty",
    "loaded_qty",
    "delivered_qty",
    "reason",
    "reason_code",
    "severity",
    "recommendation",
)


def _meta(order: dict) -> dict:
    return {
        "po_id": order.get("id"),
        "po_number": order.get("po_number"),
        "customer": order.get("customer") or order.get("customer_name") or "",
    }


def _finding(order: dict, *, shipment: Optional[dict] = None, reason: str, reason_code: str,
             severity: str, recommendation: str, current_qty=None, snapshot_qty=None,
             loaded_qty=None, delivered_qty=None) -> dict:
    sh = shipment if isinstance(shipment, dict) else {}
    sid = canonical_shipment_id(sh) if sh else ""
    return {
        **_meta(order),
        "shipment_id": sid or None,
        "sj_number": (sh.get("number") or sh.get("sj_number") or None) if sh else None,
        "current_qty": current_qty if current_qty is not None else (shipment_qty_total(sh) if sh else None),
        "snapshot_qty": snapshot_qty,
        "loaded_qty": loaded_qty,
        "delivered_qty": delivered_qty,
        "reason": reason,
        "reason_code": reason_code,
        "severity": severity,
        "recommendation": recommendation,
    }


def _max_severity(findings: List[dict]) -> str:
    if not findings:
        return SEVERITY_CONSISTENT
    return max(findings, key=lambda f: _RANK.get(f.get("severity"), 0)).get("severity") or SEVERITY_CONSISTENT


def _safe_restore(extra: Optional[dict], sid: str) -> bool:
    return extra is not None and extra.get("_restore_target") == sid


def _is_numani(order: dict) -> bool:
    if str(order.get("id") or "") == NUMANI_PO_ID:
        return True
    ids = {canonical_shipment_id(sh) for sh in shipments_stored(order)}
    return {NUMANI_SJ1, NUMANI_SJ2, NUMANI_SJ3}.issubset(ids)


def _safe_duplicate_extra(order: dict) -> Optional[dict]:
    """Deterministic extra SJ: no loading, no delivery, others already cover PO qty via snapshots."""
    po = po_qty_total(order)
    if po <= 0:
        return None
    extras = []
    covered = 0
    covered_ids = []
    for sh in shipments_stored(order):
        if not shipment_is_active(sh):
            continue
        sid = canonical_shipment_id(sh)
        loaded = loaded_qty_for_shipment(order, sh)
        delivered = delivered_qty_for_shipment_records(order, sh)
        snap = snapshot_qty_for_shipment(order, sh)
        if loaded <= 0 and delivered <= 0:
            extras.append(sh)
            continue
        use = snap if snap else shipment_qty_total(sh)
        covered += use
        covered_ids.append(sid)
    if len(extras) != 1 or not covered_ids:
        return None
    extra = extras[0]
    extra_qty = shipment_qty_total(extra)
    info = dict(extra)
    shrinks = []
    for sh in shipments_stored(order):
        if not shipment_is_active(sh) or canonical_shipment_id(sh) == canonical_shipment_id(extra):
            continue
        snap = snapshot_qty_for_shipment(order, sh)
        cur = shipment_qty_total(sh)
        if snap and snap > cur and (snap - cur) == extra_qty:
            shrinks.append(sh)
    if len(shrinks) == 1:
        info["_restore_target"] = canonical_shipment_id(shrinks[0])
        info["_restore_qty"] = snapshot_qty_for_shipment(order, shrinks[0])
    if covered == po and extra_qty > 0:
        return info
    if covered + extra_qty == po and covered > 0 and len(shrinks) == 1:
        return info
    return None


def _safe_legacy_loading_allocation(order: dict) -> List[dict]:
    """Plan exact per-SJ attribution only when legacy totals have one solution.

    This does not change historical Loading totals. It creates a read bridge that
    lets Delivery prove each current SJ against those already-confirmed totals.
    """
    if order.get("legacy_loading_allocations"):
        return []
    ships = active_shipments_stored(order)
    if len(ships) < 2 or loading_confirmations_stored(order):
        return []
    po = po_qty_total(order)
    if po <= 0 or sum(shipment_qty_total(sh) for sh in ships) != po:
        return []
    if any(len(sids) > 1 for sids in alias_occupancy(order).values()):
        return []
    for rec in order.get("delivery_confirmations") or []:
        if isinstance(rec, dict) and not str(rec.get("shipment_id") or "").strip():
            return []

    # Legacy totals alone cannot prove how Loading was divided. Auto-attribute
    # only the deterministic partial-delivery shape: every completed sibling is
    # delivered for exactly its SJ qty and one exact leftover SJ remains.
    pending = []
    delivered_total = 0
    for sh in ships:
        delivered = delivered_qty_for_shipment_records(order, sh)
        sj_qty = shipment_qty_total(sh)
        if delivered <= 0:
            pending.append(sh)
            continue
        if delivered != sj_qty:
            return []
        delivered_total += delivered
    if len(pending) != 1:
        return []
    if shipment_qty_total(pending[0]) != max(0, po - delivered_total):
        return []

    per_line_sj: Dict[int, int] = {}
    allocations = []
    for sh in ships:
        sid = canonical_shipment_id(sh)
        if not sid or not find_sj_file(order, sid):
            return []
        normalized = []
        for row in sh.get("lines") or []:
            if not isinstance(row, dict):
                return []
            idx, err = match_po_line_idx(order, row)
            qty = max(0, _safe_int(row.get("qty") if row.get("qty") is not None else row.get("quantity")))
            if err or idx is None or qty <= 0:
                return []
            per_line_sj[idx] = per_line_sj.get(idx, 0) + qty
            line_keys = _po_line_identity_keys((order.get("line_items") or [])[idx])
            normalized.append({
                "line_idx": idx,
                "line_id": line_keys[0] if line_keys else None,
                "accurate_item_id": _line_accurate_item_id(order, idx),
                "qty": qty,
            })
        if not normalized:
            return []
        delivered = delivered_qty_for_shipment_records(order, sh)
        if delivered > sum(row["qty"] for row in normalized):
            return []
        allocations.append({
            "shipment_id": sid,
            "surat_jalan_id": sid,
            "sj_number": sh.get("number") or sh.get("sj_number"),
            "lines": normalized,
            "source": "legacy_loading_deterministic_allocation",
            "status": "complete",
        })

    if not per_line_sj:
        return []
    for idx, allocated in per_line_sj.items():
        if _legacy_loaded_qty_for_line(order, idx) != allocated:
            return []
    legacy_total = sum(_legacy_loaded_qty_for_line(order, idx) for idx in per_line_sj)
    if legacy_total != po:
        return []
    return allocations


def audit_order(order: dict) -> dict:
    """Pure function. Does not mutate `order`."""
    findings: List[dict] = []
    po = po_qty_total(order)
    active_qty = active_sj_qty(order)
    loaded = qty_loaded(order)
    delivered = qty_delivered(order)
    occ = alias_occupancy(order)

    if po > 0 and active_qty > po:
        findings.append(_finding(
            order, reason="Total qty SJ aktif melebihi qty PO.",
            reason_code="ACTIVE_SJ_EXCEEDS_PO",
            severity=SEVERITY_CRITICAL_BLOCKER,
            recommendation="Turunkan alokasi aktif lewat koreksi resmi; jangan hard-delete.",
            current_qty=active_qty, snapshot_qty=po, loaded_qty=loaded, delivered_qty=delivered,
        ))

    if delivered > po > 0:
        findings.append(_finding(
            order, reason="Total delivery melebihi qty PO.",
            reason_code="DELIVERY_EXCEEDS_PO",
            severity=SEVERITY_CRITICAL_BLOCKER,
            recommendation="Tinjau confirmation per shipment_id. Jangan mengubah bukti.",
            current_qty=active_qty, snapshot_qty=po, loaded_qty=loaded, delivered_qty=delivered,
        ))

    for token, sids in sorted(occ.items()):
        if len(sids) > 1:
            findings.append(_finding(
                order, reason=f"Alias '{token}' mengarah ke lebih dari satu shipment.",
                reason_code="ALIAS_CROSS_SHIPMENT",
                severity=SEVERITY_NEEDS_MANUAL_REVIEW,
                recommendation="Alias hanya untuk kompatibilitas baca. Perbaiki binding ke shipment_id kanonis.",
                current_qty=None,
            ))

    extra = _safe_duplicate_extra(order)

    for sh in shipments_stored(order):
        sid = canonical_shipment_id(sh)
        number = sh.get("number") or sh.get("sj_number")
        cur = shipment_qty_total(sh)
        snap = snapshot_qty_for_shipment(order, sh)
        sh_loaded = loaded_qty_for_shipment(order, sh)
        sh_delivered = delivered_qty_for_shipment_records(order, sh)
        active = shipment_is_active(sh)

        legacy_snapshot_drift = (
            active
            and extra is None
            and po > 0
            and active_qty <= po
            and delivered <= po
            and snap is not None
            and cur > snap > 0
            and sh_loaded == cur
            and sh_delivered == cur
        )
        if legacy_snapshot_drift:
            findings.append(_finding(
                order, shipment=sh,
                reason=(
                    "Snapshot SJ historis lebih kecil, tetapi qty SJ aktif, "
                    "Loading, dan delivery sudah sama persis."
                ),
                reason_code="LEGACY_SNAPSHOT_DRIFT",
                severity=SEVERITY_LEGACY_SNAPSHOT_DRIFT,
                recommendation=(
                    "Pertahankan bukti historis; jangan ubah qty. Runtime baru "
                    "harus tetap memblokir perubahan qty setelah Loading."
                ),
                current_qty=cur,
                snapshot_qty=snap,
                loaded_qty=sh_loaded,
                delivered_qty=sh_delivered,
            ))
            continue

        if sh_loaded > 0 and snap is not None and sh_loaded > snap:
            findings.append(_finding(
                order, shipment=sh,
                reason="Loading lebih besar dari qty snapshot SJ.",
                reason_code="LOADING_EXCEEDS_SNAPSHOT",
                severity=SEVERITY_CRITICAL_BLOCKER,
                recommendation="Jangan auto-repair. Tinjau confirmation vs snapshot.",
                current_qty=cur, snapshot_qty=snap, loaded_qty=sh_loaded, delivered_qty=sh_delivered,
            ))
        if sh_delivered > cur > 0 and active:
            snap_ok = snap is not None and sh_delivered <= snap
            findings.append(_finding(
                order, shipment=sh,
                reason="Delivery lebih besar dari qty SJ aktif.",
                reason_code="DELIVERY_EXCEEDS_SJ",
                severity=(
                    SEVERITY_SAFE_AUTO_REPAIR
                    if snap_ok and _safe_restore(extra, sid)
                    else (SEVERITY_NEEDS_MANUAL_REVIEW if snap_ok else SEVERITY_CRITICAL_BLOCKER)
                ),
                recommendation=(
                    "Qty SJ menyusut setelah delivery; restore dari snapshot jika duplikat jelas."
                    if snap_ok else
                    "Tinjau delivery confirmation yang terikat shipment_id ini."
                ),
                current_qty=cur, snapshot_qty=snap, loaded_qty=sh_loaded, delivered_qty=sh_delivered,
            ))
        if snap is not None and cur != snap:
            findings.append(_finding(
                order, shipment=sh,
                reason="Mismatch current SJ versus confirmation snapshot.",
                reason_code="CURRENT_SJ_SNAPSHOT_MISMATCH",
                severity=(
                    SEVERITY_SAFE_AUTO_REPAIR if _safe_restore(extra, sid) else SEVERITY_NEEDS_MANUAL_REVIEW
                ),
                recommendation="Pulihkan qty dari snapshot hanya jika duplikat deterministik.",
                current_qty=cur, snapshot_qty=snap, loaded_qty=sh_loaded, delivered_qty=sh_delivered,
            ))
        if snap is not None and cur != snap and sh_loaded > 0:
            if extra is not None and canonical_shipment_id(extra) != sid and extra.get("_restore_target") == sid:
                findings.append(_finding(
                    order, shipment=sh,
                    reason="Qty SJ berubah setelah Loading; snapshot masih unik dan ada SJ ekstra tanpa bukti.",
                    reason_code="QTY_CHANGED_AFTER_LOADING",
                    severity=SEVERITY_SAFE_AUTO_REPAIR,
                    recommendation="Kembalikan qty dari snapshot Loading dan supersede SJ ekstra tanpa bukti.",
                    current_qty=cur, snapshot_qty=snap, loaded_qty=sh_loaded, delivered_qty=sh_delivered,
                ))
            else:
                findings.append(_finding(
                    order, shipment=sh,
                    reason="Qty SJ berbeda dari snapshot Loading.",
                    reason_code="QTY_CHANGED_AFTER_LOADING",
                    severity=SEVERITY_NEEDS_MANUAL_REVIEW if extra is None else SEVERITY_SAFE_AUTO_REPAIR,
                    recommendation="Jika duplikat deterministik, restore snapshot. Jika ambigu, review manual.",
                    current_qty=cur, snapshot_qty=snap, loaded_qty=sh_loaded, delivered_qty=sh_delivered,
                ))
        if sh_delivered > 0 and snap is not None and cur != snap:
            findings.append(_finding(
                order, shipment=sh,
                reason="Qty SJ berubah setelah delivery.",
                reason_code="QTY_CHANGED_AFTER_DELIVERY",
                severity=SEVERITY_SAFE_AUTO_REPAIR if _safe_restore(extra, sid) else SEVERITY_NEEDS_MANUAL_REVIEW,
                recommendation="Jangan mengubah bukti delivery. Restore qty dari snapshot jika duplikat jelas.",
                current_qty=cur, snapshot_qty=snap, loaded_qty=sh_loaded, delivered_qty=sh_delivered,
            ))
        if not active:
            findings.append(_finding(
                order, shipment=sh,
                reason="SJ cancelled/superseded (audit trail).",
                reason_code="SJ_INACTIVE",
                severity=SEVERITY_CONSISTENT,
                recommendation="Tetap tampil di history. Jangan dihitung alokasi aktif.",
                current_qty=cur, snapshot_qty=snap, loaded_qty=sh_loaded, delivered_qty=sh_delivered,
            ))

    remaining_alloc = max(0, po - loaded) if loaded >= po > 0 else max(0, po - active_qty)
    if extra is not None:
        findings.append(_finding(
            order, shipment=extra,
            reason="SJ aktif tanpa bukti Loading/delivery sementara alokasi lain sudah menutup qty PO.",
            reason_code="DUPLICATE_SJ_AFTER_ALLOCATION",
            severity=SEVERITY_SAFE_AUTO_REPAIR,
            recommendation="Tandai superseded/cancelled. Jangan hard-delete. Jangan mengarang successor kecuali snapshot unik.",
            current_qty=shipment_qty_total(extra),
            snapshot_qty=None,
            loaded_qty=0,
            delivered_qty=0,
        ))
        findings.append(_finding(
            order, shipment=extra,
            reason="SJ aktif tanpa allocation tersisa.",
            reason_code="ACTIVE_SJ_NO_ALLOCATION_REMAINING",
            severity=SEVERITY_SAFE_AUTO_REPAIR,
            recommendation="SJ ekstra tidak boleh mengonsumsi qty yang sudah Loading/teralokasi.",
            current_qty=shipment_qty_total(extra),
            snapshot_qty=remaining_alloc,
            loaded_qty=loaded,
            delivered_qty=delivered,
        ))

    legacy_loaded = sum(
        _legacy_loaded_qty_for_line(order, idx)
        for idx, _line in enumerate(order.get("line_items") or [])
    )
    if legacy_loaded > 0 and len(active_shipments_stored(order)) > 1 and not order.get("legacy_loading_allocations"):
        safe_allocations = _safe_legacy_loading_allocation(order)
        findings.append(_finding(
            order,
            reason="Loading lama belum terikat ke masing-masing Surat Jalan.",
            reason_code="LEGACY_LOADING_UNSCOPED_MULTI_SJ",
            severity=(SEVERITY_SAFE_AUTO_REPAIR if safe_allocations else SEVERITY_NEEDS_MANUAL_REVIEW),
            recommendation=(
                "Tambahkan atribusi read-bridge per SJ dari total Loading lama yang deterministik."
                if safe_allocations else
                "Atribusi per SJ tidak unik; review dokumen dan bukti Loading secara manual."
            ),
            current_qty=active_qty,
            snapshot_qty=po,
            loaded_qty=legacy_loaded,
            delivered_qty=delivered,
        ))

    for conf in order.get("loading_confirmations") or []:
        if not isinstance(conf, dict) or conf.get("cancelled") or conf.get("deleted"):
            continue
        if not str(conf.get("shipment_id") or "").strip():
            ships = [sh for sh in shipments_stored(order) if shipment_is_active(sh)]
            sev = SEVERITY_SAFE_AUTO_REPAIR if len(ships) == 1 else SEVERITY_NEEDS_MANUAL_REVIEW
            findings.append(_finding(
                order, shipment=ships[0] if len(ships) == 1 else None,
                reason="Loading confirmation tanpa shipment_id.",
                reason_code="LOADING_MISSING_SHIPMENT_ID",
                severity=sev,
                recommendation="Stamp shipment_id hanya jika PO punya satu SJ aktif. Jangan menebak lintas SJ.",
                loaded_qty=_safe_int(conf.get("qty_sj_snapshot")),
            ))

    for rec in order.get("delivery_confirmations") or []:
        if not isinstance(rec, dict):
            continue
        if not str(rec.get("shipment_id") or "").strip():
            ships = [sh for sh in shipments_stored(order) if shipment_is_active(sh)]
            sev = SEVERITY_SAFE_AUTO_REPAIR if len(ships) == 1 else SEVERITY_NEEDS_MANUAL_REVIEW
            findings.append(_finding(
                order, shipment=ships[0] if len(ships) == 1 else None,
                reason="Delivery confirmation tanpa shipment_id.",
                reason_code="DELIVERY_MISSING_SHIPMENT_ID",
                severity=sev,
                recommendation="Stamp shipment_id hanya jika PO punya satu SJ aktif.",
                delivered_qty=_safe_int(rec.get("qty")),
            ))

    if loaded > 0 and active_qty > po > 0:
        findings.append(_finding(
            order,
            reason="SJ baru muncul setelah qty PO pernah ter-Loading secara penuh pada kombinasi SJ lain.",
            reason_code="SJ_AFTER_FULL_LOADING",
            severity=SEVERITY_SAFE_AUTO_REPAIR if extra is not None else SEVERITY_NEEDS_MANUAL_REVIEW,
            recommendation="SJ ekstra tanpa bukti boleh superseded. Jangan mengubah Loading lama.",
            current_qty=active_qty, snapshot_qty=po, loaded_qty=loaded, delivered_qty=delivered,
        ))

    if _is_numani(order):
        sj2 = find_shipment_exact(order, NUMANI_SJ2)
        sj3 = find_shipment_exact(order, NUMANI_SJ3)
        repaired = (
            sj2 is not None and shipment_qty_total(sj2) == 4000
            and sj3 is not None and not shipment_is_active(sj3)
        )
        if not repaired:
            findings.append(_finding(
                order,
                reason="Pola ES TEH NUMANI terdeteksi (tiga shipment_id yang disetujui).",
                reason_code="NUMANI_PATTERN",
                severity=SEVERITY_SAFE_AUTO_REPAIR,
                recommendation="Jalankan backfill targeted: SJ1 1000 aktif, SJ2 4000 aktif, SJ3 superseded.",
                current_qty=active_qty, snapshot_qty=5000, loaded_qty=loaded, delivered_qty=delivered,
            ))

    # Filter informational CONSISTENT inactive rows out of severity max unless they are the only rows.
    material = [f for f in findings if f.get("severity") != SEVERITY_CONSISTENT]
    severity = _max_severity(material)
    if not material:
        findings.append(_finding(
            order,
            reason="PO konsisten terhadap invariant SJ/Loading/Delivery.",
            reason_code="CONSISTENT",
            severity=SEVERITY_CONSISTENT,
            recommendation="Tidak ada perubahan.",
            current_qty=active_qty, snapshot_qty=po, loaded_qty=loaded, delivered_qty=delivered,
        ))
        severity = SEVERITY_CONSISTENT

    findings.sort(key=lambda f: (
        _RANK.get(f.get("severity"), 0),
        str(f.get("reason_code") or ""),
        str(f.get("shipment_id") or ""),
    ))
    return {
        "po_id": order.get("id"),
        "po_number": order.get("po_number"),
        "customer": order.get("customer") or order.get("customer_name") or "",
        "severity": severity,
        "po_qty": po,
        "active_sj_qty": active_qty,
        "loaded_qty": loaded,
        "delivered_qty": delivered,
        "remaining_delivery": max(0, po - delivered),
        "cas_token": cas_token(order),
        "findings": findings,
        "safe_repair_ops": plan_safe_auto_repair(order, findings) if severity == SEVERITY_SAFE_AUTO_REPAIR else [],
    }


def plan_safe_auto_repair(order: dict, findings: Optional[List[dict]] = None) -> List[dict]:
    """Only deterministic ops. Ambiguous cases return empty (stay NEEDS_MANUAL_REVIEW)."""
    ops: List[dict] = []
    legacy_allocations = _safe_legacy_loading_allocation(order)
    if legacy_allocations:
        ops.append({
            "op": "allocate_legacy_loading",
            "allocations": legacy_allocations,
            "reason": "Atribusi exact-SJ dari total Loading lama yang sama persis dengan seluruh alokasi SJ aktif.",
        })
    extra = _safe_duplicate_extra(order)
    if extra is not None:
        restore_id = extra.get("_restore_target")
        if restore_id:
            sh = next((s for s in shipments_stored(order) if canonical_shipment_id(s) == restore_id), None)
            snap = snapshot_qty_for_shipment(order, sh) if sh else None
            if sh is not None and snap:
                ops.append({
                    "op": "restore_qty",
                    "shipment_id": restore_id,
                    "lines": [{"line_idx": 0, "qty": snap, "line_id": (sh.get("lines") or [{}])[0].get("line_id") if sh.get("lines") else None, "name": (sh.get("lines") or [{}])[0].get("name") if sh.get("lines") else None}],
                    "reason": "Restore qty SJ dari snapshot Loading yang unik.",
                })
        successor = restore_id
        if not successor:
            loaded_ships = [
                canonical_shipment_id(sh) for sh in shipments_stored(order)
                if shipment_is_active(sh) and loaded_qty_for_shipment(order, sh) > 0
                and canonical_shipment_id(sh) != canonical_shipment_id(extra)
            ]
            # Never invent a supersede relation when more than one successor is possible.
            successor = loaded_ships[0] if len(loaded_ships) == 1 else None
        ops.append({
            "op": "supersede_shipment",
            "shipment_id": canonical_shipment_id(extra),
            "successor_id": successor,
            "reason": "SJ duplikat/koreksi tanpa bukti Loading/delivery.",
        })
        for d in order.get("loading_discrepancies") or []:
            if isinstance(d, dict) and d.get("status") in ("open", "held", None, ""):
                ops.append({
                    "op": "resolve_discrepancy",
                    "discrepancy_id": d.get("id"),
                    "status": "superseded",
                    "reason": "Discrepancy terbuka akibat struktur duplikat; tidak hard-delete.",
                })
    ships = [sh for sh in shipments_stored(order) if shipment_is_active(sh)]
    if len(ships) == 1:
        sid = canonical_shipment_id(ships[0])
        for conf in order.get("loading_confirmations") or []:
            if isinstance(conf, dict) and not str(conf.get("shipment_id") or "").strip() and not conf.get("cancelled"):
                ops.append({
                    "op": "stamp_shipment_id",
                    "collection": "loading_confirmations",
                    "record_id": conf.get("id"),
                    "shipment_id": sid,
                    "reason": "PO satu SJ aktif — stamp shipment_id kanonis.",
                })
        for rec in order.get("delivery_confirmations") or []:
            if isinstance(rec, dict) and not str(rec.get("shipment_id") or "").strip():
                ops.append({
                    "op": "stamp_shipment_id",
                    "collection": "delivery_confirmations",
                    "record_id": rec.get("id"),
                    "shipment_id": sid,
                    "reason": "PO satu SJ aktif — stamp shipment_id kanonis.",
                })
    if _is_numani(order):
        sj2 = find_shipment_exact(order, NUMANI_SJ2)
        sj3 = find_shipment_exact(order, NUMANI_SJ3)
        already = (
            sj2 is not None and shipment_qty_total(sj2) == 4000
            and sj3 is not None and not shipment_is_active(sj3)
        )
        if already:
            return [op for op in ops if op.get("op") == "stamp_shipment_id"]
        ids = {canonical_shipment_id(sh) for sh in shipments_stored(order)}
        if ids >= {NUMANI_SJ1, NUMANI_SJ2, NUMANI_SJ3}:
            ops = [
                {
                    "op": "restore_qty",
                    "shipment_id": NUMANI_SJ2,
                    "lines": [{"line_idx": 0, "line_id": "L0", "qty": 4000, "name": "22 oz Datar 8 gr Berlian"}],
                    "reason": "SJ kedua harus 4.000 sesuai Loading/delivery yang terikat shipment_id.",
                },
                {
                    "op": "supersede_shipment",
                    "shipment_id": NUMANI_SJ3,
                    "successor_id": NUMANI_SJ2,
                    "reason": "SJ ketiga adalah koreksi/duplikat. Audit trail dipertahankan.",
                },
            ]
            for d in order.get("loading_discrepancies") or []:
                if isinstance(d, dict) and d.get("status") in ("open", "held", None, ""):
                    ops.append({
                        "op": "resolve_discrepancy",
                        "discrepancy_id": d.get("id"),
                        "status": "superseded",
                        "reason": "Discrepancy 3000+1000 dari mutate SJ2 setelah Loading.",
                    })
    return ops


def order_integrity_summary(order: dict) -> dict:
    """Derived GET snapshot. Must not mutate persisted fields."""
    report = audit_order(order)
    return {
        "severity": report["severity"],
        "po_qty": report["po_qty"],
        "active_sj_qty": report["active_sj_qty"],
        "loaded_qty": report["loaded_qty"],
        "delivered_qty": report["delivered_qty"],
        "remaining_delivery": report["remaining_delivery"],
        "finding_count": len(report["findings"]),
    }


def audit_orders(orders: Iterable[dict]) -> dict:
    rows = []
    counts = {
        SEVERITY_CONSISTENT: 0,
        SEVERITY_LEGACY_SNAPSHOT_DRIFT: 0,
        SEVERITY_SAFE_AUTO_REPAIR: 0,
        SEVERITY_NEEDS_MANUAL_REVIEW: 0,
        SEVERITY_CRITICAL_BLOCKER: 0,
    }
    findings = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        report = audit_order(order)
        rows.append(report)
        counts[report["severity"]] = counts.get(report["severity"], 0) + 1
        findings.extend(report["findings"])
    rows.sort(key=lambda r: (str(r.get("po_id") or ""), str(r.get("po_number") or "")))
    findings.sort(key=lambda f: (
        str(f.get("po_id") or ""),
        str(f.get("reason_code") or ""),
        str(f.get("shipment_id") or ""),
    ))
    return {
        "read_only": True,
        "wrote": False,
        "order_count": len(rows),
        "counts": counts,
        "orders": rows,
        "findings": findings,
    }


def report_to_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True)


def report_to_csv(report: dict) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(CSV_FIELDS), extrasaction="ignore")
    writer.writeheader()
    for row in report.get("findings") or []:
        writer.writerow({k: row.get(k) if row.get(k) is not None else "" for k in CSV_FIELDS})
    return buf.getvalue()
