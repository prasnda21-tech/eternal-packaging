from __future__ import annotations

import copy

from scripts.reconcile_verified_sj_cases import (
    NUMANI,
    SPECIAL_LALAP,
    apply_verified_repair,
    build_report,
    verify_preconditions,
)


def _shipment(sid, number, qty):
    return {"id": sid, "number": number, "total_qty": qty, "lines": [{"line_idx": 0, "line_id": "L0", "qty": qty}]}


def _file(sid, number, qty):
    return {"id": sid, "shipment_id": sid, "sj_number": number, "total_qty": qty, "lines": [{"line_idx": 0, "line_id": "L0", "qty": qty}]}


def _base(spec):
    rows = []
    files = []
    for sid, (number, qty) in spec["shipments"].items():
        broken = qty
        if spec["case"] == "numani" and number.endswith("_2"):
            broken = 3000
        if spec["case"] == "special-lalap" and number.endswith("_3"):
            broken = 6000
        rows.append(_shipment(sid, number, broken))
        files.append(_file(sid, number, broken))
    for sid, successor in spec["supersede"].items():
        qty = 1000 if spec["case"] == "numani" else 5000
        rows.append(_shipment(sid, "EXTRA", qty))
        files.append(_file(sid, "EXTRA", qty))
    loading = []
    for sid, (number, qty) in spec["shipments"].items():
        loading.append({"id": f"load-{sid}", "surat_jalan_id": sid, "lines": [{"line_idx": 0, "qty": qty}]})
    deliveries = []
    for confirmation_id, (sid, number, qty, proof_ids) in spec["deliveries"].items():
        deliveries.append({
            "id": confirmation_id,
            "shipment_id": next(iter(spec["shipments"])),
            "qty": 6000 if qty > 6000 else qty,
            "items": [{"line_idx": 0, "qty": 6000 if qty > 6000 else qty}],
            "proof_ids": ["legacy-proof"],
            "confirmed_at": f"t-{confirmation_id}",
        })
    return {
        "id": spec["po_id"],
        "po_number": spec["po_number"],
        "customer": spec["case"],
        "line_items": [{"line_id": "L0", "name": "item", "quantity": spec["expected_delivered"]}],
        "total_quantity": spec["expected_delivered"],
        "shipments": rows,
        "surat_jalan_files": files,
        "loading_confirmations": loading,
        "loading_snapshots": [],
        "loading_discrepancies": [{"id": "disc", "status": "open"}],
        "delivery_confirmations": deliveries,
        "delivery_proofs": [{"id": "78f5493c-7183-4d9b-9461-8d2f6761573c", "shipment_id": "3ab43353-5705-476b-97fd-f3beb1b1314c"}],
        "qty_delivered": sum(min(row[2], 6000) for row in spec["deliveries"].values()),
        "delivery_status": "Sebagian Terkirim",
        "current_stage": 13,
        "updated_at": "before",
    }


def test_numani_verified_repair_restores_1000_4000_and_supersedes_sj3():
    order = _base(NUMANI)
    assert verify_preconditions(order, NUMANI) == []
    apply_verified_repair(order, NUMANI, now="after")
    active = [row for row in order["shipments"] if not row.get("cancelled")]
    assert [row["total_qty"] for row in active] == [1000, 4000]
    assert all(row.get("shipment_id") for row in order["loading_confirmations"])
    assert order["qty_delivered"] == 5000
    assert order["delivery_status"] == "Terkirim"
    assert order["current_stage"] == 15
    assert order["delivery_confirmations"][0]["proof_ids"] == ["legacy-proof"]
    assert build_report(_base(NUMANI), NUMANI)["after_if_applied"]["severity"] == "CONSISTENT"


def test_special_verified_repair_restores_3000_6000_11000_and_supersedes_sj4():
    order = _base(SPECIAL_LALAP)
    before_proofs = copy.deepcopy(order["delivery_proofs"])
    apply_verified_repair(order, SPECIAL_LALAP, now="after")
    active = [row for row in order["shipments"] if not row.get("cancelled")]
    assert [row["total_qty"] for row in active] == [3000, 6000, 11000]
    assert [row["qty"] for row in order["delivery_confirmations"]] == [3000, 6000, 11000]
    assert order["delivery_confirmations"][2]["proof_ids"] == ["78f5493c-7183-4d9b-9461-8d2f6761573c"]
    assert order["delivery_proofs"] == before_proofs
    assert order["qty_delivered"] == 20000
    assert order["delivery_status"] == "Terkirim"
    assert order["current_stage"] == 15
    assert build_report(_base(SPECIAL_LALAP), SPECIAL_LALAP)["after_if_applied"]["severity"] == "CONSISTENT"


def test_dry_run_does_not_mutate_source_and_reports_cas():
    order = _base(SPECIAL_LALAP)
    before = copy.deepcopy(order)
    report = build_report(order, SPECIAL_LALAP)
    assert order == before
    assert report["dry_run"] is True
    assert report["wrote"] is False
    assert report["cas"]
    assert report["after_if_applied"]["delivered_qty"] == 20000


def test_precondition_fails_closed_on_unknown_shipment():
    order = _base(NUMANI)
    order["shipments"].append(_shipment("unknown", "SJ/UNKNOWN", 1))
    assert verify_preconditions(order, NUMANI)
