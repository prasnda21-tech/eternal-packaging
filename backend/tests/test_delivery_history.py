"""Delivery confirmation history — append-only, original evidence preserved."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.delivery_history import (
    append_confirmation,
    delivery_history_rows,
    prospective_confirmation_items,
    public_delivery_history,
    terkirim_qty_summary,
)
from services.customer_tracking import is_public_payload_safe, public_payload


def _po(**extra):
    base = {
        "id": "ord-1",
        "po_number": "PO/2026/A",
        "customer": "PT Contoh",
        "line_items": [
            {"name": "Item A", "quantity": 5000, "accurate_item_id": "a1", "production_process": "print"},
            {"name": "Item B", "quantity": 3000, "accurate_item_id": "b1", "production_process": "print"},
        ],
        "total_quantity": 8000,
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "loading_checklist": {"driver_name": "Budi", "vehicle_plate": "B 1234 XYZ"},
        "loading_info": {"lines": [{"line_idx": 0, "loaded_qty": 2000}]},
        "delivery_proofs": [
            {"id": "p1", "uploaded_at": "2026-08-27T14:20:00+07:00"},
            {"id": "p2", "uploaded_at": "2026-08-27T14:25:00+07:00"},
        ],
        "shipments": [{"id": "s1", "number": "SJ-1"}],
    }
    base.update(extra)
    return base


def _user(role="driver", uid="u-budi", name="Budi"):
    return {"sub": uid, "user_id": uid, "name": name, "email": f"{uid}@example.com", "role": role}


def test_append_only_two_confirmations():
    o = _po()
    r1 = append_confirmation(o, _user(), qty_increment=2000, now="2026-08-27T14:30:00+07:00", action_id="a1")
    assert r1["qty"] == 2000
    assert r1["driver_id"] == "u-budi"
    assert r1["driver_name"] == "Budi"
    assert r1["vehicle_plate"] == "B 1234 XYZ"
    assert r1["items"][0]["name"] == "Item A"
    assert r1["items"][0]["qty"] == 2000
    o["loading_info"] = {"lines": [{"line_idx": 0, "loaded_qty": 3000}]}
    o["loading_checklist"] = {"driver_name": "Andi", "vehicle_plate": "B 5678 ABC"}
    o["delivery_proofs"] = o["delivery_proofs"] + [
        {"id": "p3", "uploaded_at": "2026-08-28T09:00:00+07:00"},
        {"id": "p4", "uploaded_at": "2026-08-28T09:05:00+07:00"},
    ]
    r2 = append_confirmation(o, _user("driver", "u-andi", "Andi"), qty_increment=1000, now="2026-08-28T09:15:00+07:00", action_id="a2")
    assert r2["qty"] == 1000
    assert len(o["delivery_confirmations"]) == 2
    rows = delivery_history_rows(o)
    assert len(rows) == 2
    assert rows[0]["qty"] == 2000
    assert rows[0]["driver_name"] == "Budi"
    assert rows[0]["vehicle_plate"] == "B 1234 XYZ"
    assert rows[1]["qty"] == 1000
    assert rows[1]["driver_name"] == "Andi"
    assert rows[1]["vehicle_plate"] == "B 5678 ABC"


def test_double_submit_same_action_id_does_not_append():
    o = _po()
    append_confirmation(o, _user(), qty_increment=2000, now="2026-08-27T14:30:00+07:00", action_id="same")
    append_confirmation(o, _user(), qty_increment=2000, now="2026-08-27T14:31:00+07:00", action_id="same")
    assert len(o["delivery_confirmations"]) == 1


def test_zero_increment_does_not_append():
    o = _po()
    assert append_confirmation(o, _user(), qty_increment=0, now="2026-08-27T14:30:00+07:00") is None
    assert o.get("delivery_confirmations") in (None, [])


def test_prospective_items_match_selected_shipment_delta():
    o = _po()
    shipment = {
        "id": "s-partial",
        "lines": [{"line_idx": 1, "line_id": "line-b", "qty": 600}],
    }
    items = prospective_confirmation_items(
        o,
        qty_increment=600,
        shipment=shipment,
    )
    assert items == [{"line_idx": 1, "name": "Item B", "qty": 600, "line_id": "line-b"}]


def test_terkirim_summary_partial_and_over():
    o = _po(qty_delivered=6000)
    s = terkirim_qty_summary(o)
    assert s["total_po_qty"] == 8000
    assert s["qty_delivered"] == 6000
    assert s["qty_remaining"] == 2000
    over = terkirim_qty_summary(_po(qty_delivered=9000))
    assert over["qty_delivered"] == 9000
    assert over["qty_remaining"] == 0


def test_missing_fields_are_none_not_invented():
    o = _po(loading_checklist={}, delivery_confirmations=[{
        "id": "c1",
        "qty": 1000,
        "confirmed_at": "2026-08-27T14:30:00+07:00",
        "items": [{"line_idx": 0, "name": "Item A", "qty": 1000}],
        "proof_ids": ["p1"],
    }])
    row = delivery_history_rows(o)[0]
    assert row["driver_name"] is None
    assert row["vehicle_plate"] is None


def test_public_history_omits_ids_and_caps_photos():
    o = _po(
        tracking_token="tok-public-aaaaaaaa",
        delivery_confirmations=[{
            "id": "c1",
            "driver_id": "u-secret",
            "driver_name": "Budi",
            "confirmed_by_id": "u-secret",
            "vehicle_plate": "B 1234 XYZ",
            "qty": 2000,
            "confirmed_at": "2026-08-27T14:30:00+07:00",
            "items": [{"line_idx": 0, "name": "Item A", "qty": 2000}],
            "proof_ids": ["p1", "p2", "p3", "p4", "p5", "p6"],
        }],
        delivery_proofs=[{"id": f"p{i}", "uploaded_at": f"2026-08-27T14:2{i}:00+07:00"} for i in range(1, 7)],
    )
    payload = public_payload(o, "tok-public-aaaaaaaa")
    assert is_public_payload_safe(payload)
    blob = str(payload)
    assert "u-secret" not in blob
    assert "driver_id" not in blob
    assert "storage_path" not in blob
    hist = payload["delivery_history"]
    assert hist[0]["driver_name"] == "Budi"
    assert hist[0]["item"]
    assert hist[0]["qty"] == 2000
    assert hist[0]["vehicle_plate"] == "B 1234 XYZ"
    public_ids = {p["id"] for p in payload["delivery_proofs"]}
    assert len(public_ids) <= 5
    for photo in hist[0]["photos"]:
        assert photo["id"] in public_ids
        assert photo["url"].startswith("/api/public/tracking/")
