"""Non-mutating M1 parent-link quantity simulation (test-only).

Pinned engine semantics: SHA 34e8008ab7d33a43a1aafad5bbd76efc83e0d140

- Synthetic fixtures only (no production Mongo).
- Parent changes applied on in-memory copies.
- write_authorized must remain false.
- Does not mutate application endpoints or production logic.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from quantity_rules import is_process_only_service_line, line_physical_fulfillment
from services.order_line_addon import resolve_parent_idx
from services.stock_compatibility_read import (
    _physical_delivery_master_id,
    aggregate_legacy_delivered_qty,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "m1_qty_sim"
MASTER_A = "mid-phys-a-test"
MASTER_B = "mid-phys-b-test"
MASTER_SVC = "mid-svc-test"
PROCESS_IDS: Set[str] = {MASTER_SVC}


def _load_json(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_order(stem: str) -> dict:
    return _load_json(f"{stem}.json")


def _load_plans() -> dict:
    return _load_json("plans.json")


def _sj_qty_by_role(order: dict, process_ids: Set[str]) -> Tuple[float, float]:
    """Sum confirmation/shipment line qtys split physical vs SERVICE master."""
    lines = list(order.get("line_items") or [])
    physical = 0.0
    service = 0.0
    # Prefer confirmation items when present (matches engine path).
    conf_items: List[dict] = []
    for conf in order.get("delivery_confirmations") or []:
        if not isinstance(conf, dict):
            continue
        for it in conf.get("items") or []:
            if isinstance(it, dict):
                conf_items.append(it)
    rows = conf_items
    if not rows:
        for sh in order.get("shipments") or []:
            if not isinstance(sh, dict):
                continue
            for ln in sh.get("lines") or []:
                if isinstance(ln, dict):
                    rows.append(ln)
    for row in rows:
        try:
            idx = int(row.get("line_idx"))
            qty = float(row.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(lines):
            continue
        mid = str((lines[idx] or {}).get("master_item_id") or "").strip()
        if mid in process_ids:
            service += max(qty, 0.0)
        elif mid:
            physical += max(qty, 0.0)
    return physical, service


def _freeze_ship_confirm(order: dict) -> dict:
    return {
        "shipments": copy.deepcopy(order.get("shipments") or []),
        "delivery_confirmations": copy.deepcopy(order.get("delivery_confirmations") or []),
        "qty_delivered": order.get("qty_delivered"),
    }


def _find_by_accurate_line_id(lines: List[dict], accurate_line_id: str) -> Optional[int]:
    want = str(accurate_line_id or "").strip()
    hits = [
        i for i, li in enumerate(lines)
        if isinstance(li, dict) and str(li.get("accurate_line_id") or "").strip() == want
    ]
    if len(hits) != 1:
        return None
    return hits[0]


def _simulate_parent_links(order: dict, plan: dict) -> Tuple[dict, str]:
    """Return (order_copy, status). Never mutates input. Never writes DB."""
    status = str(plan.get("status") or "")
    if status == "BLOCKED_MULTI_CANDIDATE":
        return copy.deepcopy(order), "BLOCKED_MULTI_CANDIDATE"
    if status in ("NO_LINKS", ""):
        return copy.deepcopy(order), "NO_LINKS"

    out = copy.deepcopy(order)
    lines = list(out.get("line_items") or [])
    for link in plan.get("links") or []:
        s_idx = _find_by_accurate_line_id(lines, link["service_accurate_line_id"])
        p_idx = _find_by_accurate_line_id(lines, link["parent_accurate_line_id"])
        if s_idx is None or p_idx is None:
            return out, "BLOCKED_RESOLVE"
        parent_lid = str(link.get("parent_line_id") or "").strip()
        service_lid = str(link.get("service_line_id") or "").strip()
        if not parent_lid or parent_lid.startswith("idx:"):
            return out, "BLOCKED_NO_LINE_ID"
        if not service_lid or service_lid.startswith("idx:"):
            return out, "BLOCKED_NO_LINE_ID"
        lines[p_idx]["line_id"] = parent_lid
        lines[s_idx]["line_id"] = service_lid
        lines[s_idx]["parent_line_id"] = parent_lid
        lines[s_idx].pop("parent_line_idx", None)
        # PF must remain unchanged (no key delete / flip).
    out["line_items"] = lines
    return out, "SIMULATED"


def _attribute(order: dict) -> Tuple[Dict[str, float], Dict[str, str]]:
    return aggregate_legacy_delivered_qty(
        [order],
        [],
        process_master_ids=PROCESS_IDS,
    )


def _gate_over_attribution(
    attr_after: float,
    physical_sj: float,
    service_sj: float = 0.0,
) -> str:
    """Fail-closed when attributed physical mid exceeds physical SJ evidence.

    Printing SERVICE qty is process work — not additional physical delivery ceiling.
    ``service_sj`` retained for callers/logging only.
    """
    _ = service_sj
    ceiling = physical_sj
    if attr_after > ceiling + 1e-9:
        return "BLOCKED_OVER_ATTR"
    return "PASS"


# ---------------------------------------------------------------------------
# Meta / safety
# ---------------------------------------------------------------------------


def test_plans_write_authorized_false():
    plans = _load_plans()
    assert plans.get("write_authorized") is False


def test_no_motor_mongo_imported_by_this_module():
    """Simulation module must not pull production DB clients."""
    import ast
    import test_m1_qty_parent_simulation as mod

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    banned_roots = {"motor", "pymongo", "database", "server"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                assert root not in banned_roots, f"banned import: {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            assert root not in banned_roots, f"banned from-import: {node.module}"
    assert not hasattr(mod, "db")
    assert not hasattr(mod, "client")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_single_physical_plus_service_before_after():
    order = _load_order("so_single_svc")
    plan = _load_plans()["plans"]["so_single_svc"]
    frozen = _freeze_ship_confirm(order)

    before_d, before_p = _attribute(order)
    phys_sj, svc_sj = _sj_qty_by_role(order, PROCESS_IDS)
    assert phys_sj == 1000.0
    assert svc_sj == 1000.0

    # Before (narrow skip): unlinked SERVICE does not blast-unproven sibling physical.
    # Native physical SJ credits; SERVICE line skipped (no invent parent).
    assert before_p.get(MASTER_A) == "confirmed_shipment"
    assert MASTER_SVC not in before_d  # process mid never accumulates
    attr_before = float(before_d.get(MASTER_A, 0.0))
    assert attr_before == phys_sj == 1000.0

    sim, status = _simulate_parent_links(order, plan)
    assert status == "SIMULATED"
    assert _freeze_ship_confirm(order) == frozen  # input untouched
    assert _freeze_ship_confirm(sim) == frozen  # shipments/confirmations unchanged

    # PF null stays null / absent-false
    assert line_physical_fulfillment(sim["line_items"][1]) is False
    assert resolve_parent_idx(sim["line_items"][1], sim["line_items"]) == 0
    assert (
        _physical_delivery_master_id(sim["line_items"], 1, {}, PROCESS_IDS) == MASTER_A
    )

    after_d, after_p = _attribute(sim)
    attr_after = float(after_d.get(MASTER_A, 0.0))
    assert MASTER_SVC not in after_d
    assert after_p.get(MASTER_A) == "confirmed_shipment"
    # Alt-3: native physical SJ only — SERVICE must not multiply delivery.
    assert attr_after == phys_sj == 1000.0
    assert attr_after != phys_sj + svc_sj
    assert _gate_over_attribution(attr_after, phys_sj, svc_sj) == "PASS"
    # Report delta for humans / CI log
    print(
        "single_svc before/after",
        {"attr_A_before": attr_before, "attr_A_after": attr_after, "prov": after_p.get(MASTER_A)},
    )


def test_dual_service_colors_remap_once_to_parent():
    order = _load_order("so_dual_svc")
    plan = _load_plans()["plans"]["so_dual_svc"]
    frozen = _freeze_ship_confirm(order)
    phys_sj, svc_sj = _sj_qty_by_role(order, PROCESS_IDS)
    assert phys_sj == 1000.0
    assert svc_sj == 2000.0

    before_d, before_p = _attribute(order)
    assert before_p.get(MASTER_A) == "confirmed_shipment"

    sim, status = _simulate_parent_links(order, plan)
    assert status == "SIMULATED"
    assert _freeze_ship_confirm(sim) == frozen
    assert resolve_parent_idx(sim["line_items"][1], sim["line_items"]) == 0
    assert resolve_parent_idx(sim["line_items"][2], sim["line_items"]) == 0

    after_d, after_p = _attribute(sim)
    attr_after = float(after_d.get(MASTER_A, 0.0))
    assert MASTER_SVC not in after_d
    assert after_p.get(MASTER_A) == "confirmed_shipment"
    # Two colors remap onto one physical mid — not a second inventory mid.
    assert list(after_d.keys()) == [MASTER_A]
    # Alt-3: colors do not multiply physical delivered qty.
    assert attr_after == phys_sj == 1000.0
    assert attr_after != 3000.0
    assert _gate_over_attribution(attr_after, phys_sj, svc_sj) == "PASS"
    print("dual_svc before/after", {"attr_A_before": before_d.get(MASTER_A), "attr_A_after": attr_after})


def test_pf_true_dual_service_flag_unchanged_and_remap():
    order = _load_order("so_pf_dual")
    plan = _load_plans()["plans"]["so_pf_dual"]
    assert plan.get("physical_fulfillment_decision") == "KEEP_PF_AND_LINK_PARENT"

    before_pf = [
        line_physical_fulfillment(li) for li in order["line_items"] if li.get("item_type") == "SERVICE"
    ]
    assert before_pf == [True, True]
    assert is_process_only_service_line(order["line_items"][1]) is False

    frozen = _freeze_ship_confirm(order)
    phys_sj, svc_sj = _sj_qty_by_role(order, PROCESS_IDS)

    before_d, before_p = _attribute(order)
    assert before_p.get(MASTER_A) == "confirmed_shipment"

    sim, status = _simulate_parent_links(order, plan)
    assert status == "SIMULATED"
    assert _freeze_ship_confirm(sim) == frozen

    after_pf = [
        line_physical_fulfillment(li) for li in sim["line_items"] if li.get("item_type") == "SERVICE"
    ]
    assert after_pf == [True, True], "PF=true must remain unchanged in simulation"

    after_d, after_p = _attribute(sim)
    attr_after = float(after_d.get(MASTER_A, 0.0))
    assert after_p.get(MASTER_A) == "confirmed_shipment"
    # PF=true must not invent extra physical delivery on top of native SJ.
    assert attr_after == phys_sj == 1000.0
    assert attr_after != phys_sj + svc_sj
    assert _gate_over_attribution(attr_after, phys_sj, svc_sj) == "PASS"
    print("pf_dual before/after", {"attr_A_before": before_d.get(MASTER_A), "attr_A_after": attr_after, "pf": after_pf})


def test_multi_phys_blocked_without_exclusive_parent():
    order = _load_order("so_multi_phys")
    plan = _load_plans()["plans"]["so_multi_phys"]
    assert plan["status"] == "BLOCKED_MULTI_CANDIDATE"
    assert plan.get("links") == []

    frozen = _freeze_ship_confirm(order)
    before_d, before_p = _attribute(order)

    sim, status = _simulate_parent_links(order, plan)
    assert status == "BLOCKED_MULTI_CANDIDATE"
    assert _freeze_ship_confirm(sim) == frozen
    # No parent applied
    assert sim["line_items"][1].get("parent_line_id") in (None, "")
    after_d, after_p = _attribute(sim)
    # Parent-link plan stays BLOCKED (ambiguous candidates), but delivery
    # attribution no longer blast-unproven: native physical SJ lines credit.
    assert after_p.get(MASTER_A) == "confirmed_shipment"
    assert after_d.get(MASTER_A) == before_d.get(MASTER_A)
    assert float(after_d.get(MASTER_A, 0.0)) == 1000.0
    print(
        "multi_phys BLOCKED_LINK_but_SJ_ok",
        {
            "candidates": plan.get("candidate_parent_accurate_line_ids"),
            "attr_A": after_d.get(MASTER_A),
            "prov": after_p.get(MASTER_A),
        },
    )


def test_physical_only_known_good_confirmed():
    order = _load_order("so_physical_only_good")
    plan = _load_plans()["plans"]["so_physical_only_good"]
    frozen = _freeze_ship_confirm(order)

    before_d, before_p = _attribute(order)
    sim, status = _simulate_parent_links(order, plan)
    assert status == "NO_LINKS"
    after_d, after_p = _attribute(sim)
    assert _freeze_ship_confirm(sim) == frozen
    assert before_d == after_d
    assert before_p.get(MASTER_A) == "confirmed_shipment"
    assert after_d.get(MASTER_A) == 500.0
    assert MASTER_SVC not in after_d
    print("physical_only", {"attr_A": after_d.get(MASTER_A), "prov": after_p.get(MASTER_A)})


def test_item_b_ready_regression_fixture():
    order = _load_order("so_item_b_ready")
    plan = _load_plans()["plans"]["so_item_b_ready"]
    frozen = _freeze_ship_confirm(order)

    before_d, before_p = _attribute(order)
    sim, status = _simulate_parent_links(order, plan)
    assert status == "NO_LINKS"
    after_d, after_p = _attribute(sim)
    assert _freeze_ship_confirm(sim) == frozen
    assert MASTER_A not in after_d
    assert after_p.get(MASTER_B) == "confirmed_shipment"
    assert after_d.get(MASTER_B) == 200.0
    # Belum-shaped expectation: completed==delivered → Belum 0 if catalog used these numbers
    # (we do not force Belum here — only attribution READY-shaped provenance)
    assert before_d == after_d
    print("item_b", {"attr_B": after_d.get(MASTER_B), "prov": after_p.get(MASTER_B)})


def test_over_attribution_gate_detects_excess():
    """Unit-level gate: attributed > physical SJ evidence => BLOCKED."""
    assert _gate_over_attribution(1001.0, 1000.0, 2000.0) == "BLOCKED_OVER_ATTR"
    assert _gate_over_attribution(1000.0, 1000.0, 2000.0) == "PASS"
    assert _gate_over_attribution(2000.0, 1000.0, 1000.0) == "BLOCKED_OVER_ATTR"


def test_input_order_not_mutated_by_simulation():
    order = _load_order("so_single_svc")
    plan = _load_plans()["plans"]["so_single_svc"]
    snapshot = copy.deepcopy(order)
    _simulate_parent_links(order, plan)
    assert order == snapshot
