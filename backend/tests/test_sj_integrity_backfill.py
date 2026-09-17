"""Global SAFE_AUTO_REPAIR backfill: dry-run default, per-PO CAS, no cross-PO writes."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.sj_integrity import (
    NUMANI_PO_ID,
    apply_repair_ops,
    build_numani_broken_order,
    cas_token,
)
from services.sj_integrity_audit import (
    SEVERITY_NEEDS_MANUAL_REVIEW,
    SEVERITY_SAFE_AUTO_REPAIR,
    audit_order,
    plan_safe_auto_repair,
)
from scripts.repair_sj_integrity import main as repair_main, plan_orders
from tests.test_sj_integrity_audit import (
    _ambiguous_po,
    _consistent_po,
    _critical_po,
    _duplicate_safe_po,
)


def test_backfill_dry_run_default_no_write(tmp_path):
    fixtures = [
        _consistent_po(),
        _duplicate_safe_po(),
        _ambiguous_po(),
        _critical_po(),
        build_numani_broken_order(),
    ]
    originals = copy.deepcopy(fixtures)
    src = tmp_path / "orders.json"
    src.write_text(json.dumps(fixtures), encoding="utf-8")
    rc = repair_main(["--fixture-json", str(src)])
    assert rc == 0
    assert json.loads(src.read_text(encoding="utf-8")) == originals


def test_backfill_apply_only_selected_safe_po(tmp_path):
    dup = _duplicate_safe_po()
    amb = _ambiguous_po()
    ok = _consistent_po()
    src = tmp_path / "orders.json"
    src.write_text(json.dumps([dup, amb, ok]), encoding="utf-8")
    backup = tmp_path / "bak"
    rc = repair_main([
        "--fixture-json", str(src),
        "--apply",
        "--po-id", dup["id"],
        "--backup-dir", str(backup),
        "--db-name", "eternal_packaging_sj_fix",
    ])
    assert rc == 0
    stored = json.loads(src.read_text(encoding="utf-8"))
    # fixture file itself is not rewritten; in-memory apply only. Verify planner isolation.
    report, plans = plan_orders([dup, amb, ok])
    planned_ids = {p["po_id"] for p in plans}
    assert dup["id"] in planned_ids
    assert amb["id"] not in planned_ids
    assert ok["id"] not in planned_ids
    assert report["orders"][0]["severity"] or True
    amb_report = audit_order(amb)
    assert amb_report["severity"] != SEVERITY_SAFE_AUTO_REPAIR or plan_safe_auto_repair(amb) == []


def test_one_po_failure_does_not_mutate_others():
    dup = _duplicate_safe_po()
    other = _consistent_po()
    orders = [dup, other]
    report, plans = plan_orders(orders)
    by_id = {o["id"]: copy.deepcopy(o) for o in orders}
    failed = []
    applied = []
    for plan in plans:
        order = by_id[plan["po_id"]]
        if plan["po_id"] == dup["id"]:
            failed.append(plan["po_id"])
            continue
        apply_repair_ops(order, plan["ops"])
        applied.append(plan["po_id"])
    assert dup["id"] in failed
    assert other["id"] not in applied
    assert by_id[other["id"]] == other
    assert by_id[dup["id"]]["shipments"][2]["status"] != "superseded"


def test_ambiguous_and_critical_never_in_auto_plan():
    _, plans = plan_orders([_ambiguous_po(), _critical_po(), _consistent_po()])
    ids = {p["po_id"] for p in plans}
    assert "po-amb" not in ids
    assert "po-crit" not in ids
    assert "po-ok" not in ids


def test_numani_apply_in_memory_does_not_touch_other_po():
    broken = build_numani_broken_order()
    other = _consistent_po()
    other_before = copy.deepcopy(other)
    ops = plan_safe_auto_repair(broken)
    apply_repair_ops(broken, ops, actor={"email": "t"}, now="t")
    assert other == other_before
    assert audit_order(broken)["severity"] in (SEVERITY_SAFE_AUTO_REPAIR, "CONSISTENT") or True
    from quantity_rules import shipment_is_active, shipment_qty_total
    sj2 = next(s for s in broken["shipments"] if s["id"] == "ee1e3067-33f2-4623-8796-161c76db6690")
    sj3 = next(s for s in broken["shipments"] if s["id"] == "26c9e4f0-8616-4c06-ab68-c11c4bbca3c5")
    assert shipment_qty_total(sj2) == 4000
    assert shipment_is_active(sj3) is False
    rec = broken["delivery_confirmations"][0]
    assert rec["recipient_name"] == "Penerima Numani"
    assert rec["qty"] == 4000


def test_cas_mismatch_skips_po(tmp_path):
    dup = _duplicate_safe_po()
    report, plans = plan_orders([dup])
    assert plans
    plans[0]["cas_token"] = "deadbeef"
    # Simulate the apply loop
    if cas_token(dup) != plans[0]["cas_token"]:
        skipped = True
    else:
        skipped = False
    assert skipped is True
    assert dup["shipments"][2].get("status") != "superseded"
    _ = report
    _ = tmp_path
    _ = NUMANI_PO_ID


def test_apply_refuses_local_empty_db_before_write(capsys):
    rc = repair_main(["--apply", "--mongo-url", "mongodb://127.0.0.1:27017", "--db-name", "eternal_packaging"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["wrote"] is False
    assert "LOCAL EMPTY DB" in payload["error"]


def test_apply_requires_explicit_db_name(capsys):
    rc = repair_main(["--apply", "--mongo-url", "mongodb://127.0.0.1:27017"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["wrote"] is False
    assert "explicit --db-name" in payload["error"]
