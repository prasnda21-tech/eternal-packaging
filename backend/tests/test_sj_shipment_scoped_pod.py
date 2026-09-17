"""Regression tests: POD evidence must never leak across sibling SJs."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.delivery_history import append_confirmation
from services.delivery_pod_signature import (
    resolve_requested_signature_identity,
    signature_confirm_block_reason,
)


def _signature(sig_id: str, shipment_id: str, *, recipient=False) -> dict:
    row = {
        "id": sig_id,
        "signature_file_id": sig_id,
        "signature_storage_path": f"signatures/{sig_id}.png",
        "shipment_id": shipment_id,
    }
    if recipient:
        row["recipient_name"] = "Penerima Sah"
    return row


def _order() -> dict:
    return {
        "id": "po-multi",
        "line_items": [{"name": "Cup", "quantity": 3000}],
        "shipments": [
            {"id": "sj-1", "number": "SJ/1", "lines": [{"line_idx": 0, "qty": 1000}]},
            {"id": "sj-2", "number": "SJ/2", "lines": [{"line_idx": 0, "qty": 2000}]},
        ],
        "surat_jalan_files": [
            {"id": "file-1", "shipment_id": "sj-1", "sj_number": "SJ/1"},
            {"id": "file-2", "shipment_id": "sj-2", "sj_number": "SJ/2"},
        ],
        "delivery_proofs": [
            {"id": "sj1-p1", "shipment_id": "sj-1", "storage_path": "proofs/1.jpg"},
            {"id": "sj1-p2", "shipment_id": "sj-1", "storage_path": "proofs/2.jpg"},
            {"id": "sj2-p1", "shipment_id": "sj-2", "storage_path": "proofs/3.jpg"},
            {"id": "sj2-p2", "shipment_id": "sj-2", "storage_path": "proofs/4.jpg"},
        ],
        "delivery_recipient_signature": _signature("rec-1", "sj-1", recipient=True),
        "delivery_driver_signature": _signature("drv-1", "sj-1"),
        "delivery_confirmations": [],
    }


def test_sibling_signatures_do_not_unlock_selected_shipment():
    order = _order()
    sj2 = order["shipments"][1]

    reason = signature_confirm_block_reason(order, sj2)

    assert reason == "Tanda tangan penerima wajib sebelum Konfirmasi Terkirim."


def test_selected_shipment_signatures_unlock_selected_shipment():
    order = _order()
    sj2 = order["shipments"][1]
    order["delivery_recipient_signature"] = _signature("rec-2", "sj-2", recipient=True)
    order["delivery_driver_signature"] = _signature("drv-2", "sj-2")

    assert signature_confirm_block_reason(order, sj2) is None


def test_delivery_history_uses_only_selected_shipment_evidence():
    order = _order()
    sj2 = order["shipments"][1]

    rec = append_confirmation(
        order,
        {"id": "user-1", "email": "driver@example.com", "name": "Driver"},
        qty_increment=2000,
        now="2026-09-17T00:00:00+00:00",
        shipment_id="sj-2",
        shipment=sj2,
    )

    assert rec["proof_ids"] == ["sj2-p1", "sj2-p2"]
    assert rec["has_recipient_signature"] is False
    assert rec["has_driver_signature"] is False
    assert rec["recipient_name"] is None


def test_signature_upload_identity_rejects_sibling_sj_file():
    order = _order()

    sid, file_id, reason = resolve_requested_signature_identity(
        order,
        shipment_id="sj-2",
        sj_file_id="file-1",
    )

    assert sid is None
    assert file_id is None
    assert reason == "File Surat Jalan tidak sesuai dengan SJ yang dipilih."


def test_signature_upload_identity_accepts_exact_active_sj_file():
    order = _order()

    sid, file_id, reason = resolve_requested_signature_identity(
        order,
        shipment_id="sj-2",
        sj_file_id="file-2",
    )

    assert (sid, file_id, reason) == ("sj-2", "file-2", None)
