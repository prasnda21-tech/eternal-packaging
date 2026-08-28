"""Accurate Sales Order field mapping from official detail.do / save.do keys.

Does not call live Accurate. Fixture mirrors SO PO/2026/08/28/000004 (CAKE IN LOVE).
"""
from datetime import datetime, timezone

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.accurate import (
    _normalize_accurate_so,
    so_preview_dates,
    summarize_accurate_payload_shape,
)


CAKE_IN_LOVE_ADDRESS = (
    "59G2+JPW, Dongkelan Kauman Rt07, Dongkelan Kauman, Tirtonirmolo, "
    "Kec. Kasihan, Kabupaten Bantul, Daerah Istimewa Yogyakarta 55183"
)

# Shape taken from Accurate OpenAPI /api/sales-order/save.do plus GET detail
# expansions (nested *.name, userName, salesmanList, dataClassification1Name).
CAKE_IN_LOVE_SO_DETAIL = {
    "id": 4004,
    "number": "PO/2026/08/28/000004",
    "transDate": "28/08/2026",
    "shipDate": "05/09/2026",
    "toAddress": CAKE_IN_LOVE_ADDRESS,
    "customer": {"id": 88, "name": "CAKE IN LOVE", "customerNo": "CIL-001"},
    "paymentTerm": {"id": 3, "name": "TRANSFER BANK"},
    "shipment": {"id": 7, "name": "OUTLET"},
    "userName": "nabila salsabila",
    "detailItem": [
        {
            "id": 91,
            "quantity": 10000,
            "itemUnitName": "PCS",
            "detailNotes": "SABLON WARNA HITAM",
            "item": {"id": 418, "no": "100418", "name": "Plastik PE 15"},
            "salesmanList": [{"id": 12, "name": "Dimas Aji Prasetya", "number": "S-12"}],
            "dataClassification1Name": "JS",
        }
    ],
}


def test_cake_in_love_mapping_from_accurate_detail_shape():
    so = _normalize_accurate_so(CAKE_IN_LOVE_SO_DETAIL)
    line = so["line_items"][0]
    assert so["customer"] == "CAKE IN LOVE"
    assert so["address"] == CAKE_IN_LOVE_ADDRESS
    assert so["created_by_user"] == "nabila salsabila"
    assert so["sales"] == "Dimas Aji Prasetya"
    assert so["created_by_user"] != so["sales"]
    assert so["expedition"] == "OUTLET"
    assert so["delivery_zone"] == "JS"
    assert so["payment_terms"] == "TRANSFER BANK"
    assert so["estimated_ship_date"] == "2026-09-05"
    assert so["order_date"] == "2026-08-28"
    assert line["name"] == "Plastik PE 15"
    assert line["quantity"] == 10000
    assert line["color"] == "HITAM"
    order_iso, est_iso = so_preview_dates(so, now=datetime(2026, 8, 28, tzinfo=timezone.utc))
    assert order_iso == "2026-08-28"
    assert est_iso == "2026-09-05"


def test_salesman_from_line_list_when_header_missing():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "quantity": 1,
                "item": {"id": 1, "no": "X", "name": "Item"},
                "salesmanList": [{"name": "Dimas Aji Prasetya"}],
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["sales"] == "Dimas Aji Prasetya"
    assert so["created_by_user"] is None


def test_created_by_from_nested_user_not_salesman():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "user": {"id": 9, "name": "nabila salsabila"},
        "salesman": {"id": 12, "name": "Dimas Aji Prasetya"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["created_by_user"] == "nabila salsabila"
    assert so["sales"] == "Dimas Aji Prasetya"


def test_address_toAddress_preferred_over_customer_bill():
    raw = {
        "id": 1,
        "number": "SO-1",
        "toAddress": CAKE_IN_LOVE_ADDRESS,
        "customer": {
            "name": "CAKE IN LOVE",
            "billStreet": "Should not win",
            "billCity": "Jakarta",
        },
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["address"] == CAKE_IN_LOVE_ADDRESS


def test_expedition_from_shipmentName_not_branch():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "shipmentName": "OUTLET",
        "branchName": "Yogyakarta",
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["expedition"] == "OUTLET"
    assert so["outlet"] == "Yogyakarta"


def test_shipDate_used_instead_of_plus_three_default():
    so = _normalize_accurate_so(CAKE_IN_LOVE_SO_DETAIL)
    now = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
    _order, est = so_preview_dates(so, now=now)
    assert est == "2026-09-05"
    assert est != (now.replace(day=31)).date().isoformat()


def test_missing_shipDate_keeps_plus_three_fallback():
    raw = {
        "id": 1,
        "number": "SO-1",
        "transDate": "28/08/2026",
        "customer": {"name": "A"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["estimated_ship_date"] is None
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    _order, est = so_preview_dates(so, now=now)
    assert est.startswith("2026-08-31")


def test_fixture_key_shape_has_no_secrets():
    shape = "\n".join(summarize_accurate_payload_shape(CAKE_IN_LOVE_SO_DETAIL))
    assert "toAddress: str" in shape
    assert "shipDate: str" in shape
    assert "userName: str" in shape
    assert "shipment: object" in shape
    assert "salesmanList: list[1]" in shape
    assert "Authorization" not in shape
    assert "access_token" not in shape
    assert "<redacted>" not in shape


def test_zone_uses_dataClassification1_only():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "dataClassification2Name": "NOT-A-ZONE",
        "detailItem": [
            {
                "quantity": 1,
                "item": {"id": 1, "name": "Item"},
                "dataClassification2Name": "ALSO-NOT",
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["delivery_zone"] is None


def test_payment_term_name_string_and_nested_object():
    a = _normalize_accurate_so({
        "id": 1, "number": "A", "customer": {"name": "C"},
        "paymentTermName": "TRANSFER BANK",
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "X"}}],
    })
    b = _normalize_accurate_so({
        "id": 1, "number": "B", "customer": {"name": "C"},
        "paymentTerm": {"name": "TRANSFER BANK"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "X"}}],
    })
    assert a["payment_terms"] == "TRANSFER BANK"
    assert b["payment_terms"] == "TRANSFER BANK"
