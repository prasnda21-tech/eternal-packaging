"""Guards for ATS YGY 357 RO onefile + pointer install package (no live Mongo)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RO_SCRIPT = ROOT / "scripts" / "ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh"
INSTALL_SCRIPT = ROOT / "scripts" / "VPS_ADMIN_ATS_YGY_INSTALL_357_POINTER.sh"
CANDIDATE = (
    ROOT
    / "backend"
    / "data"
    / "ats_baselines"
    / "ygy_main_2026-11-04T224500+0700_d8bef8b608c8.json"
)
REGISTRY = ROOT / "backend" / "data" / "ats_baselines" / "registry.json"


def test_ro_onefile_embeds_357_and_refuses_synthetic_policy():
    text = RO_SCRIPT.read_text(encoding="utf-8")
    assert "READ-ONLY" in text
    assert "2026-11-04T22:45:00+07:00" in text
    assert "100463" in text and "100464" in text and "100488" in text and "100489" in text
    assert "SYNTHETIC_ID_REFUSED" in text
    assert "OWNER_STATED_FILE_EQUALS_CUTOFF_UNVERIFIED_BY_AGENT" in text
    assert "BASELINE_ONLY" in text
    assert "NOT claiming BASELINE_ONLY" in text or "NOT claiming BASELINE_ONLY as current" in text
    assert "physical_stock_balances" in text.lower() or "PHYSICAL_WRITE=False" in text
    # Embedded candidates JSON
    start = "cat > \"$WORK_DIR/CANDIDATES_357.json\" <<'__ETERNAL_ATS_YGY_357_JSON_V1__'\n"
    end = "\n__ETERNAL_ATS_YGY_357_JSON_V1__\n"
    i = text.find(start)
    assert i >= 0, "CANDIDATES_357 open heredoc missing"
    j = text.find(end, i + len(start))
    assert j > i, "CANDIDATES_357 close heredoc missing"
    rows = json.loads(text[i + len(start) : j])
    assert len(rows) == 357
    hit = next(r for r in rows if r["kode"] == "100032")
    assert str(hit["qty"]) in ("9000", "9000.0")
    # No synthetic master ids embedded for the four critical codes
    for kode in ("100463", "100464", "100488", "100489"):
        row = next(r for r in rows if r["kode"] == kode)
        assert "master_item_id" not in row or not row.get("master_item_id")


def test_install_pointer_script_no_reactivate_has_rollback():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "POINTER" in text
    assert "rollback" in text
    assert "ygy_main_2026-11-02T232200+0700_draft_162" in text
    assert "EXPECT_100032" in text or "9000" in text
    assert "ETERNAL_ATS_YGY_MODE" in text
    # Must not set MODE_VALUE=activated rewrite path as primary install action
    assert "does NOT" in text or "Do NOT" in text or "unchanged" in text
    assert "rewrite_ats_mode_file" not in text
    assert "physical_stock_balances" in text.lower() or "physical_stock_balances" in text
    assert "OWNER_ACK_RO_EVIDENCE_AND_SNAPSHOT" in text


def test_candidate_pending_four_and_registry_not_switched():
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert "2026-11-02" in reg["active_baseline_id"]
    assert "2026-11-04" in (reg.get("candidate_baseline_id") or "")
    cand = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    assert cand["cutoff_at"] == "2026-11-04T22:45:00+07:00"
    assert int(cand.get("pending_live_master_count") or 0) == 4
    assert cand.get("install_gate", {}).get("do_not_activate_pointer_until_owner_ack") is True
    for kode in ("100463", "100464", "100488", "100489"):
        row = next(r for r in cand["rows"] if r["kode"] == kode)
        assert not row.get("master_item_id")
        assert row.get("eligible_master") is False
