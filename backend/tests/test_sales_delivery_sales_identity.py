"""Stage C.2 — multi-PO Sales identity contract (pure validation).

Multi-PO create path itself lives on origin/main (PR #710). These tests lock
the portable guard that must be wired into validate_orders_merge_compatible /
_create_multi_source_sales_delivery at integration time.
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_sj_sales_id")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from services.sales_delivery_sales_identity import (
    po_sales_snapshot,
    resolve_sj_sales_from_orders,
    validate_orders_sales_identity_compatible,
)


def _po(**kwargs):
    base = {
        "id": kwargs.pop("id", "po-1"),
        "po_number": kwargs.pop("po_number", "PO-1"),
        "sales": kwargs.pop("sales", ""),
        "sales_master_id": kwargs.pop("sales_master_id", None),
    }
    base.update(kwargs)
    if base["sales_master_id"] is None:
        base.pop("sales_master_id")
    return base


def test_single_po_validation_is_noop():
    validate_orders_sales_identity_compatible([_po(sales="Vira")])
    validate_orders_sales_identity_compatible([])
    label, mid = resolve_sj_sales_from_orders([_po(sales="Vira", sales_master_id="sm-1")])
    assert label == "Vira"
    assert mid == "sm-1"


def test_same_master_id_compatible():
    a = _po(id="a", po_number="PO-A", sales="Vira", sales_master_id="sm-vira")
    b = _po(id="b", po_number="PO-B", sales="Vira Offline", sales_master_id="sm-vira")
    validate_orders_sales_identity_compatible([a, b])
    label, mid = resolve_sj_sales_from_orders([a, b])
    assert mid == "sm-vira"
    # Prefer a nonempty snapshot among matching ids
    assert label in ("Vira", "Vira Offline")


def test_same_master_id_uses_proven_official_label():
    a = _po(id="a", sales="Old Snapshot", sales_master_id="sm-vira")
    b = _po(id="b", sales="Also Old", sales_master_id="sm-vira")
    label, mid = resolve_sj_sales_from_orders(
        [a, b], proven_master_labels={"sm-vira": "Vira"}
    )
    assert mid == "sm-vira"
    assert label == "Vira"


def test_different_master_ids_rejected():
    a = _po(id="a", po_number="PO-A", sales="Vira", sales_master_id="sm-vira")
    b = _po(id="b", po_number="PO-B", sales="Alfi", sales_master_id="sm-alfi")
    with pytest.raises(HTTPException) as exc:
        validate_orders_sales_identity_compatible([a, b])
    assert exc.value.status_code == 400
    assert "sales_master_id" in str(exc.value.detail)
    assert "PO pertama" in str(exc.value.detail)


def test_legacy_same_labels_ok():
    a = _po(id="a", sales="Legacy Vira")
    b = _po(id="b", sales="Legacy Vira")
    validate_orders_sales_identity_compatible([a, b])
    label, mid = resolve_sj_sales_from_orders([a, b])
    assert label == "Legacy Vira"
    assert mid is None


def test_legacy_different_labels_rejected():
    a = _po(id="a", po_number="PO-A", sales="Vira")
    b = _po(id="b", po_number="PO-B", sales="Alfi")
    with pytest.raises(HTTPException) as exc:
        validate_orders_sales_identity_compatible([a, b])
    assert exc.value.status_code == 400
    assert "sama persis" in str(exc.value.detail)


def test_legacy_blank_identity_rejected():
    a = _po(id="a", po_number="PO-A", sales="Vira")
    b = _po(id="b", po_number="PO-B", sales="")
    with pytest.raises(HTTPException) as exc:
        validate_orders_sales_identity_compatible([a, b])
    assert "kosong" in str(exc.value.detail)


def test_mixed_identity_rejected_without_proof():
    a = _po(id="a", sales="Vira", sales_master_id="sm-vira")
    b = _po(id="b", sales="Vira")  # legacy, same label but no authoritative proof
    with pytest.raises(HTTPException) as exc:
        validate_orders_sales_identity_compatible([a, b])
    assert "campuran" in str(exc.value.detail).lower() or "relasi otoritatif" in str(
        exc.value.detail
    )


def test_mixed_identity_accepted_with_exact_proven_label():
    a = _po(id="a", sales="Whatever", sales_master_id="sm-vira")
    b = _po(id="b", sales="Vira")
    validate_orders_sales_identity_compatible(
        [a, b], proven_master_labels={"sm-vira": "Vira"}
    )
    label, mid = resolve_sj_sales_from_orders(
        [a, b], proven_master_labels={"sm-vira": "Vira"}
    )
    assert mid == "sm-vira"
    assert label == "Vira"


def test_mixed_identity_rejected_on_label_mismatch_even_with_proof():
    a = _po(id="a", sales="Vira", sales_master_id="sm-vira")
    b = _po(id="b", po_number="PO-B", sales="Viraa")  # not exact
    with pytest.raises(HTTPException) as exc:
        validate_orders_sales_identity_compatible(
            [a, b], proven_master_labels={"sm-vira": "Vira"}
        )
    assert "fuzzy" in str(exc.value.detail).lower() or "sama persis" in str(
        exc.value.detail
    )


def test_no_fuzzy_whitespace_or_case_equivalence_for_legacy():
    # Exact match only — trailing space / case differences reject
    a = _po(id="a", sales="Vira")
    b = _po(id="b", sales="vira")
    with pytest.raises(HTTPException):
        validate_orders_sales_identity_compatible([a, b])


def test_customer_compatibility_is_orthogonal():
    """Sales guard does not encode customer rules; caller keeps merge architecture."""
    # Two POs same sales, different customers — Sales guard still OK.
    a = _po(id="a", sales="Vira", sales_master_id="sm-vira", customer_id="c1")
    b = _po(id="b", sales="Vira", sales_master_id="sm-vira", customer_id="c2")
    validate_orders_sales_identity_compatible([a, b])


def test_po_sales_snapshot_field_priority():
    assert po_sales_snapshot({"sales": "A", "seller": "B"}) == "A"
    assert po_sales_snapshot({"seller": "B"}) == "B"
    assert po_sales_snapshot(None) == ""
