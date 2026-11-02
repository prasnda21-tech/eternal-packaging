"""Dummy-DB / in-process integration for YGY ATS cutover (not production).

Unit projector tests alone are NOT integration proof — this file exercises
baseline load, Mode B branch match, SSI overlay, and Owner scenario numbers.
"""
from __future__ import annotations

import os
import unittest
from decimal import Decimal
from pathlib import Path

from services.stock_ats_cutover import Baseline, CutoverError, Event, Reservation, project_available
from services.stock_ats_ygy_runtime import (
    CUTOFF_AT,
    YGY_BRANCH_ID,
    YGY_WAREHOUSE_ID,
    apply_ats_overlay_to_ssi_row,
    ats_ygy_mode,
    baseline_for_master,
    coverage_gaps_for_live_ro,
    eligible_rows,
    load_baseline_manifest,
    map_business_event,
    mapped_kind,
    order_matches_ygy_mode_b,
    project_master_ats,
)

MID_100032 = "a88fb167-cf11-4d1f-8862-25dae1723053"
AS_OF = "2026-11-03T01:00:00+07:00"
AFTER = "2026-11-03T00:00:00+07:00"


def _ev(kind, qty, seq, key="", time=AFTER, reverse="", mid=MID_100032):
    return Event(
        f"e{seq}",
        YGY_WAREHOUSE_ID,
        mid,
        "PCS",
        time,
        seq,
        kind,
        Decimal(qty),
        key,
        reverse,
    )


class AtsYgyIntegrationTests(unittest.TestCase):
    def setUp(self):
        os.environ["ETERNAL_ATS_YGY_MODE"] = "draft_read"
        self.base = baseline_for_master(MID_100032)
        self.assertIsNotNone(self.base)
        self.assertEqual(str(self.base.available_qty), "20000")
        self.assertEqual(self.base.cutoff_at, CUTOFF_AT)

    def tearDown(self):
        os.environ.pop("ETERNAL_ATS_YGY_MODE", None)

    def test_manifest_162_and_inactive_excluded(self):
        man = load_baseline_manifest()
        self.assertEqual(len(man["rows"]), 162)
        elig = eligible_rows(man)
        self.assertEqual(len(elig), 159)
        # three inactive must not be eligible
        inactive_codes = {"100056", "100111", "100102"}
        for row in man["rows"]:
            if row["kode"] in inactive_codes:
                self.assertFalse(row["eligible_master"])
                self.assertNotIn(row["master_item_id"], elig)

    def test_outside_file_not_zeroed(self):
        r = project_master_ats(
            master_item_id="not-in-file",
            as_of=AS_OF,
            evidence_complete=True,
        )
        self.assertEqual(r["status"], "EXCLUDED")
        self.assertIsNone(r["available_qty"])

    def test_before_cutoff_as_of_fails(self):
        with self.assertRaises(CutoverError):
            project_available(
                self.base,
                reservations_at_cutoff=[],
                events=[],
                evidence_complete=True,
                as_of="2026-11-02T23:00:00+07:00",
            )

    def test_at_cutoff_baseline_unchanged(self):
        # event exactly at cutoff excluded by projector (cutoff < t <= end)
        e = _ev("ISSUE_FREE", 1000, 1, time=CUTOFF_AT)
        p = project_available(
            self.base,
            reservations_at_cutoff=[],
            events=[e],
            evidence_complete=True,
            as_of=AS_OF,
        )
        self.assertEqual(p["available_qty"], "20000")

    def test_old_po_ship_reserved_keeps_20000(self):
        p = project_available(
            self.base,
            reservations_at_cutoff=[Reservation("old-po-line", Decimal(3000))],
            events=[_ev("SHIP_RESERVED", 1000, 1, key="old-po-line")],
            evidence_complete=True,
            as_of=AS_OF,
        )
        self.assertEqual(p["available_qty"], "20000")

    def test_new_po_2000_then_ship_reserved(self):
        events = [
            _ev("RESERVE", 2000, 1, key="new"),
            _ev("SHIP_RESERVED", 1000, 2, key="new"),
        ]
        p = project_available(
            self.base,
            reservations_at_cutoff=[],
            events=events,
            evidence_complete=True,
            as_of=AS_OF,
        )
        self.assertEqual(p["available_qty"], "18000")

    def test_partial_cancel_and_reversal(self):
        events = [
            _ev("RESERVE", 2000, 1, key="new"),
            _ev("SHIP_RESERVED", 500, 2, key="new"),
            _ev("RELEASE", 1500, 3, key="new"),
        ]
        self.assertEqual(
            project_available(
                self.base,
                reservations_at_cutoff=[],
                events=events,
                evidence_complete=True,
                as_of=AS_OF,
            )["available_qty"],
            "19500",
        )
        rev = [
            _ev("RESERVE", 1000, 1, key="new"),
            _ev("SHIP_RESERVED", 1000, 2, key="new"),
            _ev("REVERSE", 0, 3, reverse="e2"),
        ]
        p = project_available(
            self.base,
            reservations_at_cutoff=[],
            events=rev,
            evidence_complete=True,
            as_of=AS_OF,
        )
        self.assertEqual(p["available_qty"], "19000")
        self.assertEqual(p["remaining_reservations"]["new"], "1000")

    def test_receipt_free_issue_idempotent(self):
        events = [_ev("RECEIVE_FREE", 1000, 1), _ev("ISSUE_FREE", 200, 2)]
        self.assertEqual(
            project_available(
                self.base,
                reservations_at_cutoff=[],
                events=events,
                evidence_complete=True,
                as_of=AS_OF,
            )["available_qty"],
            "20800",
        )
        e = _ev("RESERVE", 1000, 1, key="new")
        self.assertEqual(
            project_available(
                self.base,
                reservations_at_cutoff=[],
                events=[e, e],
                evidence_complete=True,
                as_of=AS_OF,
            )["available_qty"],
            "19000",
        )

    def test_unknown_unit_event_and_wrong_warehouse(self):
        with self.assertRaises(CutoverError):
            project_available(
                self.base,
                reservations_at_cutoff=[],
                events=[_ev("PRODUCTION_CONSUMPTION", 1, 1)],
                evidence_complete=True,
                as_of=AS_OF,
            )
        from dataclasses import replace

        e = _ev("RECEIVE_FREE", 1, 1)
        with self.assertRaises(CutoverError):
            project_available(
                self.base,
                reservations_at_cutoff=[],
                events=[replace(e, warehouse_id="OTHER")],
                evidence_complete=True,
                as_of=AS_OF,
            )

    def test_evidence_incomplete_fail_closed(self):
        r = project_master_ats(
            master_item_id=MID_100032,
            as_of=AS_OF,
            evidence_complete=False,
        )
        self.assertEqual(r["status"], "UNAVAILABLE")
        self.assertIsNone(r["available_qty"])

    def test_ssi_overlay_list_detail_export_fields_agree(self):
        row = {
            "master_item_id": MID_100032,
            "item_code": "100032",
            "physical_qty": -19300,
            "stock_dapat_dijual": -50000,
            "qty_siap_jual": -50000,
            "qty_polos": None,
            "tercetak_belum_kirim": None,
        }
        events = [
            _ev("RESERVE", 2000, 1, key="new"),
            _ev("SHIP_RESERVED", 1000, 2, key="new"),
        ]
        out = apply_ats_overlay_to_ssi_row(
            dict(row),
            warehouse_id=YGY_WAREHOUSE_ID,
            as_of=AS_OF,
            events=events,
            evidence_complete=True,
        )
        self.assertEqual(out["ats_cutover_status"], "READY")
        self.assertEqual(out["stock_dapat_dijual"], 18000.0)
        self.assertEqual(out["qty_siap_jual"], out["stock_dapat_dijual"])
        self.assertEqual(out["qty_siap_jual_status"], "READY")
        # physical untouched; polos/tercetak not invented
        self.assertEqual(out["physical_qty"], -19300)
        self.assertIsNone(out["qty_polos"])
        self.assertIsNone(out["tercetak_belum_kirim"])
        self.assertFalse(out.get("physical_stock_verified"))

        # wrong warehouse untouched ATS
        other = apply_ats_overlay_to_ssi_row(
            dict(row),
            warehouse_id="other-wh",
            as_of=AS_OF,
            events=events,
            evidence_complete=True,
        )
        self.assertEqual(other["ats_cutover_status"], "SKIPPED")
        self.assertEqual(other["stock_dapat_dijual"], -50000)

    def test_mode_b_branch_token_50(self):
        self.assertTrue(order_matches_ygy_mode_b({"accurate_branch_id": "50"}))
        self.assertTrue(order_matches_ygy_mode_b({"raw_branch_token": "50"}))
        self.assertTrue(order_matches_ygy_mode_b({"branch_id": YGY_BRANCH_ID}))
        self.assertFalse(order_matches_ygy_mode_b({"branch_id": "other", "accurate_branch_id": "99"}))

    def test_explicit_event_map_and_gaps(self):
        self.assertEqual(mapped_kind("NEW_PO_LINE_RESERVE"), "RESERVE")
        with self.assertRaises(CutoverError):
            mapped_kind("TRANSFER_MYSTERY")
        gaps = coverage_gaps_for_live_ro(
            has_stock_allocations=False,
            event_stream_complete=False,
        )
        self.assertTrue(any("ZERO_STOCK_ALLOCATIONS" in g for g in gaps))
        self.assertTrue(any("ADOPTION_POLICY" in g for g in gaps))

    def test_mode_off_does_not_override(self):
        os.environ["ETERNAL_ATS_YGY_MODE"] = "off"
        row = {
            "master_item_id": MID_100032,
            "stock_dapat_dijual": 1,
            "qty_siap_jual": 1,
        }
        out = apply_ats_overlay_to_ssi_row(
            row,
            warehouse_id=YGY_WAREHOUSE_ID,
            as_of=AS_OF,
            evidence_complete=True,
            events=[_ev("RESERVE", 1, 1, key="x")],
        )
        self.assertEqual(out["ats_cutover_status"], "OFF")
        self.assertEqual(out["stock_dapat_dijual"], 1)


if __name__ == "__main__":
    unittest.main()
