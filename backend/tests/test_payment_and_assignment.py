"""Tugas Saya assignment filter + payment proof cap. No live API."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.payment_files import MAX_PAYMENT_PROOFS, payment_proof_reject_reason
from services.shipment_status import delivery_date_value


def test_payment_sixth_file_rejected_existing_kept():
    existing = [{"id": str(i)} for i in range(5)]
    msg = payment_proof_reject_reason(existing)
    assert msg
    assert "5" in msg
    assert payment_proof_reject_reason(existing[:4]) is None
    assert MAX_PAYMENT_PROOFS == 5
    assert len(existing) == 5


def test_delivery_date_is_actual_not_deadline():
    o = {
        "deadline": "2026-08-01",
        "delivery_deadline_effective": "2026-08-02",
        "delivered_at": "2026-08-26T15:00:00+07:00",
    }
    assert delivery_date_value(o) == "2026-08-26"
    o2 = {"deadline": "2026-08-01", "delivered_at": None, "delivery_proofs": []}
    assert delivery_date_value(o2) is None
