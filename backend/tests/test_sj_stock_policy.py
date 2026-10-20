"""Regression tests for opt-in SJ identity, with legacy behavior unchanged."""
import pytest

from services.sj_stock_policy import (
    POLICY_V2, LEGACY_POLICY, canonical_sj_id,
    is_sj_stock_v2, policy_version, sj_stock_reference, resolve_v2_sj_stock_identity,
)


def test_missing_marker_keeps_old_po_legacy():
    assert policy_version({"id": "po-old"}) == LEGACY_POLICY
    assert sj_stock_reference({"id": "po-old"}, {"id": "sj-1"}) == "po-old"
    assert not is_sj_stock_v2({"id": "po-old"})


def test_no_date_based_upgrade_of_historical_po():
    order = {"id": "po-old", "created_at": "2099-01-01", "stock_business_rule_version": None}
    assert policy_version(order) == LEGACY_POLICY


def test_explicit_v2_requires_sj_identity():
    order = {"id": "po-new", "stock_business_rule_version": POLICY_V2}
    assert is_sj_stock_v2(order)
    assert sj_stock_reference(order, {"id": "sj-1"}) == "sj-1"
    with pytest.raises(ValueError, match="SJ_STOCK_ID_REQUIRED"):
        sj_stock_reference(order, None)


def test_multi_sj_isolated_identity():
    order = {"id": "po-new", "stock_business_rule_version": 2}
    assert sj_stock_reference(order, {"id": "sj-1"}) != sj_stock_reference(order, {"id": "sj-2"})


def test_canonical_business_id_precedes_shipment_row_id():
    assert canonical_sj_id({"id": "sh-1", "sales_delivery_id": "business-sj-1"}) == "business-sj-1"


def test_string_version_cannot_enable_v2():
    assert policy_version({"stock_business_rule_version": "2"}) == LEGACY_POLICY


def test_v2_requires_unique_existing_sj_on_order():
    order = {
        "id": "po-new", "stock_business_rule_version": 2,
        "shipments": [
            {"id": "shipment-a", "sales_delivery_id": "sj-a"},
            {"id": "shipment-b", "sales_delivery_id": "sj-b"},
        ],
    }
    assert resolve_v2_sj_stock_identity(order, "shipment-a") == "sj-a"
    assert resolve_v2_sj_stock_identity(order, "sj-b") == "sj-b"
    with pytest.raises(ValueError, match="AMBIGUOUS_OR_MISSING"):
        resolve_v2_sj_stock_identity(order, "sj-unrelated")


def test_v2_duplicate_sj_identity_fails_closed():
    order = {
        "stock_business_rule_version": 2,
        "shipments": [
            {"id": "row-a", "sales_delivery_id": "sj-duplicate"},
            {"id": "row-b", "sales_delivery_id": "sj-duplicate"},
        ],
    }
    with pytest.raises(ValueError, match="AMBIGUOUS_OR_MISSING"):
        resolve_v2_sj_stock_identity(order, "sj-duplicate")
