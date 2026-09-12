"""Authoritative Tanggal Pengiriman — resolver + delivery schedule behavior."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.tanggal_pengiriman import (
    apply_manual_tanggal_pengiriman,
    automatic_tanggal_pengiriman,
    clear_manual_tanggal_pengiriman,
    effective_tanggal_pengiriman,
    resolve_tanggal_pengiriman,
    wib_calendar_date,
)
from services.delivery_schedule import (
    effective_delivery_date,
    schedule_row,
    schedule_status_code,
)


def _approvals(design: str, payment: str) -> dict:
    return {
        "design_approved_at": f"{design}T10:00:00+07:00",
        "payment_approved_at": f"{payment}T10:00:00+07:00",
        "deadline": "2026-01-01",  # legacy — must not win
    }


def test_design_later_than_payment_uses_design_plus_3():
    o = _approvals("2026-09-16", "2026-09-15")
    assert automatic_tanggal_pengiriman(o) == "2026-09-19"
    assert resolve_tanggal_pengiriman(o)["source"] == "automatic"


def test_payment_later_than_design_uses_payment_plus_3():
    o = _approvals("2026-09-14", "2026-09-15")
    assert automatic_tanggal_pengiriman(o) == "2026-09-18"
    snap = resolve_tanggal_pengiriman(o)
    assert snap["tanggal_pengiriman"] == "2026-09-18"
    assert snap["source"] == "automatic"


def test_manual_overrides_automatic_example_15_to_17():
    """Last approval 15 → auto 18; manual 17 wins for Jadwal Pengiriman."""
    o = _approvals("2026-09-14", "2026-09-15")
    assert automatic_tanggal_pengiriman(o) == "2026-09-18"
    apply_manual_tanggal_pengiriman(o, "2026-09-17")
    snap = resolve_tanggal_pengiriman(o)
    assert snap["tanggal_pengiriman"] == "2026-09-17"
    assert snap["calculated"] == "2026-09-18"
    assert snap["source"] == "manual"
    assert effective_delivery_date(o) == "2026-09-17"
    row = schedule_row(o, today="2026-09-10")
    assert row["effective_delivery_date"] == "2026-09-17"
    assert row["tanggal_pengiriman"] == "2026-09-17"
    assert row["status"] == "terjadwal"
    assert row.get("jadwal_manual") is None
    assert row.get("deadline") is None


def test_manual_not_overwritten_by_later_recalculation():
    o = _approvals("2026-09-14", "2026-09-15")
    apply_manual_tanggal_pengiriman(o, "2026-09-17")
    # Later approval update would change automatic to 20 — manual stays 17.
    o["payment_approved_at"] = "2026-09-17T10:00:00+07:00"
    assert automatic_tanggal_pengiriman(o) == "2026-09-20"
    assert effective_tanggal_pengiriman(o) == "2026-09-17"


def test_changing_manual_moves_jadwal_date():
    o = _approvals("2026-09-14", "2026-09-15")
    apply_manual_tanggal_pengiriman(o, "2026-09-17")
    assert schedule_row(o, today="2026-09-10")["effective_delivery_date"] == "2026-09-17"
    apply_manual_tanggal_pengiriman(o, "2026-09-19")
    row = schedule_row(o, today="2026-09-10")
    assert row["effective_delivery_date"] == "2026-09-19"
    assert o["scheduled_delivery_date"] == "2026-09-19"
    # Old date 17 must not remain as active schedule key.
    assert o["scheduled_delivery_date"] != "2026-09-17"


def test_clear_manual_falls_back_to_automatic():
    o = _approvals("2026-09-14", "2026-09-15")
    apply_manual_tanggal_pengiriman(o, "2026-09-17")
    clear_manual_tanggal_pengiriman(o)
    assert effective_tanggal_pengiriman(o) == "2026-09-18"
    assert resolve_tanggal_pengiriman(o)["source"] == "automatic"


def test_legacy_deadline_does_not_override():
    o = _approvals("2026-09-14", "2026-09-15")
    o["deadline"] = "2026-09-01"
    assert effective_tanggal_pengiriman(o) == "2026-09-18"
    o2 = {"deadline": "2026-09-01"}
    assert effective_tanggal_pengiriman(o2) is None
    assert schedule_status_code(o2, today="2026-09-01") == "belum_dijadwalkan"


def test_incomplete_approvals_do_not_fabricate_date():
    o = {"design_approved_at": "2026-09-15T10:00:00+07:00"}  # payment missing
    assert automatic_tanggal_pengiriman(o) is None
    assert effective_tanggal_pengiriman(o) is None
    o2 = {"payment_approved_at": "2026-09-15T10:00:00+07:00"}
    assert automatic_tanggal_pengiriman(o2) is None


def test_timezone_date_only_manual_does_not_shift():
    o = _approvals("2026-09-14", "2026-09-15")
    apply_manual_tanggal_pengiriman(o, "2026-09-17")
    assert wib_calendar_date(o["delivery_deadline_manual"]) == "2026-09-17"
    assert effective_tanggal_pengiriman(o) == "2026-09-17"


def test_hari_ini_status_from_authoritative_date():
    o = _approvals("2026-09-14", "2026-09-15")
    apply_manual_tanggal_pengiriman(o, "2026-09-20")
    assert schedule_status_code(o, today="2026-09-20") == "hari_ini"
    assert schedule_status_code(o, today="2026-09-21") == "terlambat"
    assert schedule_status_code(o, today="2026-09-19") == "terjadwal"
