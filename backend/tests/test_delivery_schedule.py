"""Delivery schedule + reschedule rules (no live API)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.delivery_schedule import (
    RescheduleError,
    apply_reschedule,
    apply_set_date,
    effective_delivery_date,
    find_reschedule_by_action_id,
    matches_schedule_filters,
    original_deadline,
    public_reschedule_snippet,
    reschedule_rows,
    schedule_row,
    schedule_status_code,
)


def _po(**extra):
    base = {
        "id": "ord-1",
        "po_number": "PO/2026/A",
        "customer": "PT Contoh",
        "deadline": "2026-08-30",
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


def test_effective_uses_deadline_when_no_manual():
    o = _po()
    assert original_deadline(o) == "2026-08-30"
    assert effective_delivery_date(o) == "2026-08-30"


def test_manual_schedule_wins_over_deadline():
    o = _po(delivery_deadline_manual="2026-08-29T00:00:00+07:00")
    assert original_deadline(o) == "2026-08-30"
    assert effective_delivery_date(o) == "2026-08-29"


def test_reschedule_wins_and_preserves_deadline():
    o = _po(deadline="2026-08-30")
    rec, mutated = apply_reschedule(
        o, reason="Hujan", new_date="2026-08-31", can_set_date=True, user=_user("u-admin", "Admin")
    )
    assert mutated is True
    assert rec["status"] == "scheduled"
    assert rec["previous_date"] == "2026-08-30"
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
    assert effective_delivery_date(o) == "2026-08-30"


def test_invalid_date_rejected_when_can_set_date():
    o = _po()
    try:
        apply_reschedule(o, reason="Telat", new_date="31/08/2026", can_set_date=True, user=_user())
        assert False, "expected reject"
    except RescheduleError as e:
        assert "Tanggal" in str(e)
    assert effective_delivery_date(o) == "2026-08-30"


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
    assert effective_delivery_date(o) == "2026-08-30"


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
    assert rec["previous_date"] == "2026-08-30"
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
    assert [(r["previous_date"], r["new_date"]) for r in rows] == [
        ("2026-08-30", "2026-08-31"),
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
    o = _po(deadline="2026-08-27")
    assert schedule_status_code(o, today="2026-08-27") == "hari_ini"
    assert schedule_status_code(o, today="2026-08-28") == "terlambat"
    o2 = _po(deadline="2026-08-30", scheduled_delivery_date="2026-08-31")
    assert schedule_status_code(o2, today="2026-08-27") == "terjadwal"
    o3 = _po(delivery_status="Terkirim")
    assert schedule_status_code(o3, today="2026-08-27") == "terkirim"


def test_filter_and_reset_semantics():
    row = schedule_row(_po(deadline="2026-08-30", customer="PT Contoh"))
    assert matches_schedule_filters(row, {"po": "PO/2026", "customer": "contoh", "status": "belum_dijadwalkan"})
    assert not matches_schedule_filters(row, {"po": "ZZZ"})
    assert not matches_schedule_filters(row, {"status": "terkirim"})
    assert matches_schedule_filters(row, {"date_from": "2026-08-30", "date_to": "2026-08-30"})
    assert not matches_schedule_filters(row, {"date_from": "2026-09-01"})


def test_public_snippet_hides_reason_and_actors():
    o = _po()
    apply_reschedule(o, reason="INTERNAL SECRET", new_date="2026-08-31", can_set_date=True, user=_user())
    snippet = public_reschedule_snippet(o)
    assert snippet == {"scheduled_date": "2026-08-31"}
    assert "reason" not in snippet
    assert "requested_by_id" not in snippet
