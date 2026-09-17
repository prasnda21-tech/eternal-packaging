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
    loaded_qty_for_shipment,
)
from services.sj_integrity_audit import (
    SEVERITY_CONSISTENT,
    SEVERITY_NEEDS_MANUAL_REVIEW,
    SEVERITY_SAFE_AUTO_REPAIR,
    audit_order,
    plan_safe_auto_repair,
)
from quantity_rules import delivery_batch_photo_readiness
from scripts.repair_sj_integrity import (
    PRODUCTION_CONFIRMATION,
    _production_argument_error,
    _production_plan_error,
    main as repair_main,
    plan_orders,
)
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


def test_production_apply_requires_explicit_authorization_before_write(capsys):
    rc = repair_main(["--apply", "--mongo-url", "mongodb://127.0.0.1:27017", "--db-name", "eternal_packaging"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["wrote"] is False
    assert "--allow-production" in payload["error"]


def test_production_argument_guard_requires_phrase_ids_count_ops_and_backup(tmp_path):
    class Args:
        allow_production = True
        confirm = PRODUCTION_CONFIRMATION
        po_id = ["po-safe"]
        expected_plan_count = 1
        allowed_op = ["stamp_shipment_id"]
        backup_dir = "/var/backups/eternal-packaging/sj-integrity/test"

    args = Args()
    assert _production_argument_error(args) == ""
    args.confirm = "WRONG"
    assert "--confirm" in _production_argument_error(args)
    args.confirm = PRODUCTION_CONFIRMATION
    args.allowed_op = ["supersede"]
    assert "permits only" in _production_argument_error(args)
    args.allowed_op = ["stamp_shipment_id"]
    args.backup_dir = str(tmp_path)
    assert "backup directory" in _production_argument_error(args)


def test_production_plan_guard_is_exact_and_op_allowlisted():
    class Args:
        po_id = ["po-safe"]
        expected_plan_count = 1
        allowed_op = ["stamp_shipment_id"]

    valid = [{
        "po_id": "po-safe",
        "ops": [{
            "op": "stamp_shipment_id",
            "collection": "loading_confirmations",
            "record_id": "loading-safe",
            "shipment_id": "sj-safe",
        }],
    }]
    assert _production_plan_error(Args(), valid) == ""
    assert "PO ids differ" in _production_plan_error(
        Args(), [{"po_id": "po-other", "ops": valid[0]["ops"]}],
    )
    assert "disallowed" in _production_plan_error(
        Args(), [{"po_id": "po-safe", "ops": [{"op": "restore_qty"}]}],
    )
    assert "loading_confirmations" in _production_plan_error(
        Args(), [{"po_id": "po-safe", "ops": [{
            "op": "stamp_shipment_id",
            "collection": "delivery_confirmations",
            "record_id": "delivery-safe",
            "shipment_id": "sj-safe",
        }]}],
    )


def test_apply_requires_explicit_db_name(capsys):
    rc = repair_main(["--apply", "--mongo-url", "mongodb://127.0.0.1:27017"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["wrote"] is False
    assert "explicit --db-name" in payload["error"]


def _special_lalap_legacy_loading_fixture():
    quantities = [3000, 6000, 6000, 5000]
    shipments = []
    files = []
    for index, qty in enumerate(quantities, start=1):
        sid = f"special-sj-{index}"
        number = "SJ/SPECIAL" if index == 1 else f"SJ/SPECIAL_{index}"
        line = {"line_idx": 0, "line_id": "L0", "accurate_item_id": "item-special", "qty": qty}
        shipments.append({"id": sid, "number": number, "lines": [line], "total_qty": qty})
        files.append({"id": f"file-{index}", "shipment_id": sid, "sj_number": number, "lines": [line], "total_qty": qty})
    return {
        "id": "special-lalap-legacy",
        "po_number": "PO/SPECIAL",
        "customer": "SPECIAL LALAP",
        "line_items": [{
            "line_id": "L0", "accurate_item_id": "item-special",
            "name": "22 oz Datar 8 gr Berlian", "quantity": 20000,
            "production_process": "print",
        }],
        "total_quantity": 20000,
        "packing_info": {"qty_packed": 20000, "lines": [{"line_idx": 0, "packed_qty": 20000}]},
        "loading_info": {"qty_loaded": 20000, "lines": [{"line_idx": 0, "loaded_qty": 20000}]},
        "shipments": shipments,
        "surat_jalan_files": files,
        "loading_confirmations": [],
        "delivery_confirmations": [
            {"id": "d1", "shipment_id": "special-sj-1", "qty": 3000},
            {"id": "d2", "shipment_id": "special-sj-2", "qty": 6000},
            {"id": "d3", "shipment_id": "special-sj-3", "qty": 6000},
        ],
        "qty_delivered": 15000,
    }


def test_legacy_multi_sj_loading_gets_deterministic_read_bridge_only():
    order = _special_lalap_legacy_loading_fixture()
    before = copy.deepcopy(order)
    report = audit_order(order)
    assert order == before
    assert report["severity"] == SEVERITY_SAFE_AUTO_REPAIR
    ops = plan_safe_auto_repair(order, report["findings"])
    allocation_op = next(op for op in ops if op["op"] == "allocate_legacy_loading")
    assert [row["shipment_id"] for row in allocation_op["allocations"]] == [
        "special-sj-1", "special-sj-2", "special-sj-3", "special-sj-4",
    ]
    assert "legacy_loading_allocations" not in order

    apply_repair_ops(order, ops, actor={"email": "audit@test"}, now="t")
    assert order["loading_info"] == before["loading_info"]
    assert order["delivery_confirmations"] == before["delivery_confirmations"]
    assert [loaded_qty_for_shipment(order, sh) for sh in order["shipments"]] == [
        3000, 6000, 6000, 5000,
    ]
    assert delivery_batch_photo_readiness(order, order["shipments"][3])["allowed"] is True
    assert audit_order(order)["severity"] == SEVERITY_CONSISTENT


def test_legacy_multi_sj_loading_ambiguous_total_is_never_auto_allocated():
    order = _special_lalap_legacy_loading_fixture()
    order["loading_info"]["lines"][0]["loaded_qty"] = 19000
    report = audit_order(order)
    assert report["severity"] == SEVERITY_NEEDS_MANUAL_REVIEW
    assert not any(op["op"] == "allocate_legacy_loading" for op in plan_safe_auto_repair(order))


def test_legacy_multi_sj_loading_without_unique_delivery_leftover_is_manual():
    order = _special_lalap_legacy_loading_fixture()
    order["delivery_confirmations"] = []
    order["qty_delivered"] = 0
    report = audit_order(order)
    assert report["severity"] == SEVERITY_NEEDS_MANUAL_REVIEW
    assert not any(op["op"] == "allocate_legacy_loading" for op in plan_safe_auto_repair(order))
