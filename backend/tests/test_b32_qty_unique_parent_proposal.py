"""B32: qty-unique SERVICE→print parent proposals (offline, no Mongo)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "repair" / "propose_qty_unique_parents.py"


def _load():
    spec = importlib.util.spec_from_file_location("propose_qty_unique_parents", HELPER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_qty_unique_matches_only_matching_print_parent():
    m = _load()
    lines = [
        {"item_type": "INVENTORY", "production_process": "print", "quantity": 10000, "master_item_id": "m031", "item_code": "100031"},
        {"item_type": "INVENTORY", "production_process": "print", "quantity": 500, "master_item_id": "m051", "item_code": "100051"},
        {"item_type": "SERVICE", "production_process": "print", "quantity": 500, "master_item_id": "svc"},
    ]
    prop = m.propose_unique_qty_parent(lines, 2)
    assert prop is not None
    assert prop["parent_line_idx"] == 1
    assert prop["parent_master_id"] == "m051"
    assert prop["rule"] == "QTY_MATCH_UNIQUE_AMONG_PRINT_PARENTS"


def test_qty_ambiguous_when_two_prints_share_qty():
    m = _load()
    lines = [
        {"item_type": "INVENTORY", "production_process": "print", "quantity": 500, "master_item_id": "a"},
        {"item_type": "INVENTORY", "production_process": "print", "quantity": 500, "master_item_id": "b"},
        {"item_type": "SERVICE", "production_process": "print", "quantity": 500, "master_item_id": "svc"},
    ]
    assert m.propose_unique_qty_parent(lines, 2) is None


def test_no_print_parent_yields_none():
    m = _load()
    lines = [
        {"item_type": "INVENTORY", "production_process": "offset", "quantity": 90000, "master_item_id": "np"},
        {"item_type": "SERVICE", "production_process": "print", "quantity": 90000, "master_item_id": "svc"},
    ]
    assert m.propose_unique_qty_parent(lines, 1) is None


def test_b32_allowlist_sha_and_size():
    plan_path = ROOT / "docs" / "repair" / "REPAIR_PLAN_B32_QTY_UNIQUE.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["write_authorized"] is False
    assert plan["order_count"] == 3
    assert plan["service_link_count"] == 5
    assert len(plan["document_ids_allowlisted"]) == 3
    # C_NO_PRINT must stay outside allowlist
    hold = set(plan["class_c_still_hold_order_ids"])
    assert hold.isdisjoint(set(plan["document_ids_allowlisted"]))
    # Import validator pin
    sys.path.insert(0, str(ROOT / "scripts" / "repair"))
    import b32_live_validator as v  # noqa: WPS433

    digest = v._sha256_file(plan_path)
    assert digest == v.EXPECTED_ALLOWLIST_SHA256


def test_b32_validator_offline_fixtures(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts" / "repair"))
    import b32_live_validator as v

    plan = json.loads((ROOT / "docs/repair/REPAIR_PLAN_B32_QTY_UNIQUE.json").read_text())
    svc_mid = "9bfd5938-6868-4d1b-bf9b-6212225134a2"
    for detail in plan["links_detail"]:
        oid = detail["order_id"]
        # Build minimal lines covering max idx
        max_idx = max(detail["service_line_idx"], detail["parent_line_idx"], 4)
        lines = [{"item_type": "INVENTORY", "production_process": "offset", "quantity": 1, "master_item_id": f"np-{i}"} for i in range(max_idx + 1)]
        # Place all print parents for this PO from links_detail sharing order
        siblings = [d for d in plan["links_detail"] if d["order_id"] == oid]
        for d in siblings:
            pidx = d["parent_line_idx"]
            lines[pidx] = {
                "item_type": "INVENTORY",
                "production_process": "print",
                "quantity": d["parent_ordered"],
                "master_item_id": d["parent_master_id"],
                "item_code": d["parent_item_code"],
            }
            sidx = d["service_line_idx"]
            lines[sidx] = {
                "item_type": "SERVICE",
                "production_process": "print",
                "quantity": d["service_ordered"],
                "master_item_id": svc_mid,
            }
        (tmp_path / f"{oid}.json").write_text(
            json.dumps({"id": oid, "po_number": detail["po_number"], "line_items": lines}),
            encoding="utf-8",
        )
    rc = v.main([
        "--allowlist", str(ROOT / "docs/repair/REPAIR_PLAN_B32_QTY_UNIQUE.json"),
        "--table", str(ROOT / "docs/repair/APPROVAL_TABLE_B32.tsv"),
        "--fixture-dir", str(tmp_path),
    ])
    assert rc == 0
