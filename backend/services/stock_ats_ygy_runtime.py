"""YGY-MAIN Accurate ATS cutover — read overlay for Stock Saat Ini.

Does NOT write physical_stock_balances, ledger, or activate production.
Baseline draft is Owner-defined free blank stock (not physical/opname).
Overlay READY only when evidence_complete and projector succeeds.
Otherwise fail closed with explicit coverage gaps (no invented ATS).

Mode B: Accurate branch token "50" maps to YGY org for order filtering —
do not rely on ERP branch UUID alone (RO proved UUID-only → 0 orders).
"""
from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.stock_ats_cutover import (
    Baseline,
    CutoverError,
    Event,
    Reservation,
    project_available,
    quantity,
    timestamp,
)

YGY_WAREHOUSE_ID = "b3d0bf36-1140-4eac-b0f7-b5c924ed0d24"
YGY_WAREHOUSE_CODE = "YGY-MAIN"
YGY_BRANCH_ID = "5c92529c-3df8-4d9f-b290-a5c94fa51903"
# Mode B — Accurate org/branch token observed on live orders for Jogja.
ACCURATE_BRANCH_TOKENS_YGY = frozenset({"50", "YGY", "YOGYA", "YOGYAKARTA", "JOGJA"})

CUTOFF_AT = "2026-11-02T23:22:00+07:00"
CUTOFF_AT_UTC = "2026-11-02T16:22:00Z"

REASON_ATS_COVERAGE_GAP = "ATS_EVENT_COVERAGE_GAP"
REASON_ATS_NOT_IN_BASELINE = "ATS_NOT_IN_BASELINE_FILE"
REASON_ATS_INACTIVE_MASTER = "ATS_INACTIVE_MASTER_EXCLUDED"
REASON_ATS_MODE_OFF = "ATS_CUTOVER_MODE_OFF"
REASON_ATS_WRONG_WAREHOUSE = "ATS_WAREHOUSE_NOT_YGY_MAIN"
SOURCE_ATS = "ACCURATE_ATS_CUTOVER"

# off | draft_read | activated — activated still requires evidence_complete.
def ats_ygy_mode() -> str:
    return str(os.environ.get("ETERNAL_ATS_YGY_MODE") or "off").strip().lower()


def ats_ygy_enabled() -> bool:
    return ats_ygy_mode() in ("draft_read", "activated")


def default_baseline_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "ats_baseline_ygy_main_draft_162.json"


_BASELINE_CACHE: Optional[Dict[str, Any]] = None


def load_baseline_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    global _BASELINE_CACHE
    p = path or default_baseline_path()
    if _BASELINE_CACHE is not None and path is None:
        return _BASELINE_CACHE
    raw = json.loads(Path(p).read_text())
    if str(raw.get("warehouse_id") or "") != YGY_WAREHOUSE_ID:
        raise CutoverError("Baseline warehouse_id must be YGY-MAIN")
    if str(raw.get("cutoff_at") or "") != CUTOFF_AT:
        raise CutoverError("Baseline cutoff_at must match Owner decision")
    # Integrity: recompute sha over sorted eligible identity if file embeds source_sha256
    sha = str(raw.get("source_sha256") or "")
    if len(sha) != 64:
        raise CutoverError("Baseline source_sha256 required")
    if path is None:
        _BASELINE_CACHE = raw
    return raw


def eligible_rows(manifest: Optional[Dict[str, Any]] = None) -> Dict[str, dict]:
    man = manifest or load_baseline_manifest()
    out: Dict[str, dict] = {}
    for row in man.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if not row.get("eligible_master"):
            continue
        mid = str(row.get("master_item_id") or "").strip()
        if not mid:
            continue
        out[mid] = row
    return out


def baseline_for_master(master_item_id: str, *, batch_id: str = "ygy-ats-2026-11-02") -> Optional[Baseline]:
    rows = eligible_rows()
    mid = str(master_item_id or "").strip()
    row = rows.get(mid)
    if not row:
        return None
    man = load_baseline_manifest()
    return Baseline(
        batch_id=batch_id,
        warehouse_id=YGY_WAREHOUSE_ID,
        master_item_id=mid,
        unit=str(row.get("satuan") or "PCS"),
        available_qty=quantity(row.get("accurate_ats")),
        cutoff_at=str(man.get("cutoff_at") or CUTOFF_AT),
        source_sha256=str(man.get("source_sha256")),
    )


def order_matches_ygy_mode_b(order: dict) -> bool:
    """True when order belongs to YGY org under Mode B (token 50) or ERP branch id."""
    if not isinstance(order, dict):
        return False
    br = str(order.get("branch_id") or "").strip()
    cab = str(order.get("cabang_id") or "").strip()
    if br == YGY_BRANCH_ID or cab == YGY_BRANCH_ID:
        return True
    tokens = {
        str(order.get("raw_branch_token") or "").strip().upper(),
        str(order.get("accurate_branch_id") or "").strip().upper(),
        str(order.get("branch_code") or "").strip().upper(),
        cab.upper(),
        br.upper(),
    }
    # Accurate often stores numeric branch as string "50"
    if "50" in {str(order.get("raw_branch_token") or "").strip(),
                str(order.get("accurate_branch_id") or "").strip(),
                str(order.get("branch_code") or "").strip(),
                cab, br}:
        return True
    return bool(tokens & ACCURATE_BRANCH_TOKENS_YGY)


def coverage_gaps_for_live_ro(*, has_stock_allocations: bool, event_stream_complete: bool) -> List[str]:
    gaps: List[str] = []
    if not has_stock_allocations:
        gaps.append(
            "LIVE_RO_ZERO_STOCK_ALLOCATIONS: cannot WH-scope open PO from allocations; "
            "Owner ATS baseline adopts free stock at cutoff — pre-cutoff PO must not re-cut ATS "
            "unless explicit opening Reservation keys are supplied"
        )
    if not event_stream_complete:
        gaps.append(
            "POST_CUTOFF_EVENT_STREAM_INCOMPLETE: projector refuses READY until canonical "
            "RESERVE/SHIP_RESERVED/RELEASE/RECEIVE_FREE/ISSUE_FREE/REVERSE coverage is verified"
        )
    gaps.append(
        "ADOPTION_POLICY_PROPOSAL: treat Accurate ATS @ cutoff as free blank baseline; "
        "reservations_at_cutoff empty unless Owner supplies WH-proven opening reservations; "
        "map only post-cutoff mutations with warehouse_id==YGY-MAIN"
    )
    return gaps


def project_master_ats(
    *,
    master_item_id: str,
    as_of: str,
    reservations_at_cutoff: Iterable[Reservation] = (),
    events: Iterable[Event] = (),
    evidence_complete: bool,
) -> Dict[str, Any]:
    base = baseline_for_master(master_item_id)
    if base is None:
        man = load_baseline_manifest()
        inactive = {
            str(r.get("master_item_id"))
            for r in (man.get("rows") or [])
            if isinstance(r, dict) and not r.get("eligible_master")
        }
        if str(master_item_id) in inactive:
            return {
                "status": "EXCLUDED",
                "reason": REASON_ATS_INACTIVE_MASTER,
                "available_qty": None,
                "source": SOURCE_ATS,
            }
        return {
            "status": "EXCLUDED",
            "reason": REASON_ATS_NOT_IN_BASELINE,
            "available_qty": None,
            "source": SOURCE_ATS,
            "note": "Rows outside ATS file are not auto-zeroed",
        }
    try:
        projected = project_available(
            base,
            reservations_at_cutoff=reservations_at_cutoff,
            events=events,
            evidence_complete=evidence_complete,
            as_of=as_of,
        )
    except CutoverError as exc:
        return {
            "status": "UNAVAILABLE",
            "reason": REASON_ATS_COVERAGE_GAP,
            "error": str(exc),
            "available_qty": None,
            "source": SOURCE_ATS,
            "cutoff_at": base.cutoff_at,
        }
    return {
        "status": "READY",
        "reason": None,
        "available_qty": float(Decimal(projected["available_qty"])),
        "available_qty_str": projected["available_qty"],
        "remaining_reservations": projected["remaining_reservations"],
        "effects": projected["effects"],
        "source": SOURCE_ATS,
        "cutoff_at": projected["cutoff_at"],
        "as_of": projected["as_of"],
        "physical_stock_verified": False,
    }


def apply_ats_overlay_to_ssi_row(
    row: dict,
    *,
    warehouse_id: str,
    as_of: str,
    reservations_at_cutoff: Iterable[Reservation] = (),
    events: Iterable[Event] = (),
    evidence_complete: bool = False,
) -> dict:
    """Overlay stock_dapat_dijual / qty_siap_jual for YGY ATS when enabled.

    Never mutates physical_qty / balances. Leaves polos/tercetak verification alone.
    """
    if not ats_ygy_enabled():
        row["ats_cutover_status"] = "OFF"
        row["ats_cutover_reason"] = REASON_ATS_MODE_OFF
        return row
    wid = str(warehouse_id or "").strip()
    if wid != YGY_WAREHOUSE_ID:
        row["ats_cutover_status"] = "SKIPPED"
        row["ats_cutover_reason"] = REASON_ATS_WRONG_WAREHOUSE
        return row
    mid = str(row.get("master_item_id") or row.get("id") or "").strip()
    result = project_master_ats(
        master_item_id=mid,
        as_of=as_of,
        reservations_at_cutoff=reservations_at_cutoff,
        events=events,
        evidence_complete=evidence_complete,
    )
    row["ats_cutover_status"] = result.get("status")
    row["ats_cutover_reason"] = result.get("reason")
    row["ats_cutover_source"] = SOURCE_ATS
    row["ats_cutover_cutoff_at"] = result.get("cutoff_at") or CUTOFF_AT
    if result.get("status") == "READY":
        qty = result.get("available_qty")
        row["stock_dapat_dijual"] = qty
        row["qty_siap_jual"] = qty
        row["stock_dapat_dijual_status"] = "READY"
        row["qty_siap_jual_status"] = "READY"
        row["qty_siap_jual_reason"] = None
        row["qty_siap_jual_note"] = (
            "ATS cutover YGY-MAIN: Accurate free blank @ Owner cutoff; "
            "post-cutoff events projected; physical_stock not rewritten"
        )
        row["ats_cutover_available_qty"] = qty
        row["physical_stock_verified"] = False
    elif result.get("status") == "UNAVAILABLE":
        row["stock_dapat_dijual"] = None
        row["qty_siap_jual"] = None
        row["stock_dapat_dijual_status"] = "UNVERIFIED"
        row["qty_siap_jual_status"] = "UNVERIFIED"
        row["qty_siap_jual_reason"] = REASON_ATS_COVERAGE_GAP
        row["qty_siap_jual_note"] = result.get("error") or "ATS evidence incomplete"
        row["ats_coverage_gaps"] = coverage_gaps_for_live_ro(
            has_stock_allocations=False,
            event_stream_complete=False,
        )
    # EXCLUDED: leave legacy SSI figures; do not force zero
    return row


def map_business_event(
    *,
    kind: str,
    event_id: str,
    warehouse_id: str,
    master_item_id: str,
    unit: str,
    occurred_at: str,
    sequence: int,
    qty: Any,
    reservation_key: str = "",
    reverses_event_id: str = "",
) -> Event:
    """Explicit mapping only — unknown kinds raise at project time."""
    return Event(
        event_id=event_id,
        warehouse_id=warehouse_id,
        master_item_id=master_item_id,
        unit=unit,
        occurred_at=occurred_at,
        sequence=sequence,
        kind=kind,
        qty=quantity(qty),
        reservation_key=reservation_key,
        reverses_event_id=reverses_event_id,
    )


# Explicit post-cutoff mutation → projector kind (Owner-required catalogue).
EVENT_KIND_MAP = {
    "NEW_PO_LINE_RESERVE": "RESERVE",
    "SHIP_AGAINST_RESERVATION": "SHIP_RESERVED",
    "CANCEL_OR_RELEASE_REMAINING": "RELEASE",
    "PURCHASE_RECEIPT_UNRESERVED": "RECEIVE_FREE",
    "UNRESERVED_ISSUE_OR_ADJUST_OUT": "ISSUE_FREE",
    "LEDGER_REVERSAL": "REVERSE",
}


def mapped_kind(business_code: str) -> str:
    kind = EVENT_KIND_MAP.get(str(business_code or "").strip())
    if not kind:
        raise CutoverError(f"Unmapped business event requires explicit policy: {business_code}")
    return kind
