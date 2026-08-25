"""Unit tests for append-only workflow entries."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.workflow_entries import append_qty_entry, seed_legacy, sum_entry_qty


USER = {"email": "budi@example.com", "name": "Budi"}


def test_append_keeps_entry_1_and_sums():
    entries, e1, created = append_qty_entry([], qty=5000, stage="operator", user=USER, now="2026-08-26T08:15:00+07:00", action_id="a1")
    assert created and e1["qty"] == 5000
    entries, e2, created2 = append_qty_entry(entries, qty=2000, stage="operator", user=USER, now="2026-08-26T10:20:00+07:00", action_id="a2")
    assert created2 and e2["qty"] == 2000
    assert entries[0]["qty"] == 5000
    assert sum_entry_qty(entries) == 7000


def test_double_submit_same_action_id():
    entries, _, _ = append_qty_entry([], qty=5000, stage="operator", user=USER, now="t1", action_id="same")
    again, existing, created = append_qty_entry(entries, qty=5000, stage="operator", user=USER, now="t2", action_id="same")
    assert created is False
    assert existing["qty"] == 5000
    assert len(again) == 1
    assert sum_entry_qty(again) == 5000


def test_legacy_seed_then_append():
    seeded = seed_legacy([], snapshot_qty=5000, stage="operator", now="t0", by_name="Budi")
    assert sum_entry_qty(seeded) == 5000
    entries, _, _ = append_qty_entry(seeded, qty=2000, stage="operator", user=USER, now="t1", action_id="n1")
    assert entries[0]["qty"] == 5000
    assert sum_entry_qty(entries) == 7000
