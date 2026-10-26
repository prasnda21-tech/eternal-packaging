"""Delivery schedule + reschedule rules (no live API).

Authoritative date is Tanggal Pengiriman (manual override or latest approval + 3).
Legacy order.deadline never drives scheduling.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.delivery_schedule import (
    RescheduleError,
    apply_reschedule,
    apply_set_date,
    effective_delivery_date,
    effective_delivery_location,
    find_reschedule_by_action_id,
    matches_schedule_filters,
    original_deadline,
    persistable_delivery_location,
    public_reschedule_snippet,
    reschedule_rows,
    schedule_row,
    schedule_rows,
    schedule_status_code,
    sj_item_lines,
    today_key,
)
from services.tanggal_pengiriman import apply_manual_tanggal_pengiriman


def _po(**extra):
    base = {
        "id": "ord-1",
        "po_number": "PO/2026/A",
        "customer": "PT Contoh",
        "deadline": "2026-08-30",
        "design_approved_at": "2026-08-27T10:00:00+07:00",
        "payment_approved_at": "2026-08-27T12:00:00+07:00",
        "delivery_status": "Belum Terkirim",
        "current_stage": 13,
        "line_items": [{"name": "Karton"}],
        "delivery_proofs": [{"id": "p1"}, {"id": "p2"}],
        "qty_delivered": 0,
        "packing_info": {"qty_packed": 100},
        "loading_info": {"lines": [{"loaded_qty": 100}]},
    }
    base.update(extra)
    return base


def _user(uid="u-driver", name="Budi", email="driver@example.com"):
    return {"id": uid, "name": name, "email": email}


def test_effective_uses_automatic_when_no_manual():
    o = _po()
    assert original_deadline(o) == "2026-08-30"
    # latest approval Thu 27 + 3 working days (Mon–Sat; Sun 30 skipped) = Mon 31
    assert effective_delivery_date(o) == "2026-08-31"


def test_manual_schedule_wins_over_automatic():
    o = _po(delivery_deadline_manual="2026-08-29T00:00:00+07:00")
    assert original_deadline(o) == "2026-08-30"
    assert effective_delivery_date(o) == "2026-08-29"


def test_legacy_deadline_alone_does_not_schedule():
    o = {
        "id": "ord-legacy",
        "po_number": "PO/LEG",
        "deadline": "2026-08-30",
        "delivery_status": "Belum Terkirim",
        "current_stage": 5,
    }
    assert effective_delivery_date(o) is None
    assert schedule_status_code(o, today="2026-08-30") == "belum_dijadwalkan"


def test_reschedule_wins_and_preserves_deadline():
    o = _po(deadline="2026-08-30")
    rec, mutated = apply_reschedule(
        o, reason="Hujan", new_date="2026-08-31", can_set_date=True, user=_user("u-admin", "Admin")
    )
    assert mutated is True
    assert rec["status"] == "scheduled"
    # previous = automatic working-day date (Sun skipped), not legacy calendar +3
    assert rec["previous_date"] == "2026-08-31"
    assert rec["new_date"] == "2026-08-31"
    assert o["deadline"] == "2026-08-30"
    assert original_deadline(o) == "2026-08-30"
    assert effective_delivery_date(o) == "2026-08-31"
    assert o["scheduled_delivery_date"] == "2026-08-31"
    assert o["delivery_status"] == "Belum Terkirim"
    assert len(o["delivery_proofs"]) == 2
    assert o["packing_info"]["qty_packed"] == 100


def test_empty_reason_rejected():
    o = _po()
    try:
        apply_reschedule(o, reason="   ", new_date="2026-08-31", can_set_date=True, user=_user())
        assert False, "expected reject"
    except RescheduleError as e:
        assert "Alasan" in str(e)
    assert o.get("delivery_reschedules") in (None, [])
    assert effective_delivery_date(o) == "2026-08-31"


def test_invalid_date_rejected_when_can_set_date():
    o = _po()
    try:
        apply_reschedule(o, reason="Telat", new_date="31/08/2026", can_set_date=True, user=_user())
        assert False, "expected reject"
    except RescheduleError as e:
        assert "Tanggal" in str(e)
    assert effective_delivery_date(o) == "2026-08-31"


def test_request_without_date_permission_is_pending():
    o = _po()
    rec, mutated = apply_reschedule(
        o,
        reason="Pelanggan tidak di tempat",
        new_date="2026-09-02",
        can_set_date=False,
        user=_user(),
    )
    assert mutated is True
    assert rec["status"] == "pending"
    assert rec["new_date"] is None
    assert rec["reason"] == "Pelanggan tidak di tempat"
    assert o.get("scheduled_delivery_date") is None
    assert effective_delivery_date(o) == "2026-08-31"


def test_authorized_set_date_appends_history():
    o = _po(deadline="2026-08-30")
    pending, _ = apply_reschedule(
        o, reason="Reason A", new_date=None, can_set_date=False, user=_user("u-d", "Driver")
    )
    rec, mutated = apply_set_date(
        o,
        pending["reschedule_id"],
        new_date="2026-08-31",
        user=_user("u-hp", "Kepala"),
        reason="Reason A",
    )
    assert mutated is True
    assert rec["status"] == "scheduled"
    assert rec["previous_date"] == "2026-08-31"
    assert rec["new_date"] == "2026-08-31"
    assert rec["fulfills_reschedule_id"] == pending["reschedule_id"]
    assert len(o["delivery_reschedules"]) == 2
    assert o["deadline"] == "2026-08-30"
    assert effective_delivery_date(o) == "2026-08-31"


def test_history_append_only_second_reschedule():
    o = _po(deadline="2026-08-30")
    apply_reschedule(o, reason="A", new_date="2026-08-31", can_set_date=True, user=_user("a", "A"))
    apply_reschedule(o, reason="B", new_date="2026-09-02", can_set_date=True, user=_user("a", "A"))
    rows = reschedule_rows(o)
    # first previous is automatic working-day Mon 31 (not calendar Sun 30)
    assert [(r["previous_date"], r["new_date"]) for r in rows] == [
        ("2026-08-31", "2026-08-31"),
        ("2026-08-31", "2026-09-02"),
    ]
    assert effective_delivery_date(o) == "2026-09-02"
    assert o["deadline"] == "2026-08-30"


def test_double_submit_same_action_id_one_record():
    o = _po()
    r1, m1 = apply_reschedule(
        o, reason="Hujan", new_date="2026-08-31", can_set_date=True, user=_user(), action_id="act-1"
    )
    r2, m2 = apply_reschedule(
        o, reason="Hujan", new_date="2026-08-31", can_set_date=True, user=_user(), action_id="act-1"
    )
    assert m1 is True
    assert m2 is False
    assert r1["reschedule_id"] == r2["reschedule_id"]
    assert len(o["delivery_reschedules"]) == 1


def test_failed_validation_does_not_write():
    o = _po()
    proofs = list(o["delivery_proofs"])
    try:
        apply_reschedule(o, reason="", new_date="2026-08-31", can_set_date=True, user=_user())
    except RescheduleError:
        pass
    assert o["deadline"] == "2026-08-30"
    assert o["delivery_proofs"] == proofs
    assert o["delivery_status"] == "Belum Terkirim"
    assert find_reschedule_by_action_id(o, "x") is None


def test_schedule_status_today_and_late():
    o = _po(
        design_approved_at="2026-08-24T10:00:00+07:00",
        payment_approved_at="2026-08-24T10:00:00+07:00",
    )
    # auto = 27
    assert schedule_status_code(o, today="2026-08-27") == "hari_ini"
    assert schedule_status_code(o, today="2026-08-28") == "terlambat"
    o2 = _po(scheduled_delivery_date="2026-08-31")
    assert schedule_status_code(o2, today="2026-08-27") == "terjadwal"
    o3 = _po(delivery_status="Terkirim")
    o3.pop("qty_delivered")
    assert schedule_status_code(o3, today="2026-08-27") == "terkirim"
    partial = _po(
        delivery_status="Terkirim",
        current_stage=14,
        qty_delivered=1,
        line_items=[{"name": "Karton", "quantity": 100, "production_process": "print"}],
    )
    assert schedule_status_code(partial, today="2026-08-27") != "terkirim"


def test_buffer_stock_past_sj_row_exists_but_not_terlambat():
    sh = {
        "id": "sh-buf",
        "number": "SJBUF",
        "shipment_index": 1,
        "delivery_date": "2026-09-10",
        "lines": [{"line_id": "lid-a", "line_idx": 0, "name": "Cup", "qty": 1000}],
    }
    buffer_po = _po(buffer_stock=True, shipments=[sh])
    normal_po = _po(buffer_stock=False, shipments=[sh])
    buf_rows = schedule_rows(buffer_po, today="2026-09-19")
    norm_rows = schedule_rows(normal_po, today="2026-09-19")
    assert len(buf_rows) == 1
    assert buf_rows[0]["status"] == "terjadwal"
    assert buf_rows[0]["status"] != "terlambat"
    assert buf_rows[0]["effective_delivery_date"] == "2026-09-10"
    assert buf_rows[0]["sj_label"] == "SJBUF"
    assert len(norm_rows) == 1
    assert norm_rows[0]["status"] == "terlambat"


def test_filter_and_reset_semantics():
    # Automatic future date → terjadwal (not belum).
    row = schedule_row(_po(customer="PT Contoh"), today="2026-08-20")
    assert row["status"] == "terjadwal"
    assert row["effective_delivery_date"] == "2026-08-31"
    assert matches_schedule_filters(row, {"po": "PO/2026", "customer": "contoh", "status": "terjadwal"})
    assert not matches_schedule_filters(row, {"po": "ZZZ"})
    assert not matches_schedule_filters(row, {"status": "terkirim"})
    assert matches_schedule_filters(row, {"date_from": "2026-08-31", "date_to": "2026-08-31"})
    assert not matches_schedule_filters(row, {"date_from": "2026-09-01"})


def test_automatic_working_days_skip_sunday():
    """Owner contract: Minggu libur — Mon–Sat working days for automatic Tanggal Pengiriman."""
    from datetime import date
    from services.tanggal_pengiriman import automatic_tanggal_pengiriman, automatic_working_tanggal_pengiriman

    o = _po(
        design_approved_at="2026-08-27T10:00:00+07:00",
        payment_approved_at="2026-08-27T12:00:00+07:00",
    )
    # Calendar +3 lands on Sunday 30 — not the active automatic contract.
    assert automatic_tanggal_pengiriman(o, 3) == "2026-08-30"
    assert date.fromisoformat("2026-08-30").weekday() == 6  # Sunday
    # Working-day +3: Fri 28, Sat 29, skip Sun 30 → Mon 31
    assert automatic_working_tanggal_pengiriman(o, 3, None) == "2026-08-31"
    assert effective_delivery_date(o) == "2026-08-31"


def test_manual_and_reschedule_accept_sunday_override():
    """Authorized manual / reschedule may land on Sunday; auto still skips Minggu."""
    from datetime import date

    sunday = "2026-08-30"
    assert date.fromisoformat(sunday).weekday() == 6

    # B + D: authorized manual Sunday wins over automatic Monday.
    o = _po(
        design_approved_at="2026-08-27T10:00:00+07:00",
        payment_approved_at="2026-08-27T12:00:00+07:00",
    )
    assert effective_delivery_date(o) == "2026-08-31"
    apply_manual_tanggal_pengiriman(o, sunday)
    assert effective_delivery_date(o) == sunday

    # C: authorized reschedule to Sunday is accepted and becomes effective.
    o2 = _po(
        design_approved_at="2026-08-27T10:00:00+07:00",
        payment_approved_at="2026-08-27T12:00:00+07:00",
    )
    rec, mutated = apply_reschedule(
        o2, reason="Request Minggu", new_date=sunday, can_set_date=True, user=_user("u-admin", "Admin")
    )
    assert mutated is True
    assert rec["status"] == "scheduled"
    assert rec["previous_date"] == "2026-08-31"
    assert rec["new_date"] == sunday
    assert effective_delivery_date(o2) == sunday

    # E: unauthorized override stays pending; automatic Monday remains.
    o3 = _po(
        design_approved_at="2026-08-27T10:00:00+07:00",
        payment_approved_at="2026-08-27T12:00:00+07:00",
    )
    pending, mutated3 = apply_reschedule(
        o3, reason="Minta Minggu", new_date=sunday, can_set_date=False, user=_user()
    )
    assert mutated3 is True
    assert pending["status"] == "pending"
    assert pending["new_date"] is None
    assert o3.get("scheduled_delivery_date") is None
    assert effective_delivery_date(o3) == "2026-08-31"

    # F: SJ scheduled on Sunday remains visible via date filters.
    sh = {
        "id": "sh-sun",
        "number": "SJSUN",
        "shipment_index": 1,
        "delivery_date": sunday,
        "lines": [{"line_id": "lid-a", "line_idx": 0, "name": "Cup", "qty": 1000}],
    }
    row = schedule_rows(_po(shipments=[sh]), today="2026-08-20")[0]
    assert row["effective_delivery_date"] == sunday
    assert matches_schedule_filters(row, {"date_from": sunday, "date_to": sunday})
    assert not matches_schedule_filters(row, {"date_from": "2026-08-31", "date_to": "2026-08-31"})


def test_public_snippet_hides_reason_and_actors():
    o = _po()
    apply_reschedule(o, reason="INTERNAL SECRET", new_date="2026-08-31", can_set_date=True, user=_user())
    snippet = public_reschedule_snippet(o)
    assert snippet == {"scheduled_date": "2026-08-31"}
    assert "INTERNAL" not in str(snippet)
    assert "Budi" not in str(snippet)


def test_schedule_row_omits_deadline_and_jadwal_manual_columns():
    o = _po()
    apply_manual_tanggal_pengiriman(o, "2026-08-28")
    row = schedule_row(o, today="2026-08-20")
    assert row["tanggal_pengiriman"] == "2026-08-28"
    assert row["effective_delivery_date"] == "2026-08-28"
    assert row["deadline"] is None
    assert row["jadwal_manual"] is None


def test_today_key_is_wib():
    assert len(today_key()) == 10


def test_today_key_uses_wib_not_utc_at_0001():
    from datetime import datetime, timezone
    # 2026-08-27 17:01 UTC = 2026-08-28 00:01 WIB
    now = datetime(2026, 8, 27, 17, 1, tzinfo=timezone.utc)
    assert now.date().isoformat() == "2026-08-27"
    assert today_key(now) == "2026-08-28"


def test_today_key_wib_0659_still_same_business_day():
    from datetime import datetime, timezone, timedelta
    wib = timezone(timedelta(hours=7))
    now = datetime(2026, 8, 28, 6, 59, tzinfo=wib)
    assert now.astimezone(timezone.utc).date().isoformat() == "2026-08-27"
    assert today_key(now) == "2026-08-28"


def test_today_key_wib_0700_stays_wib_date():
    from datetime import datetime, timezone, timedelta
    wib = timezone(timedelta(hours=7))
    now = datetime(2026, 8, 28, 7, 0, tzinfo=wib)
    assert now.astimezone(timezone.utc).date().isoformat() == "2026-08-28"
    assert today_key(now) == "2026-08-28"


def test_schedule_hari_ini_follows_wib_today_key():
    from datetime import datetime, timezone
    o = _po(scheduled_delivery_date="2026-08-28")
    now = datetime(2026, 8, 27, 17, 1, tzinfo=timezone.utc)  # 00:01 WIB 28 Aug
    assert schedule_status_code(o, today=today_key(now)) == "hari_ini"
    # UTC calendar date would have treated 28 Aug as still "terjadwal"
    assert now.date().isoformat() == "2026-08-27"
    assert schedule_status_code(o, today=now.date().isoformat()) == "terjadwal"


def test_schedule_row_embeds_canonical_shipment_percents():
    o = _po(
        total_quantity=10000,
        line_items=[{"name": "Cetak", "quantity": 10000, "production_process": "print"}],
        qty_delivered=5000,
        operator_splits=[{"line_idx": 0, "qty_completed": 7500}],
    )
    row = schedule_row(o)
    assert row["percent_delivered"] == 50
    assert row["percent_delivered_display"] == 50
    assert row["percent_ready_to_ship"] == 75
    assert row["percent_ready_to_ship_display"] == 75
    assert row["qty_delivered"] == 5000
    assert row["qty_packed"] == 100
    assert row["item_label"] == "Cetak"  # still in API; UI may omit column
    assert row["sj_item_lines"] == []


def test_schedule_row_exposes_delivery_coords_for_status_lokasi():
    """Presentation-only lat/lng for desktop Status Lokasi — not eligibility."""
    with_coords = schedule_row(_po(delivery_location={"lat": -6.2, "lng": 106.8, "formatted_address": "Jl A"}))
    assert with_coords["lat"] == -6.2
    assert with_coords["lng"] == 106.8

    address_only = schedule_row(_po(delivery_location={"formatted_address": "Jl Tanpa Koordinat"}, address="Jl Order"))
    assert address_only["lat"] is None
    assert address_only["lng"] is None

    zero = schedule_row(_po(delivery_location={"lat": 0, "lng": 0}))
    assert zero["lat"] is None
    assert zero["lng"] is None


def test_schedule_row_one_shipment_one_line():
    sh = {
        "id": "sh-1",
        "number": "SJ001",
        "shipment_index": 1,
        "delivery_date": "2026-09-10",
        "lines": [{"line_id": "lid-a", "line_idx": 0, "name": "Cup", "qty": 1000}],
    }
    o = _po(shipments=[sh])
    rows = schedule_rows(o)
    assert len(rows) == 1
    row = rows[0]
    assert row["effective_delivery_date"] == "2026-09-10"
    assert row["sj_label"] == "SJ001"
    assert row["shipment_id"] == "sh-1"
    assert row["sj_item_lines"] == [{
        "shipment_id": "sh-1",
        "sj_number": "SJ001",
        "line_id": "lid-a",
        "line_idx": 0,
        "name": "Cup",
        "qty": 1000,
    }]
    assert row["id"] == "ord-1"
    assert row["order_number"] == "PO/2026/A"


def test_schedule_rows_two_dates_keep_historical_batch_after_newer_sj():
    """PO001: Cup 10 Sep / Lid 12 Sep — later SJ must not rewrite Sep 10."""
    o = _po(
        po_number="PO001",
        line_items=[{"name": "Cup", "quantity": 1000}, {"name": "Lid", "quantity": 1000}],
        shipments=[
            {
                "id": "sh-1",
                "number": "SJ001",
                "shipment_index": 1,
                "delivery_date": "2026-09-10",
                "lines": [{"line_id": "lid-cup", "line_idx": 0, "name": "Cup", "qty": 1000}],
            },
            {
                "id": "sh-2",
                "number": "SJ001-2",
                "shipment_index": 2,
                "delivery_date": "2026-09-12",
                "lines": [{"line_id": "lid-lid", "line_idx": 1, "name": "Lid", "qty": 1000}],
            },
        ],
    )
    rows = schedule_rows(o, today="2026-09-11")
    assert len(rows) == 2
    sep10 = next(r for r in rows if r["effective_delivery_date"] == "2026-09-10")
    sep12 = next(r for r in rows if r["effective_delivery_date"] == "2026-09-12")
    assert sep10["sj_label"] == "SJ001"
    assert [ln["name"] for ln in sep10["sj_item_lines"]] == ["Cup"]
    assert [ln["qty"] for ln in sep10["sj_item_lines"]] == [1000]
    assert "Lid" not in [ln["name"] for ln in sep10["sj_item_lines"]]
    assert sep10["shipment_id"] == sep10["sj_item_lines"][0]["shipment_id"]
    assert sep12["sj_label"] == "SJ001-2"
    assert [ln["name"] for ln in sep12["sj_item_lines"]] == ["Lid"]
    assert [ln["qty"] for ln in sep12["sj_item_lines"]] == [1000]
    assert "Cup" not in [ln["name"] for ln in sep12["sj_item_lines"]]
    assert sep12["shipment_id"] == sep12["sj_item_lines"][0]["shipment_id"]
    assert matches_schedule_filters(sep10, {"date_from": "2026-09-10", "date_to": "2026-09-10"})
    assert not matches_schedule_filters(sep12, {"date_from": "2026-09-10", "date_to": "2026-09-10"})
    assert matches_schedule_filters(sep12, {"date_from": "2026-09-12", "date_to": "2026-09-12"})
    assert sep10["item_label"] == "2 item"
    assert sep10["sj_item_lines"][0]["name"] != sep12["sj_item_lines"][0]["name"]


def test_schedule_rows_three_dates_and_same_name_lines_stay_separate():
    o = _po(shipments=[
        {
            "id": "sh-1",
            "number": "SJ001",
            "shipment_index": 1,
            "delivery_date": "2026-09-08",
            "lines": [{"line_id": "a", "line_idx": 0, "name": "Cup", "qty": 100}],
        },
        {
            "id": "sh-2",
            "number": "SJ001-2",
            "shipment_index": 2,
            "delivery_date": "2026-09-10",
            "lines": [
                {"line_id": "b1", "line_idx": 0, "name": "12 oz PET Datar 11 gr SJP", "qty": 1000},
                {"line_id": "b2", "line_idx": 1, "name": "12 oz PET Datar 11 gr SJP", "qty": 2000},
            ],
        },
        {
            "id": "sh-3",
            "number": "SJ001-3",
            "shipment_index": 3,
            "delivery_date": "2026-09-12",
            "lines": [{"line_id": "c", "line_idx": 2, "name": "Lid", "qty": 50}],
        },
    ])
    rows = schedule_rows(o)
    assert [r["sj_label"] for r in rows] == ["SJ001", "SJ001-2", "SJ001-3"]
    assert [r["effective_delivery_date"] for r in rows] == ["2026-09-08", "2026-09-10", "2026-09-12"]
    mid = rows[1]
    assert [ln["line_id"] for ln in mid["sj_item_lines"]] == ["b1", "b2"]
    assert [ln["qty"] for ln in mid["sj_item_lines"]] == [1000, 2000]
    assert mid["sj_item_lines"][0]["name"] == mid["sj_item_lines"][1]["name"]
    assert "Lid" not in [ln["name"] for ln in mid["sj_item_lines"]]


def test_schedule_rows_same_date_keep_separate_shipments_by_index():
    o = _po(shipments=[
        {
            "id": "sh-a",
            "number": "SJ001",
            "shipment_index": 1,
            "delivery_date": "2026-09-10",
            "lines": [{"line_id": "a", "line_idx": 0, "name": "Cup", "qty": 100}],
        },
        {
            "id": "sh-b",
            "number": "SJ001-2",
            "shipment_index": 2,
            "delivery_date": "2026-09-10",
            "lines": [{"line_id": "b", "line_idx": 1, "name": "Lid", "qty": 200}],
        },
    ])
    rows = schedule_rows(o)
    assert len(rows) == 2
    assert [r["shipment_index"] for r in rows] == [1, 2]
    assert [r["sj_label"] for r in rows] == ["SJ001", "SJ001-2"]
    assert [ln["name"] for ln in rows[0]["sj_item_lines"]] == ["Cup"]
    assert [ln["name"] for ln in rows[1]["sj_item_lines"]] == ["Lid"]


def test_schedule_row_missing_shipments_or_lines_is_empty_safe():
    assert schedule_row(_po())["sj_item_lines"] == []
    assert schedule_row(_po(shipments=None))["sj_item_lines"] == []
    assert schedule_row(_po(shipments="bad"))["sj_item_lines"] == []
    assert schedule_row(_po(), shipment={"id": "sh-x", "number": "SJ/X", "lines": None})["sj_item_lines"] == []
    assert sj_item_lines({}, {"id": "sh-x", "lines": ["skip", {"name": "Ok", "qty": 5}]}) == [
        {"shipment_id": "sh-x", "sj_number": None, "line_id": None, "line_idx": None, "name": "Ok", "qty": 5},
    ]
    assert sj_item_lines({}, {"id": "sh-u", "lines": [{"name": "Ok", "qty": 5, "unit": "pcs"}]}) == [
        {"shipment_id": "sh-u", "sj_number": None, "line_id": None, "line_idx": None, "name": "Ok", "qty": 5, "unit": "pcs"},
    ]
    assert sj_item_lines(_po(shipments=[{"id": "sh-x", "number": "SJ/X", "lines": [{"name": "Nope", "qty": 1}]}])) == []


def test_schedule_rows_no_sj_returns_empty():
    """A — PO with no Surat Jalan must not appear on Jadwal Pengiriman."""
    assert schedule_rows(_po()) == []
    assert schedule_rows(_po(shipments=None)) == []
    assert schedule_rows(_po(shipments=[])) == []
    assert schedule_rows(_po(shipments="bad")) == []


def test_schedule_rows_missing_sj_id_excluded():
    """C1 — dated shipment with number but no canonical id is not a schedule row."""
    o = _po(shipments=[{
        "number": "SJ001",
        "delivery_date": "2026-09-10",
        "lines": [{"line_id": "a", "line_idx": 0, "name": "Cup", "qty": 100}],
    }])
    assert schedule_rows(o) == []


def test_schedule_rows_missing_sj_number_excluded():
    """C2 — dated shipment with id but blank number is not a schedule row."""
    o = _po(shipments=[{
        "id": "sh-1",
        "number": "",
        "sj_number": "",
        "delivery_date": "2026-09-10",
        "lines": [{"line_id": "a", "line_idx": 0, "name": "Cup", "qty": 100}],
    }])
    assert schedule_rows(o) == []


def test_schedule_rows_mixed_valid_and_invalid_keeps_only_canonical_sj():
    """E — invalid partial shipment must not hide or replace the valid SJ row."""
    o = _po(shipments=[
        {
            "id": "sh-valid",
            "number": "SJ001",
            "shipment_index": 1,
            "delivery_date": "2026-09-10",
            "lines": [{"line_id": "a", "line_idx": 0, "name": "Cup", "qty": 100}],
        },
        {
            "id": "sh-partial",
            "number": "",
            "sj_number": "",
            "shipment_index": 2,
            "delivery_date": "2026-09-11",
            "lines": [{"line_id": "b", "line_idx": 1, "name": "Lid", "qty": 50}],
        },
    ])
    rows = schedule_rows(o)
    assert len(rows) == 1
    assert rows[0]["shipment_id"] == "sh-valid"
    assert rows[0]["sj_label"] == "SJ001"
    assert rows[0]["effective_delivery_date"] == "2026-09-10"


def test_schedule_rows_deleted_last_sj_returns_empty():
    """F — after last canonical SJ is removed, do not emit a PO-level row."""
    o = _po(shipments=[])
    assert schedule_rows(o) == []
    leftover_undated = _po(shipments=[{"id": "sh-x", "number": "SJ/X"}])
    assert schedule_rows(leftover_undated) == []


def test_schedule_rows_partial_sj_qty_does_not_require_full_po_qty():
    """PO 10,000 with one valid SJ 5,000 is one row; remainder without SJ is not a row."""
    o = _po(
        total_quantity=10000,
        line_items=[{"name": "Cup", "quantity": 10000}],
        shipments=[{
            "id": "sh-half",
            "number": "SJ001",
            "shipment_index": 1,
            "delivery_date": "2026-09-10",
            "lines": [{"line_id": "a", "line_idx": 0, "name": "Cup", "qty": 5000}],
        }],
    )
    rows = schedule_rows(o)
    assert len(rows) == 1
    assert rows[0]["shipment_id"] == "sh-half"
    assert rows[0]["sj_label"] == "SJ001"
    assert rows[0]["sj_item_lines"][0]["qty"] == 5000

    o["shipments"].append({
        "id": "sh-half-2",
        "number": "SJ001-2",
        "shipment_index": 2,
        "delivery_date": "2026-09-11",
        "lines": [{"line_id": "b", "line_idx": 0, "name": "Cup", "qty": 5000}],
    })
    rows2 = schedule_rows(o)
    assert len(rows2) == 2
    assert [r["sj_label"] for r in rows2] == ["SJ001", "SJ001-2"]
    assert [r["shipment_id"] for r in rows2] == ["sh-half", "sh-half-2"]
    assert [r["sj_item_lines"][0]["qty"] for r in rows2] == [5000, 5000]


def _dated_sj(sid, number, loc=None, qty=5000):
    sh = {
        "id": sid,
        "number": number,
        "shipment_index": int(sid.split("-")[-1]) if str(sid).split("-")[-1].isdigit() else 1,
        "delivery_date": "2026-09-10",
        "lines": [{"line_id": sid, "line_idx": 0, "name": "Cup", "qty": qty}],
    }
    if loc is not None:
        sh["delivery_location"] = loc
    return sh


def test_effective_location_po_only_then_sj_override():
    loc_a = {"lat": -6.2, "lng": 106.8, "formatted_address": "PO-A"}
    loc_b = {"lat": -6.3, "lng": 106.9, "formatted_address": "SJ-B"}
    loc_c = {"lat": -6.4, "lng": 107.0, "formatted_address": "SJ-C"}
    po = _po(
        delivery_location=loc_a,
        total_quantity=10000,
        line_items=[{"name": "Cup", "quantity": 10000}],
        shipments=[
            _dated_sj("sh-1", "SJ001", None, 5000),
            _dated_sj("sh-2", "SJ001-2", loc_b, 3000),
            _dated_sj("sh-3", "SJ001-3", loc_c, 2000),
        ],
    )
    po["shipments"][0]["shipment_index"] = 1
    po["shipments"][1]["shipment_index"] = 2
    po["shipments"][2]["shipment_index"] = 3

    # A — PO only
    a = next(r for r in schedule_rows(po) if r["shipment_id"] == "sh-1")
    assert a["lat"] == -6.2
    assert a["lng"] == 106.8

    # C/D — SJ override wins per shipment
    b = next(r for r in schedule_rows(po) if r["shipment_id"] == "sh-2")
    c = next(r for r in schedule_rows(po) if r["shipment_id"] == "sh-3")
    assert b["lat"] == -6.3 and b["lng"] == 106.9
    assert c["lat"] == -6.4 and c["lng"] == 107.0
    assert po["delivery_location"]["lat"] == -6.2


def test_effective_location_sj_only_when_po_empty():
    loc_b = {"lat": -6.31, "lng": 106.91}
    po = _po(shipments=[_dated_sj("sh-1", "SJ001", loc_b)])
    po["shipments"][0]["shipment_index"] = 1
    rows = schedule_rows(po)
    assert len(rows) == 1
    assert rows[0]["lat"] == -6.31
    assert rows[0]["lng"] == 106.91


def test_effective_location_invalid_sj_falls_back_to_po():
    loc_a = {"lat": -6.2, "lng": 106.8}
    po = _po(
        delivery_location=loc_a,
        shipments=[
            _dated_sj("sh-lat-only", "SJ001", {"lat": -6.5}),
            _dated_sj("sh-lng-only", "SJ001-2", {"lng": 106.9}),
            _dated_sj("sh-zero", "SJ001-3", {"lat": 0, "lng": 0}),
            _dated_sj("sh-range", "SJ001-4", {"lat": 91, "lng": 106.8}),
        ],
    )
    for i, sh in enumerate(po["shipments"], start=1):
        sh["shipment_index"] = i
        sh["delivery_date"] = "2026-09-10"
    rows = schedule_rows(po)
    assert len(rows) == 4
    for r in rows:
        assert r["lat"] == -6.2
        assert r["lng"] == 106.8


def test_effective_location_neither_is_empty():
    po = _po(shipments=[_dated_sj("sh-1", "SJ001", {"lat": -6.5})])
    po["shipments"][0]["shipment_index"] = 1
    rows = schedule_rows(po)
    assert len(rows) == 1
    assert rows[0]["lat"] is None
    assert rows[0]["lng"] is None


def test_persistable_delivery_location_rejects_partial_and_zero():
    assert persistable_delivery_location({"lat": -6.2, "lng": 106.8})["lat"] == -6.2
    assert persistable_delivery_location({"lat": -6.2}) is None
    assert persistable_delivery_location({"lng": 106.8}) is None
    assert persistable_delivery_location({"lat": 0, "lng": 0}) is None
    assert persistable_delivery_location({"lat": 91, "lng": 10}) is None
    assert persistable_delivery_location(None) is None
    sj = {"delivery_location": {"lat": -6.3, "lng": 106.9}}
    order = {"delivery_location": {"lat": -6.2, "lng": 106.8}}
    eff = effective_delivery_location(sj, order)
    assert eff["lat"] == -6.3


def _role_po(**extra):
    base = {
        "id": "ord-role",
        "po_number": "PO/2026/ROLE",
        "customer": "RENEA",
        "design_status": "Approved",
        "payment_bypassed": True,
        "operator_splits": [{"line_idx": 0, "quantity": 10000, "qty_completed": 10000}],
        "helper_prep": {"qty_prepared": 10000},
        "screener_info": {"screen_qty": 10},
        "sample_files": [{"id": "s1"}],
        "sample_approved": True,
        "line_items": [{"id": "lid-a", "name": "Cetak", "quantity": 10000, "production_process": "print"}],
        "packing_info": {"qty_packed": 4000, "lines": [{"line_idx": 0, "line_id": "lid-a", "packed_qty": 4000}]},
        "current_stage": 12,
        "delivery_status": "Belum Terkirim",
        "qty_delivered": 0,
        "total_quantity": 10000,
        "cancelled": False,
        "surat_jalan_created": True,
        "surat_jalan_files": [
            {"id": "sh-1", "shipment_id": "sh-1", "name": "SJ-1.pdf", "uploaded_at": "t1"},
            {"id": "sh-2", "shipment_id": "sh-2", "name": "SJ-2.pdf", "uploaded_at": "t2"},
        ],
        "shipments": [
            {
                "id": "sh-1",
                "number": "SJ-1",
                "shipment_index": 1,
                "delivery_date": "2026-09-15",
                "lines": [{"line_id": "lid-a", "line_idx": 0, "name": "Cetak", "qty": 5000}],
            },
            {
                "id": "sh-2",
                "number": "SJ-2",
                "shipment_index": 2,
                "delivery_date": "2026-09-16",
                "lines": [{"line_id": "lid-a", "line_idx": 0, "name": "Cetak", "qty": 5000}],
            },
        ],
    }
    base.update(extra)
    return base


def test_schedule_packing_chip_done_when_stage_past_packing_even_if_qty_partial():
    from services.workflow_stage_status import resolve_stage_chip, STAGE_PACKING

    o = _role_po()
    assert o["packing_info"]["qty_packed"] == 4000
    assert resolve_stage_chip(STAGE_PACKING, o, 12)["state"] == "done"
    rows = schedule_rows(o)
    assert rows[0]["qty_packed"] == 4000
    assert rows[0]["role_status"]["pack"] == "done"


def test_schedule_loading_complete_and_not_complete():
    o = _role_po(
        current_stage=13,
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sh-1",
            "lines": [{"line_idx": 0, "qty": 5000}],
        }],
    )
    rows = schedule_rows(o)
    by_id = {r["shipment_id"]: r for r in rows}
    assert by_id["sh-1"]["role_status"]["load"] == "done"
    assert by_id["sh-2"]["role_status"]["load"] == "pending"
    assert by_id["sh-1"]["role_status"]["load"] != by_id["sh-2"]["role_status"]["load"]


def test_schedule_kirim_current_zero_delivered_is_pending_not_idle():
    o = _role_po(
        current_stage=13,
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sh-1",
            "lines": [{"line_idx": 0, "qty": 5000}],
        }],
        qty_delivered=0,
        delivery_status="Belum Terkirim",
    )
    rows = schedule_rows(o)
    by_id = {r["shipment_id"]: r for r in rows}
    assert by_id["sh-1"]["role_status"]["kirim"] == "pending"
    assert by_id["sh-2"]["role_status"]["kirim"] == "idle"


def test_schedule_kirim_complete_only_for_delivered_sj():
    o = _role_po(
        current_stage=13,
        loading_confirmations=[
            {"id": "c1", "surat_jalan_id": "sh-1", "lines": [{"line_idx": 0, "qty": 5000}]},
            {"id": "c2", "surat_jalan_id": "sh-2", "lines": [{"line_idx": 0, "qty": 5000}]},
        ],
        shipments=[
            {
                "id": "sh-1",
                "number": "SJ-1",
                "shipment_index": 1,
                "delivery_date": "2026-09-15",
                "delivered_at": "2026-09-15T10:00:00+07:00",
                "delivery_status": "Terkirim",
                "lines": [{"line_id": "lid-a", "line_idx": 0, "name": "Cetak", "qty": 5000}],
            },
            {
                "id": "sh-2",
                "number": "SJ-2",
                "shipment_index": 2,
                "delivery_date": "2026-09-16",
                "lines": [{"line_id": "lid-a", "line_idx": 0, "name": "Cetak", "qty": 5000}],
            },
        ],
    )
    rows = schedule_rows(o)
    by_id = {r["shipment_id"]: r for r in rows}
    assert by_id["sh-1"]["role_status"]["kirim"] == "done"
    assert by_id["sh-1"]["role_status"]["load"] == "done"
    assert by_id["sh-2"]["role_status"]["kirim"] != "done"
    assert by_id["sh-2"]["role_status"]["load"] == "done"


def test_schedule_no_sj_rows_remain_excluded():
    assert schedule_rows(_role_po(shipments=[])) == []
    assert schedule_rows(_role_po(shipments=[{
        "id": "sh-x",
        "number": "",
        "delivery_date": "2026-09-15",
        "lines": [{"line_idx": 0, "qty": 1}],
    }])) == []


def test_role_status_does_not_leak_or_overwrite_sj_locations():
    loc_a = {"lat": -6.2, "lng": 106.8, "formatted_address": "PO-A"}
    loc_b = {"lat": -6.3, "lng": 106.9, "formatted_address": "SJ-B"}
    loc_c = {"lat": -6.4, "lng": 107.0, "formatted_address": "SJ-C"}
    o = _role_po(
        delivery_location=loc_a,
        current_stage=13,
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sh-1",
            "lines": [{"line_idx": 0, "qty": 5000}],
        }],
    )
    o["shipments"][0]["delivery_location"] = loc_b
    o["shipments"][1]["delivery_location"] = loc_c
    rows = schedule_rows(o)
    by_id = {r["shipment_id"]: r for r in rows}
    assert by_id["sh-1"]["lat"] == -6.3 and by_id["sh-1"]["lng"] == 106.9
    assert by_id["sh-2"]["lat"] == -6.4 and by_id["sh-2"]["lng"] == 107.0
    assert o["delivery_location"]["lat"] == -6.2
    assert by_id["sh-1"]["role_status"]["load"] == "done"
    assert by_id["sh-2"]["role_status"]["load"] == "pending"
    assert by_id["sh-1"]["role_status"]["pack"] == by_id["sh-2"]["role_status"]["pack"] == "done"


def test_sj_qty_payload_no_sj_still_emits_no_row():
    assert schedule_rows(_role_po(shipments=[])) == []


def test_sj_qty_payload_one_sj_has_own_load_and_delivered_fields():
    o = _role_po(
        shipments=[{
            "id": "sh-1",
            "number": "SJ-1",
            "shipment_index": 1,
            "delivery_date": "2026-09-15",
            "lines": [{"line_id": "lid-a", "line_idx": 0, "name": "Cetak", "qty": 5000}],
        }],
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sh-1",
            "lines": [{"line_idx": 0, "qty": 5000}],
        }],
        delivery_confirmations=[{"shipment_id": "sh-1", "qty": 5000}],
    )
    rows = schedule_rows(o)
    assert len(rows) == 1
    row = rows[0]
    assert row["qty_loaded"] == 5000
    assert row["qty_load_need"] == 5000
    assert row["qty_delivered_sj"] == 5000
    assert row["qty_sj"] == 5000


def test_sj_qty_payload_two_sj_load_and_kirim_stay_isolated():
    o = _role_po(
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sh-1",
            "lines": [{"line_idx": 0, "qty": 5000}],
        }],
        delivery_confirmations=[{"shipment_id": "sh-1", "qty": 5000}],
    )
    rows = schedule_rows(o)
    by_id = {r["shipment_id"]: r for r in rows}
    assert by_id["sh-1"]["qty_loaded"] == 5000
    assert by_id["sh-1"]["qty_load_need"] == 5000
    assert by_id["sh-2"]["qty_loaded"] == 0
    assert by_id["sh-2"]["qty_load_need"] == 5000
    assert by_id["sh-1"]["qty_delivered_sj"] == 5000
    assert by_id["sh-1"]["qty_sj"] == 5000
    assert by_id["sh-2"]["qty_delivered_sj"] == 0
    assert by_id["sh-2"]["qty_sj"] == 5000


def test_sj_qty_payload_partial_loading_and_partial_delivery_numerators():
    o = _role_po(
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sh-2",
            "lines": [{"line_idx": 0, "qty": 2000}],
        }],
        delivery_confirmations=[{"shipment_id": "sh-2", "qty": 2000}],
    )
    rows = schedule_rows(o)
    by_id = {r["shipment_id"]: r for r in rows}
    assert by_id["sh-1"]["qty_loaded"] == 0
    assert by_id["sh-1"]["qty_load_need"] == 5000
    assert by_id["sh-2"]["qty_loaded"] == 2000
    assert by_id["sh-2"]["qty_load_need"] == 5000
    assert by_id["sh-1"]["qty_delivered_sj"] == 0
    assert by_id["sh-1"]["qty_sj"] == 5000
    assert by_id["sh-2"]["qty_delivered_sj"] == 2000
    assert by_id["sh-2"]["qty_sj"] == 5000


def test_sj_qty_payload_does_not_change_location_precedence():
    loc_a = {"lat": -6.2, "lng": 106.8, "formatted_address": "PO-A"}
    loc_b = {"lat": -6.3, "lng": 106.9, "formatted_address": "SJ-B"}
    loc_c = {"lat": -6.4, "lng": 107.0, "formatted_address": "SJ-C"}
    o = _role_po(delivery_location=loc_a)
    o["shipments"][0]["delivery_location"] = loc_b
    o["shipments"][1]["delivery_location"] = loc_c
    rows = schedule_rows(o)
    by_id = {r["shipment_id"]: r for r in rows}
    assert by_id["sh-1"]["lat"] == -6.3 and by_id["sh-1"]["lng"] == 106.9
    assert by_id["sh-2"]["lat"] == -6.4 and by_id["sh-2"]["lng"] == 107.0
    assert o["delivery_location"]["lat"] == -6.2
    assert by_id["sh-1"]["qty_sj"] == 5000
    assert by_id["sh-2"]["qty_sj"] == 5000
