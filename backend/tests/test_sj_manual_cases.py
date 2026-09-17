"""Evidence-verified repair plans for three legacy production cases."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.reconcile_manual_sj_cases import (
    CASES,
    apply_manual_repair,
    summarize,
    verify_preconditions,
)


def _base_order(case: str) -> dict:
    spec = CASES[case]
    qty = spec["current_qty"] if spec.get("current_qty") is not None else spec["expected_qty"]
    shipment = {
        "id": spec["shipment_id"],
        "number": spec["sj_number"],
        "status": "pending",
        "total_qty": qty,
        "lines": [{"line_idx": 0, "qty": qty, "name": "Cup"}],
    }
    return {
        "id": spec["po_id"],
        "po_number": spec["po_number"],
        "line_items": [{"name": "Cup", "quantity": spec["expected_qty"]}],
        "shipments": [shipment],
        "surat_jalan_files": [{
            "id": f"file-{spec['shipment_id']}",
            "shipment_id": spec["shipment_id"],
            "sj_number": spec["sj_number"],
            "total_qty": qty,
            "lines": [{"line_idx": 0, "qty": qty, "name": "Cup"}],
        }],
        "loading_confirmations": [{
            "id": spec["loading_id"],
            "surat_jalan_id": spec["shipment_id"],
            "qty_sj_snapshot": spec["expected_qty"],
            "lines": [{"line_idx": 0, "qty": spec["expected_qty"]}],
        }],
        "delivery_confirmations": [],
    }


@pytest.mark.parametrize("case", ["soebakrie", "seven-corner"])
def test_exact_loading_identity_is_stamped_without_qty_change(case):
    order = _base_order(case)
    before_qty = order["shipments"][0]["total_qty"]

    ops = apply_manual_repair(order, CASES[case], now="2026-09-17T00:00:00+00:00")

    assert ops == [{
        "op": "stamp_shipment_id",
        "record_id": CASES[case]["loading_id"],
        "shipment_id": CASES[case]["shipment_id"],
    }]
    assert order["loading_confirmations"][0]["shipment_id"] == CASES[case]["shipment_id"]
    assert order["shipments"][0]["total_qty"] == before_qty


def test_locan_restores_sj2_from_exact_loading_snapshot_only():
    order = _base_order("locan")
    order["qty_delivered"] = 200
    order["delivery_confirmations"] = [{
        "id": "delivery-sj1",
        "shipment_id": "9331369a-sj1",
        "qty": 200,
    }]
    before_delivery = copy.deepcopy(order["delivery_confirmations"])

    ops = apply_manual_repair(order, CASES["locan"], now="2026-09-17T00:00:00+00:00")

    assert [op["op"] for op in ops] == ["stamp_shipment_id", "restore_qty_from_loading_snapshot"]
    assert order["shipments"][0]["total_qty"] == 800
    assert order["shipments"][0]["lines"][0]["qty"] == 800
    assert order["surat_jalan_files"][0]["total_qty"] == 800
    assert order["delivery_confirmations"] == before_delivery
    assert order["qty_delivered"] == 200


def test_seven_corner_historical_evidence_is_never_rewritten():
    order = _base_order("seven-corner")
    order["delivery_confirmations"] = [{
        "id": "delivery-sj2",
        "shipment_id": CASES["seven-corner"]["shipment_id"],
        "qty": 16000,
        "sj_file_id": "wrong-sj-file",
        "recipient_signature_id": "recipient-sj1",
    }]
    order["delivery_recipient_signatures"] = [{
        "id": "recipient-sj1",
        "shipment_id": "sibling-sj-1",
    }]
    before = copy.deepcopy(order["delivery_confirmations"])

    apply_manual_repair(order, CASES["seven-corner"], now="2026-09-17T00:00:00+00:00")

    assert order["delivery_confirmations"] == before
    review = summarize(order, CASES["seven-corner"])["historical_proof_review"]
    assert review == [{
        "confirmation_id": "delivery-sj2",
        "shipment_id": CASES["seven-corner"]["shipment_id"],
        "reason_code": "HISTORICAL_PROOF_REVIEW",
        "mismatched_fields": ["sj_file_id", "recipient_signature_id"],
        "recommendation": "Jangan mengarang bukti; pertahankan histori dan review dokumen asli.",
    }]


def test_wrong_loading_identity_fails_closed():
    order = _base_order("soebakrie")
    order["loading_confirmations"][0]["surat_jalan_id"] = "another-shipment"

    errors = verify_preconditions(order, CASES["soebakrie"])

    assert "Loading surat_jalan_id mismatch" in errors
    with pytest.raises(ValueError, match="surat_jalan_id mismatch"):
        apply_manual_repair(order, CASES["soebakrie"], now="2026-09-17T00:00:00+00:00")
