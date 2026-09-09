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

# Live GET sales-order/detail.do for PO/2026/08/28/000004.
CAKE_IN_LOVE_SO_DETAIL = {
    "id": 4004,
    "number": "PO/2026/08/28/000004",
    "transDate": "28/08/2026",
    "shipDate": "05/09/2026",
    "toAddress": CAKE_IN_LOVE_ADDRESS,
    "description": "SABLON WARNA HITAM",
    "charField3": "JS",
    "searchCharField3": {"id": "JS", "name": "JS"},
    "createdBy": 1248273,
    "printUserName": "Dimas Aji Prasetya",
    "userPrinted": 488125,
    "customer": {
        "id": 88,
        "name": "CAKE IN LOVE",
        "customerNo": "CIL-001",
        "shipAddressId": 1,
        "shipAddress": {"id": 1, "picName": "CAKE IN LOVE"},
        "shipAddressList": [{"id": 1, "picName": "CAKE IN LOVE"}],
    },
    "paymentTerm": {"id": 3, "name": "TRANSFER BANK"},
    "shipment": {"id": "150", "name": "OUTLET"},
    "branchId": "50",
    "detailItem": [
        {
            "id": 91,
            "quantity": 10000,
            "itemUnitName": "PCS",
            "detailNotes": "SABLON WARNA HITAM",
            "branchId": "50",
            "item": {"id": 418, "no": "100418", "name": "Plastik PE 15"},
            "salesmanList": [{"id": 12, "name": "Dimas Aji Prasetya", "number": "S-12"}],
        }
    ],
}


def test_cake_in_love_mapping_from_accurate_detail_shape():
    so = _normalize_accurate_so(CAKE_IN_LOVE_SO_DETAIL)
    line = so["line_items"][0]
    assert so["customer"] == "CAKE IN LOVE"
    assert so["recipient"] == "CAKE IN LOVE"
    assert so["address"] == CAKE_IN_LOVE_ADDRESS
    assert so["created_by_user"] is None
    assert so["sales"] == "Dimas Aji Prasetya"
    assert so["created_by_user"] != so["sales"]
    assert so["expedition"] == "OUTLET"
    assert so["delivery_zone"] == "JS"
    assert so["payment_terms"] == "TRANSFER BANK"
    assert so["estimated_ship_date"] == "2026-09-05"
    assert so["order_date"] == "2026-08-28"
    assert line["name"] == "Plastik PE 15"
    assert line["quantity"] == 10000
    assert line["color"] is None
    assert so["notes"] == "SABLON WARNA HITAM"
    assert line["notes"] == "SABLON WARNA HITAM"
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
    assert "printUserName: str" in shape
    assert "picName: str" in shape
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


def test_zone_from_searchCharField3_name():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "searchCharField3": {"id": "JS", "name": "JS"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["delivery_zone"] == "JS"


def test_zone_from_charField3_when_search_missing():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "charField3": "JT",
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["delivery_zone"] == "JT"


def test_zone_searchCharField3_beats_charField3_and_classification():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "searchCharField3": {"name": "JS"},
        "charField3": "JT",
        "dataClassification1Name": "OTHER",
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["delivery_zone"] == "JS"


def test_color_not_from_header_description():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "description": "SABLON WARNA PUTIH",
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] is None


def test_header_description_does_not_overwrite_line_color():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "description": "SABLON WARNA PUTIH",
        "detailItem": [
            {
                "quantity": 1,
                "item": {"id": 1, "name": "Item A"},
                "detailNotes": "SABLON WARNA HITAM",
            },
            {
                "quantity": 1,
                "item": {"id": 2, "name": "Item B"},
                "color": "MERAH",
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][1]["color"] == "MERAH"


def _two_line_cup_lid_so(*, reverse: bool = False, header: str = "SABLON WARNA ORANGE"):
    cup = {
        "id": 501,
        "quantity": 500,
        "itemUnitName": "pcs",
        "detailNotes": "SABLON WARNA ORANGE",
        "item": {"id": 801, "no": "CUP-14", "name": "14 oz Inject Datar Bening Klir"},
    }
    lid = {
        "id": 502,
        "quantity": 500,
        "itemUnitName": "pcs",
        "item": {"id": 802, "no": "LID-CON", "name": "LID Connect Inject Bening Klir"},
    }
    detail = [lid, cup] if reverse else [cup, lid]
    return {
        "id": 9001,
        "number": "SO-CUP-LID",
        "customer": {"name": "Fixture Customer"},
        "description": header,
        "detailItem": detail,
    }


def test_multiline_so_header_color_does_not_inherit_to_lid():
    so = _normalize_accurate_so(_two_line_cup_lid_so())
    lines = so["line_items"]
    assert len(lines) == 2
    assert lines[0]["name"] == "14 oz Inject Datar Bening Klir"
    assert lines[0]["color"] is None
    assert lines[0]["quantity"] == 500
    assert lines[0]["accurate_line_id"] == "501"
    assert lines[1]["name"] == "LID Connect Inject Bening Klir"
    assert lines[1]["color"] is None
    assert lines[1]["quantity"] == 500
    assert lines[1]["accurate_line_id"] == "502"
    assert lines[1].get("color_source") is None
    assert "production_process" not in lines[0]
    assert "production_process" not in lines[1]
    assert so["notes"] == "SABLON WARNA ORANGE"
    assert lines[0]["notes"] == "SABLON WARNA ORANGE"
    assert not lines[1]["notes"]


def test_multiline_color_mapping_is_order_independent():
    so = _normalize_accurate_so(_two_line_cup_lid_so(reverse=True))
    by_name = {li["name"]: li for li in so["line_items"]}
    assert so["line_items"][0]["name"] == "LID Connect Inject Bening Klir"
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][1]["name"] == "14 oz Inject Datar Bening Klir"
    assert so["line_items"][1]["color"] is None
    assert by_name["LID Connect Inject Bening Klir"]["color"] is None
    assert by_name["14 oz Inject Datar Bening Klir"]["color"] is None


def test_same_name_lines_keep_distinct_colors_and_ids():
    raw = {
        "id": 2,
        "number": "SO-SAME-NAME",
        "customer": {"name": "A"},
        "description": "SABLON WARNA ORANGE",
        "detailItem": [
            {
                "id": 11,
                "quantity": 100,
                "detailNotes": "SABLON WARNA ORANGE",
                "item": {"id": 1, "name": "Cup 14 oz"},
            },
            {
                "id": 12,
                "quantity": 200,
                "detailNotes": "SABLON WARNA BENING",
                "item": {"id": 1, "name": "Cup 14 oz"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert len(so["line_items"]) == 2
    assert so["line_items"][0]["name"] == so["line_items"][1]["name"] == "Cup 14 oz"
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][1]["color"] is None
    assert so["line_items"][0]["accurate_line_id"] == "11"
    assert so["line_items"][1]["accurate_line_id"] == "12"
    assert so["line_items"][0]["quantity"] == 100
    assert so["line_items"][1]["quantity"] == 200


def test_multiline_header_only_does_not_stamp_every_line():
    raw = {
        "id": 3,
        "number": "SO-HEADER-ONLY",
        "customer": {"name": "A"},
        "description": "SABLON WARNA ORANGE",
        "detailItem": [
            {"quantity": 500, "item": {"id": 1, "name": "Paper cup 12oz"}},
            {"quantity": 500, "item": {"id": 2, "name": "Paper bag medium"}},
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][1]["color"] is None
    assert so["line_items"][0].get("color_source") is None


def test_outlet_not_from_shipment_or_branchId():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "branchId": "50",
        "shipment": {"id": "150", "name": "OUTLET"},
        "detailItem": [
            {"quantity": 1, "item": {"id": 1, "name": "Item"}, "branchId": "50"}
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["expedition"] == "OUTLET"
    assert so["outlet"] in (None, "", "-")


def test_recipient_from_shipAddress_picName():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {
            "name": "OTHER CUSTOMER",
            "shipAddress": {"picName": "CAKE IN LOVE"},
        },
        "salesman": {"name": "Dimas Aji Prasetya"},
        "printUserName": "Dimas Aji Prasetya",
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["recipient"] == "CAKE IN LOVE"
    assert so["customer"] == "OTHER CUSTOMER"
    assert so["recipient"] != so["sales"]
    assert so["created_by_user"] is None


def test_recipient_from_shipAddressList_matching_id():
    raw = {
        "id": 1,
        "number": "SO-1",
        "shipAddressId": 7,
        "customer": {
            "name": "OTHER CUSTOMER",
            "shipAddressList": [
                {"id": 2, "picName": "WRONG"},
                {"id": 7, "picName": "CAKE IN LOVE"},
            ],
        },
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["recipient"] == "CAKE IN LOVE"


def test_recipient_fallback_shipTo_attention_when_picName_empty():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "OTHER CUSTOMER", "shipAddress": {"picName": ""}},
        "shipTo": {"attention": "ICANG"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["recipient"] == "ICANG"
    assert so["customer"] == "OTHER CUSTOMER"


def test_created_by_ignores_printUserName_and_createdBy_id():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "createdBy": 1248273,
        "printUserName": "Dimas Aji Prasetya",
        "userPrinted": 488125,
        "salesman": {"name": "Dimas Aji Prasetya"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["created_by_user"] is None
    assert so["sales"] == "Dimas Aji Prasetya"
    assert so["_created_by_id"] == "1248273"
    assert so["created_by_user"] != so["sales"]


def test_outlet_not_from_warehouse_or_selling_price_branch():
    raw = {
        "id": 1,
        "number": "SO-1",
        "customer": {"name": "A"},
        "branchId": 50,
        "shipment": {"id": "150", "name": "OUTLET"},
        "warehouse": {"id": 9, "name": "Gudang Utama"},
        "defaultWarehouse": {"name": "Gudang Default"},
        "detailItem": [
            {
                "quantity": 1,
                "item": {
                    "id": 1,
                    "name": "Item",
                    "detailSellingPrice": [{"branch": {"id": 3, "name": "SellingPrice Branch"}}],
                },
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["expedition"] == "OUTLET"
    assert so["outlet"] in (None, "", "-")
    assert so["_branch_id"] == "50"


def test_cake_in_love_keeps_live_ids_without_guessing_names():
    so = _normalize_accurate_so(CAKE_IN_LOVE_SO_DETAIL)
    assert so["_created_by_id"] == "1248273"
    assert so["_branch_id"] == "50"
    assert so["created_by_user"] is None
    assert so["outlet"] in (None, "", "-")
    assert so["expedition"] == "OUTLET"
    assert so["recipient"] == "CAKE IN LOVE"
    assert so["delivery_zone"] == "JS"
    assert so["sales"] == "Dimas Aji Prasetya"
    assert so["estimated_ship_date"] == "2026-09-05"
    assert so["line_items"][0]["color"] is None


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


HEADER_KETERANGAN = (
    "RETUR LID DARI MERK TPP KE SJP 1000PCS\n"
    "KIRIM TANGGAL : 07/09/2026\n"
    "(SEKALIAN NARIK MERK TPP 1000PCS)"
)


def test_header_keterangan_present_is_order_notes():
    raw = {
        "id": 77,
        "number": "SO-KET-1",
        "customer": {"name": "A"},
        "description": HEADER_KETERANGAN,
        "poNumberFromCustomer": "CUST-PO-9",
        "detailItem": [
            {
                "quantity": 500,
                "detailNotes": "SABLON WARNA ORANGE",
                "item": {"id": 1, "name": "14 oz Inject Datar Bening Klir"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["notes"] == HEADER_KETERANGAN
    assert "CUST-PO-9" not in so["notes"]
    assert so["line_items"][0]["notes"] == "SABLON WARNA ORANGE"
    assert so["line_items"][0]["notes"] != so["notes"]


def test_header_keterangan_blank_stays_blank():
    raw = {
        "id": 78,
        "number": "SO-KET-BLANK",
        "customer": {"name": "A"},
        "description": "   ",
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw)
    assert so["notes"] == ""
    missing = {
        "id": 79,
        "number": "SO-KET-MISSING",
        "customer": {"name": "A"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so2 = _normalize_accurate_so(missing)
    assert so2["notes"] == ""


def test_header_keterangan_not_copied_onto_multiline_items():
    so = _normalize_accurate_so(_two_line_cup_lid_so(header=HEADER_KETERANGAN))
    assert so["notes"] == HEADER_KETERANGAN
    assert so["line_items"][0]["notes"] == "SABLON WARNA ORANGE"
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][1]["notes"] in (None, "")
    assert so["line_items"][1]["color"] is None
    assert so["line_items"][0]["notes"] != HEADER_KETERANGAN
    assert so["line_items"][1]["notes"] != HEADER_KETERANGAN
