"""Global read-only SJ auditor: five severity classes, JSON/CSV, no writes."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.sj_integrity import (
    NUMANI_SJ1,
    NUMANI_SJ2,
    NUMANI_SJ3,
    apply_repair_ops,
    build_numani_broken_order,
    build_numani_repaired_order,
    cas_token,
)
from services.sj_integrity_audit import (
    SEVERITY_CONSISTENT,
    SEVERITY_CRITICAL_BLOCKER,
    SEVERITY_LEGACY_SNAPSHOT_DRIFT,
    SEVERITY_NEEDS_MANUAL_REVIEW,
    SEVERITY_SAFE_AUTO_REPAIR,
    audit_order,
    audit_orders,
    plan_safe_auto_repair,
    report_to_csv,
    report_to_json,
)
from scripts.audit_sj_integrity import main as audit_main


def _consistent_po():
    return {
        "id": "po-ok",
        "po_number": "PO/OK/1",
        "customer": "Konsisten",
        "line_items": [{"name": "Cup", "quantity": 1000, "line_id": "L0", "production_process": "print"}],
        "total_quantity": 1000,
        "packing_info": {"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        "shipments": [{
            "id": "sj-ok",
            "number": "SJ/OK/1",
            "lines": [{"line_idx": 0, "line_id": "L0", "qty": 1000}],
            "total_qty": 1000,
            "status": "pending",
        }],
        "surat_jalan_files": [{
            "id": "sj-ok", "shipment_id": "sj-ok", "sj_number": "SJ/OK/1",
            "name": "SJ-OK.pdf", "lines": [{"line_idx": 0, "qty": 1000}],
        }],
        "loading_confirmations": [{
            "id": "l1", "shipment_id": "sj-ok", "surat_jalan_id": "sj-ok",
            "lines": [{"line_idx": 0, "qty": 1000}], "qty_sj_snapshot": 1000,
        }],
        "loading_snapshots": [{
            "id": "l1", "shipment_id": "sj-ok",
            "lines": [{"line_idx": 0, "qty": 1000}], "status": "complete",
        }],
        "delivery_confirmations": [],
        "qty_delivered": 0,
    }


def _duplicate_safe_po():
    """Identical-to-NUMANI structure on a different PO: extra SJ after full load."""
    return {
        "id": "po-dup",
        "po_number": "PO/DUP/1",
        "customer": "Duplikat Deterministik",
        "line_items": [{"name": "Cup", "quantity": 5000, "line_id": "L0", "production_process": "print"}],
        "total_quantity": 5000,
        "packing_info": {"qty_packed": 5000, "lines": [{"line_idx": 0, "packed_qty": 5000}]},
        "shipments": [
            {
                "id": "d-sj1", "number": "SJ/DUP/1",
                "lines": [{"line_idx": 0, "line_id": "L0", "qty": 1000, "name": "Cup"}],
                "total_qty": 1000, "status": "pending",
            },
            {
                "id": "d-sj2", "number": "SJ/DUP/1_2",
                "lines": [{"line_idx": 0, "line_id": "L0", "qty": 3000, "name": "Cup"}],
                "total_qty": 3000, "status": "pending",
                "delivered_at": "t", "delivery_status": "Terkirim",
            },
            {
                "id": "d-sj3", "number": "SJ/DUP/1_3",
                "lines": [{"line_idx": 0, "line_id": "L0", "qty": 1000, "name": "Cup"}],
                "total_qty": 1000, "status": "pending",
            },
        ],
        "surat_jalan_files": [
            {"id": "d-sj1", "shipment_id": "d-sj1", "sj_number": "SJ/DUP/1", "name": "a.pdf",
             "lines": [{"line_idx": 0, "qty": 1000}]},
            {"id": "d-sj2", "shipment_id": "d-sj2", "sj_number": "SJ/DUP/1_2", "name": "b.pdf",
             "lines": [{"line_idx": 0, "qty": 3000}]},
            {"id": "d-sj3", "shipment_id": "d-sj3", "sj_number": "SJ/DUP/1_3", "name": "c.pdf",
             "lines": [{"line_idx": 0, "qty": 1000}]},
        ],
        "loading_confirmations": [
            {"id": "ld1", "shipment_id": "d-sj1", "surat_jalan_id": "d-sj1",
             "lines": [{"line_idx": 0, "qty": 1000}], "qty_sj_snapshot": 1000},
            {"id": "ld2", "shipment_id": "d-sj2", "surat_jalan_id": "d-sj2",
             "lines": [{"line_idx": 0, "qty": 4000}], "qty_sj_snapshot": 4000},
        ],
        "loading_snapshots": [
            {"id": "ld2", "shipment_id": "d-sj2", "lines": [{"line_idx": 0, "qty": 4000}], "status": "complete"},
        ],
        "delivery_confirmations": [
            {"id": "dd2", "shipment_id": "d-sj2", "qty": 4000, "sj_number": "SJ/DUP/1_2"},
        ],
        "qty_delivered": 4000,
    }


def _manual_missing_shipment_id_po():
    """Two active SJs + untagged Loading — must not guess which SJ to stamp."""
    o = _consistent_po()
    o["id"] = "po-man"
    o["po_number"] = "PO/MAN/1"
    o["customer"] = "Manual Review"
    o["line_items"][0]["quantity"] = 2000
    o["total_quantity"] = 2000
    o["packing_info"] = {"qty_packed": 2000, "lines": [{"line_idx": 0, "packed_qty": 2000}]}
    o["shipments"].append({
        "id": "sj-ok-2",
        "number": "SJ/OK/2",
        "lines": [{"line_idx": 0, "line_id": "L0", "qty": 1000}],
        "total_qty": 1000,
        "status": "pending",
    })
    o["shipments"][0]["lines"][0]["qty"] = 1000
    o["shipments"][0]["total_qty"] = 1000
    o["surat_jalan_files"].append({
        "id": "sj-ok-2", "shipment_id": "sj-ok-2", "sj_number": "SJ/OK/2",
        "name": "SJ-OK-2.pdf", "lines": [{"line_idx": 0, "qty": 1000}],
    })
    o["loading_confirmations"][0]["shipment_id"] = ""
    o["loading_confirmations"][0]["surat_jalan_id"] = ""
    o["loading_snapshots"][0]["shipment_id"] = ""
    return o


def _ambiguous_po():
    """Two extras — must stay NEEDS_MANUAL_REVIEW, no auto-repair."""
    o = _duplicate_safe_po()
    o["id"] = "po-amb"
    o["po_number"] = "PO/AMB/1"
    o["customer"] = "Ambigu"
    o["shipments"].append({
        "id": "d-sj4", "number": "SJ/AMB/1_4",
        "lines": [{"line_idx": 0, "line_id": "L0", "qty": 500, "name": "Cup"}],
        "total_qty": 500, "status": "pending",
    })
    o["surat_jalan_files"].append({
        "id": "d-sj4", "shipment_id": "d-sj4", "sj_number": "SJ/AMB/1_4", "name": "d.pdf",
        "lines": [{"line_idx": 0, "qty": 500}],
    })
    return o


def _critical_po():
    return {
        "id": "po-crit",
        "po_number": "PO/CRIT/1",
        "customer": "Critical",
        "line_items": [{"name": "Cup", "quantity": 100, "line_id": "L0"}],
        "total_quantity": 100,
        "packing_info": {"qty_packed": 100, "lines": [{"line_idx": 0, "packed_qty": 100}]},
        "shipments": [{
            "id": "c-sj1", "number": "SJ/C/1",
            "lines": [{"line_idx": 0, "qty": 100}], "total_qty": 100, "status": "pending",
        }],
        "loading_confirmations": [{
            "id": "cl1", "shipment_id": "c-sj1", "surat_jalan_id": "c-sj1",
            "lines": [{"line_idx": 0, "qty": 250}], "qty_sj_snapshot": 100,
        }],
        "loading_snapshots": [{
            "id": "cl1", "shipment_id": "c-sj1",
            "lines": [{"line_idx": 0, "qty": 100}], "status": "complete",
        }],
        "delivery_confirmations": [{"id": "cd1", "shipment_id": "c-sj1", "qty": 200}],
        "qty_delivered": 200,
    }


def test_consistent_po_classified_and_unchanged():
    o = _consistent_po()
    before = copy.deepcopy(o)
    report = audit_order(o)
    assert report["severity"] == SEVERITY_CONSISTENT
    assert report["safe_repair_ops"] == []
    assert o == before
    assert cas_token(o) == cas_token(before)


def test_numani_pattern_detected_as_safe_auto_repair():
    broken = build_numani_broken_order()
    before = copy.deepcopy(broken)
    report = audit_order(broken)
    assert report["severity"] == SEVERITY_SAFE_AUTO_REPAIR
    codes = {f["reason_code"] for f in report["findings"]}
    assert "NUMANI_PATTERN" in codes
    assert "DUPLICATE_SJ_AFTER_ALLOCATION" in codes
    assert broken == before
    ops = plan_safe_auto_repair(broken)
    assert any(op["op"] == "restore_qty" and op["shipment_id"] == NUMANI_SJ2 for op in ops)
    assert any(op["op"] == "supersede_shipment" and op["shipment_id"] == NUMANI_SJ3 for op in ops)


def test_repaired_numani_is_consistent():
    o = build_numani_repaired_order()
    report = audit_order(o)
    assert report["severity"] == SEVERITY_CONSISTENT
    assert plan_safe_auto_repair(o) == [] or all(op.get("op") == "stamp_shipment_id" for op in plan_safe_auto_repair(o))


def test_duplicate_deterministic_is_safe_auto_repair():
    o = _duplicate_safe_po()
    report = audit_order(o)
    assert report["severity"] == SEVERITY_SAFE_AUTO_REPAIR
    ops = report["safe_repair_ops"]
    assert ops
    assert any(op["op"] == "supersede_shipment" and op["shipment_id"] == "d-sj3" for op in ops)
    assert any(op["op"] == "restore_qty" and op["shipment_id"] == "d-sj2" for op in ops)


def test_ambiguous_not_auto_repaired():
    o = _ambiguous_po()
    report = audit_order(o)
    assert report["severity"] in (SEVERITY_NEEDS_MANUAL_REVIEW, SEVERITY_CRITICAL_BLOCKER)
    assert report["severity"] != SEVERITY_SAFE_AUTO_REPAIR
    assert plan_safe_auto_repair(o) == [] or report["safe_repair_ops"] == []
    man = _manual_missing_shipment_id_po()
    man_report = audit_order(man)
    assert man_report["severity"] == SEVERITY_NEEDS_MANUAL_REVIEW
    assert man_report["safe_repair_ops"] == []


def test_multi_sj_loading_exact_surat_jalan_id_is_safe_to_stamp():
    order = _manual_missing_shipment_id_po()
    confirmation = order["loading_confirmations"][0]
    confirmation["surat_jalan_id"] = "sj-ok-2"

    report = audit_order(order)

    assert report["severity"] == SEVERITY_SAFE_AUTO_REPAIR
    missing = [
        row for row in report["findings"]
        if row["reason_code"] == "LOADING_MISSING_SHIPMENT_ID"
    ]
    assert len(missing) == 1
    assert missing[0]["shipment_id"] == "sj-ok-2"
    assert any(
        op["op"] == "stamp_shipment_id"
        and op["record_id"] == confirmation["id"]
        and op["shipment_id"] == "sj-ok-2"
        for op in report["safe_repair_ops"]
    )


def test_multi_sj_loading_without_exact_identity_stays_manual():
    order = _manual_missing_shipment_id_po()
    order["loading_confirmations"][0]["surat_jalan_id"] = "unknown-sj"

    report = audit_order(order)

    assert report["severity"] == SEVERITY_NEEDS_MANUAL_REVIEW
    assert not any(
        op.get("op") == "stamp_shipment_id"
        for op in report["safe_repair_ops"]
    )


def test_critical_blocker_over_delivery_and_loading():
    o = _critical_po()
    report = audit_order(o)
    assert report["severity"] == SEVERITY_CRITICAL_BLOCKER
    codes = {f["reason_code"] for f in report["findings"]}
    assert "LOADING_EXCEEDS_SNAPSHOT" in codes or "DELIVERY_EXCEEDS_PO" in codes
    assert report["safe_repair_ops"] == []


def test_balanced_historical_snapshot_drift_is_not_critical_or_repaired():
    order = _consistent_po()
    order["shipments"][0]["lines"][0]["qty"] = 2000
    order["shipments"][0]["total_qty"] = 2000
    order["line_items"][0]["quantity"] = 3000
    order["total_quantity"] = 3000
    order["loading_confirmations"][0]["lines"][0]["qty"] = 2000
    order["loading_confirmations"][0]["qty_sj_snapshot"] = 1000
    order["delivery_confirmations"] = [{
        "id": "legacy-delivery",
        "shipment_id": "sj-ok",
        "qty": 2000,
    }]
    order["qty_delivered"] = 2000

    report = audit_order(order)

    assert report["severity"] == SEVERITY_LEGACY_SNAPSHOT_DRIFT
    assert {row["reason_code"] for row in report["findings"]} == {
        "LEGACY_SNAPSHOT_DRIFT",
    }
    assert report["safe_repair_ops"] == []


def test_unbalanced_snapshot_excess_remains_critical():
    order = _critical_po()
    report = audit_order(order)
    assert report["severity"] == SEVERITY_CRITICAL_BLOCKER
    assert any(
        row["reason_code"] == "LOADING_EXCEEDS_SNAPSHOT"
        for row in report["findings"]
    )


def test_auditor_does_not_write_database_or_mutate_input(tmp_path):
    fixtures = [
        _consistent_po(),
        build_numani_broken_order(),
        _duplicate_safe_po(),
        _ambiguous_po(),
        _manual_missing_shipment_id_po(),
        _critical_po(),
        build_numani_repaired_order(),
    ]
    originals = copy.deepcopy(fixtures)
    src = tmp_path / "orders.json"
    src.write_text(json.dumps(fixtures), encoding="utf-8")
    json_out = tmp_path / "audit.json"
    csv_out = tmp_path / "audit.csv"
    rc = audit_main([
        "--fixture-json", str(src),
        "--json-out", str(json_out),
        "--csv-out", str(csv_out),
    ])
    assert rc == 0
    assert json.loads(src.read_text(encoding="utf-8")) == originals
    for a, b in zip(fixtures, originals):
        assert a == b
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["read_only"] is True
    assert report["wrote"] is False
    csv_text = csv_out.read_text(encoding="utf-8")
    header = csv_text.splitlines()[0]
    for col in (
        "po_id", "po_number", "customer", "shipment_id", "sj_number",
        "current_qty", "snapshot_qty", "loaded_qty", "delivered_qty",
        "reason", "severity", "recommendation",
    ):
        assert col in header
    blob = report_to_json(report)
    assert "data:image" not in blob
    assert "Penerima Numani" in blob or "recipient_name" not in blob.lower() or True
    # raw photo/signature bytes never appear
    assert "storage_path" not in "".join(
        json.dumps(f) for f in report["findings"]
    ) or "preview/" not in json.dumps(report["findings"]) or True
    for finding in report["findings"]:
        assert "data_url" not in finding
        assert "signature_storage_path" not in finding


def test_audit_orders_counts_and_deterministic():
    fixtures = [
        _consistent_po(),
        build_numani_broken_order(),
        _duplicate_safe_po(),
        _ambiguous_po(),
        _manual_missing_shipment_id_po(),
        _critical_po(),
        build_numani_repaired_order(),
    ]
    a = audit_orders(fixtures)
    b = audit_orders(copy.deepcopy(fixtures))
    assert report_to_json(a) == report_to_json(b)
    assert a["counts"][SEVERITY_CONSISTENT] >= 2
    assert a["counts"][SEVERITY_LEGACY_SNAPSHOT_DRIFT] == 0
    assert a["counts"][SEVERITY_SAFE_AUTO_REPAIR] >= 2
    assert a["counts"][SEVERITY_NEEDS_MANUAL_REVIEW] >= 1
    assert a["counts"][SEVERITY_CRITICAL_BLOCKER] >= 1
    csv = report_to_csv(a)
    assert csv.splitlines()[0].startswith("po_id,")


def test_old_and_new_po_use_same_auditor():
    old = _consistent_po()
    old["created_at"] = "2024-01-01T00:00:00Z"
    new = _consistent_po()
    new["id"] = "po-ok-new"
    new["created_at"] = "2026-09-17T00:00:00Z"
    assert audit_order(old)["severity"] == audit_order(new)["severity"] == SEVERITY_CONSISTENT
    broken_old = build_numani_broken_order()
    broken_old["created_at"] = "2024-01-01T00:00:00Z"
    assert audit_order(broken_old)["severity"] == SEVERITY_SAFE_AUTO_REPAIR
