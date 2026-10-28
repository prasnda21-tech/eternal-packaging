"""P1 hotfix: Konfirmasi Terkirim with orphan/stale SJ line_id.

Production shape (SJ/24/10/2026/000018):
  shipments[].lines[].line_id = '{po_id}_line_0' (summary stamp)
  orders.line_items[0] has no matching line_id
  line_idx=0 uniquely identifies the PO line

Stock planning already uses match_po_line_idx (falls through).
Confirmation identity validation must not fail closed on orphan stamps,
while still rejecting true line_id vs line_idx conflicts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_confirm_stale_line")
os.environ.setdefault("JWT_SECRET", "test-secret-key-confirm-stale-line-id-32")
os.environ.setdefault("CORS_ORIGINS", "*")

from quantity_rules import match_po_line_idx
from services.delivery_history import (
    append_confirmation,
    resolve_confirmation_line_identity,
    validate_shipment_line_identities_for_confirmation,
)
from services.physical_stock_engine import plan_confirmed_sj_stock

PO_ID = "7a11603f-3fdc-4642-beda-9e06d16fa2cc"
ORPHAN_LINE_ID = f"{PO_ID}_line_0"
CANONICAL_A = "ln-canonical-a"
CANONICAL_B = "ln-canonical-b"


def _sig_pair(shipment_id="sj-1"):
    return {
        "delivery_recipient_signature": {
            "id": "sig-r",
            "recipient_name": "Penerima",
            "signature_storage_path": "r.png",
            "recipient_signed_at": "t",
            "frozen": False,
            "shipment_id": shipment_id,
        },
        "delivery_driver_signature": {
            "id": "sig-d",
            "driver_name": "Driver",
            "driver_signature_storage_path": "d.png",
            "driver_signed_at": "t",
            "frozen": False,
            "shipment_id": shipment_id,
        },
    }


def _order_orphan_so_line(*, multi=False, with_canonical=False):
    """SO missing (or mismatched) line_id; SJ carries orphan {po_id}_line_0."""
    if multi:
        lines = [
            {
                "name": "Cup Frosted",
                "quantity": 1000,
                "master_item_id": "m-a",
                "accurate_item_id": "acc-a",
                "production_process": "print",
                **({"line_id": CANONICAL_A} if with_canonical else {}),
            },
            {
                "name": "Cup Frosted",
                "quantity": 500,
                "master_item_id": "m-b",
                "accurate_item_id": "acc-b",
                "production_process": "print",
                **({"line_id": CANONICAL_B} if with_canonical else {}),
            },
        ]
    else:
        lines = [
            {
                "name": "Cup Single",
                "quantity": 1000,
                "master_item_id": "m-a",
                "accurate_item_id": "acc-a",
                "production_process": "print",
                **({"line_id": CANONICAL_A} if with_canonical else {}),
            },
        ]
    ship = {
        "id": "sj-1",
        "number": "SJ/24/10/2026/000018",
        "lines": [
            {
                "line_idx": 0,
                "line_id": ORPHAN_LINE_ID,
                "qty": 400,
                "name": lines[0]["name"],
            },
        ],
    }
    order = {
        "id": PO_ID,
        "po_number": "PO/TEST",
        "line_items": lines,
        "shipments": [ship],
        "delivery_confirmations": [],
        "delivery_proofs": [
            {"id": "ph1", "shipment_id": "sj-1"},
            {"id": "ph2", "shipment_id": "sj-1"},
            {"id": "ph3", "shipment_id": "sj-1"},
        ],
        "qty_delivered": 0,
        **_sig_pair("sj-1"),
    }
    return order, ship


def test_exact_production_error_shape_now_resolves():
    """Orphan {po_id}_line_0 + line_idx=0 on SO without that line_id → OK."""
    order, ship = _order_orphan_so_line()
    # Pre-fix symptom: match_po_line_idx already succeeded via line_idx.
    idx, err = match_po_line_idx(order, ship["lines"][0])
    assert err is None and idx == 0

    assert validate_shipment_line_identities_for_confirmation(order, ship) is None
    resolved_idx, resolved_lid = resolve_confirmation_line_identity(
        order, line_idx=0, line_id=ORPHAN_LINE_ID,
    )
    assert resolved_idx == 0
    assert resolved_lid is None  # SO has no canonical id yet


def test_legacy_orphan_with_canonical_so_line_id_prefers_canonical():
    order, ship = _order_orphan_so_line(with_canonical=True)
    assert validate_shipment_line_identities_for_confirmation(order, ship) is None
    idx, lid = resolve_confirmation_line_identity(
        order, line_idx=0, line_id=ORPHAN_LINE_ID,
    )
    assert idx == 0
    assert lid == CANONICAL_A


def test_correct_canonical_line_id_still_accepted():
    order, ship = _order_orphan_so_line(with_canonical=True)
    ship["lines"][0]["line_id"] = CANONICAL_A
    assert validate_shipment_line_identities_for_confirmation(order, ship) is None
    idx, lid = resolve_confirmation_line_identity(
        order, line_idx=0, line_id=CANONICAL_A,
    )
    assert (idx, lid) == (0, CANONICAL_A)


def test_ambiguous_duplicate_line_id_on_so_rejected():
    order, ship = _order_orphan_so_line(multi=True, with_canonical=True)
    order["line_items"][0]["line_id"] = "shared-id"
    order["line_items"][1]["line_id"] = "shared-id"
    ship["lines"][0]["line_id"] = "shared-id"
    err = validate_shipment_line_identities_for_confirmation(order, ship)
    assert err is not None
    assert "ambigu" in err.lower()
    with pytest.raises(ValueError) as caught:
        resolve_confirmation_line_identity(order, line_idx=0, line_id="shared-id")
    assert "ambigu" in str(caught.value).lower()


def test_same_name_product_lines_remain_distinct_on_conflict():
    """Same display name, different canonical ids — disagreeing idx fails closed."""
    order, ship = _order_orphan_so_line(multi=True, with_canonical=True)
    ship["lines"] = [
        {"line_idx": 1, "line_id": CANONICAL_A, "qty": 100, "name": "Cup Frosted"},
    ]
    err = validate_shipment_line_identities_for_confirmation(order, ship)
    assert err is not None
    assert "menunjuk" in err.lower() or "berbeda" in err.lower()
    with pytest.raises(ValueError):
        append_confirmation(
            order, {"email": "op@x", "name": "Op"}, qty_increment=100, now="t",
            action_id="bad-conflict", shipment=ship,
        )
    assert order.get("delivery_confirmations") in (None, [])


def test_same_name_lines_confirm_via_explicit_line_idx_with_orphan_stamp():
    order, ship = _order_orphan_so_line(multi=True, with_canonical=True)
    # Orphan stamp + line_idx=1 uniquely selects second same-name line.
    ship["lines"] = [
        {
            "line_idx": 1,
            "line_id": f"{PO_ID}_line_1",
            "qty": 200,
            "name": "Cup Frosted",
        },
    ]
    assert validate_shipment_line_identities_for_confirmation(order, ship) is None
    idx, lid = resolve_confirmation_line_identity(
        order, line_idx=1, line_id=f"{PO_ID}_line_1",
    )
    assert idx == 1
    assert lid == CANONICAL_B


def test_multi_item_synthetic_line0_with_wrong_line_idx1_rejected():
    """Stale {po}_line_0 naming line 0 while line_idx=1 → fail closed (no name match)."""
    order, ship = _order_orphan_so_line(multi=True, with_canonical=True)
    # Same product name on both lines — must not resolve by name.
    assert order["line_items"][0]["name"] == order["line_items"][1]["name"]
    ship["lines"] = [
        {
            "line_idx": 1,
            "line_id": ORPHAN_LINE_ID,  # {po}_line_0 — names line 0
            "qty": 100,
            "name": "Cup Frosted",
        },
    ]
    err = validate_shipment_line_identities_for_confirmation(order, ship)
    assert err is not None
    assert "menunjuk" in err.lower()
    with pytest.raises(ValueError) as caught:
        resolve_confirmation_line_identity(
            order, line_idx=1, line_id=ORPHAN_LINE_ID,
        )
    assert "menunjuk" in str(caught.value).lower()
    with pytest.raises(ValueError):
        append_confirmation(
            order, {"email": "op@x"}, qty_increment=100, now="t",
            action_id="wrong-idx", shipment=ship,
        )
    assert order.get("delivery_confirmations") in (None, [])


def test_foreign_po_synthetic_line_id_rejected():
    order, ship = _order_orphan_so_line(with_canonical=True)
    foreign = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_line_0"
    ship["lines"][0]["line_id"] = foreign
    err = validate_shipment_line_identities_for_confirmation(order, ship)
    assert err is not None
    assert "tidak ditemukan" in err.lower()


def test_unrelated_orphan_string_rejected_even_on_single_item():
    order, ship = _order_orphan_so_line(with_canonical=True)
    ship["lines"][0]["line_id"] = "random-orphan-not-synthetic"
    err = validate_shipment_line_identities_for_confirmation(order, ship)
    assert err is not None
    assert "tidak ditemukan" in err.lower()


def test_multi_sj_partial_delivery_independent():
    order, ship1 = _order_orphan_so_line(with_canonical=True)
    ship2 = {
        "id": "sj-2",
        "number": "SJ/24/10/2026/000019",
        "lines": [
            {
                "line_idx": 0,
                "line_id": ORPHAN_LINE_ID,
                "qty": 300,
                "name": "Cup Single",
            },
        ],
    }
    order["shipments"].append(ship2)
    order["delivery_proofs"].extend([
        {"id": "ph4", "shipment_id": "sj-2"},
        {"id": "ph5", "shipment_id": "sj-2"},
        {"id": "ph6", "shipment_id": "sj-2"},
    ])
    order.update(_sig_pair("sj-1"))

    r1 = append_confirmation(
        order, {"email": "a", "name": "A"}, qty_increment=400, now="t1",
        action_id="act-sj1", shipment=ship1, shipment_id="sj-1",
    )
    assert r1 is not None
    assert r1["items"][0]["line_id"] == CANONICAL_A
    assert r1["proof_ids"] == ["ph1", "ph2", "ph3"]

    order.update(_sig_pair("sj-2"))
    r2 = append_confirmation(
        order, {"email": "a", "name": "A"}, qty_increment=300, now="t2",
        action_id="act-sj2", shipment=ship2, shipment_id="sj-2",
    )
    assert r2 is not None
    assert r2["shipment_id"] == "sj-2"
    assert r2["items"][0]["qty"] == 300
    assert len(order["delivery_confirmations"]) == 2
    # SJ-1 confirmation untouched
    assert order["delivery_confirmations"][0]["id"] == r1["id"]
    assert order["delivery_confirmations"][0]["proof_ids"] == ["ph1", "ph2", "ph3"]


def test_repeated_confirmation_same_action_idempotent():
    order, ship = _order_orphan_so_line(with_canonical=True)
    r1 = append_confirmation(
        order, {"email": "a"}, qty_increment=400, now="t1",
        action_id="same-act", shipment=ship,
    )
    r2 = append_confirmation(
        order, {"email": "a"}, qty_increment=400, now="t2",
        action_id="same-act", shipment=ship,
    )
    assert r1 is r2
    assert len(order["delivery_confirmations"]) == 1
    assert order["delivery_confirmations"][0]["items"][0]["line_id"] == CANONICAL_A


def test_completed_sj_identity_unaffected():
    """Already-delivered confirmation snapshot keeps its stored line_id."""
    order, ship = _order_orphan_so_line(with_canonical=True)
    ship["delivered_at"] = "done"
    prior = {
        "id": "conf-done",
        "action_id": "done-act",
        "shipment_id": "sj-1",
        "qty": 400,
        "items": [{"line_idx": 0, "line_id": CANONICAL_A, "qty": 400, "name": "Cup Single"}],
        "proof_ids": ["ph1", "ph2", "ph3"],
    }
    order["delivery_confirmations"] = [prior]
    # Re-validate identities still OK; historical row unchanged.
    assert validate_shipment_line_identities_for_confirmation(order, ship) is None
    assert order["delivery_confirmations"][0]["items"][0]["line_id"] == CANONICAL_A
    assert order["delivery_confirmations"][0]["proof_ids"] == ["ph1", "ph2", "ph3"]


def test_ttd_and_photo_proofs_preserved_on_orphan_confirm():
    order, ship = _order_orphan_so_line(with_canonical=True)
    rec = append_confirmation(
        order, {"email": "drv@x", "name": "Driver", "role": "driver", "id": "u1"},
        qty_increment=400, now="t",
        action_id="pod-act", shipment=ship, shipment_id="sj-1",
    )
    assert rec is not None
    assert rec["proof_ids"] == ["ph1", "ph2", "ph3"]
    assert rec["photo_count"] == 3
    assert rec["has_recipient_signature"] is True
    assert rec["has_driver_signature"] is True
    # Live signature blobs on the order are not cleared by append.
    assert order["delivery_recipient_signature"]["signature_storage_path"] == "r.png"
    assert order["delivery_driver_signature"]["driver_signature_storage_path"] == "d.png"


def test_invalid_line_idx_still_rejected():
    order, _ship = _order_orphan_so_line()
    with pytest.raises(ValueError) as caught:
        resolve_confirmation_line_identity(
            order, line_idx=9, line_id=ORPHAN_LINE_ID,
        )
    assert "tidak valid" in str(caught.value).lower()


def test_stock_plan_uses_canonical_identity_with_orphan_sj_stamp():
    order, ship = _order_orphan_so_line(with_canonical=True)
    plan = plan_confirmed_sj_stock(order, ship, allow_delivered=False)
    assert plan.get("error") is None
    assert plan.get("skip") is False
    assert len(plan["lines"]) == 1
    assert plan["lines"][0]["line_idx"] == 0
    assert plan["lines"][0]["master_item_id"] == "m-a"
    assert plan["lines"][0]["line_id"] == CANONICAL_A
    assert plan["lines"][0]["qty"] == 400


def test_so_and_sj_records_not_mutated_by_resolve():
    order, ship = _order_orphan_so_line(with_canonical=True)
    before_so = dict(order["line_items"][0])
    before_sj = dict(ship["lines"][0])
    resolve_confirmation_line_identity(
        order, line_idx=0, line_id=ORPHAN_LINE_ID,
    )
    assert order["line_items"][0] == before_so
    assert ship["lines"][0] == before_sj
    assert ship["lines"][0]["line_id"] == ORPHAN_LINE_ID
