"""Guards for ATS YGY 357 RO onefile + pointer install package (no live Mongo)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
from datetime import datetime, timezone
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


def _extract_ro_python() -> str:
    text = RO_SCRIPT.read_text(encoding="utf-8")
    start = "cat > \"$WORK_DIR/ro_ats_ygy_357_live_preview.py\" <<'__ETERNAL_ATS_YGY_357_PY_V1__'\n"
    end = "\n__ETERNAL_ATS_YGY_357_PY_V1__\n"
    i = text.find(start)
    assert i >= 0, "RO python open heredoc missing"
    j = text.find(end, i + len(start))
    assert j > i, "RO python close heredoc missing"
    return text[i + len(start) : j]


def _load_ro_helpers():
    """Load cutoff/txn helpers from embedded RO python without importing PR#730 services."""
    body = _extract_ro_python()
    # Drop shebang; keep __future__ first, then stub third-party imports offline.
    lines = body.splitlines(True)
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    body = "".join(lines)
    stub = (
        "import sys, types\n"
        "sys.modules.setdefault('dotenv', types.ModuleType('dotenv'))\n"
        "sys.modules['dotenv'].dotenv_values = lambda *a, **k: {}\n"
        "motor = types.ModuleType('motor')\n"
        "motor_asyncio = types.ModuleType('motor.motor_asyncio')\n"
        "class _C: pass\n"
        "motor_asyncio.AsyncIOMotorClient = _C\n"
        "sys.modules['motor'] = motor\n"
        "sys.modules['motor.motor_asyncio'] = motor_asyncio\n"
        "pymongo = types.ModuleType('pymongo')\n"
        "class _RP: PRIMARY = 'primary'\n"
        "pymongo.ReadPreference = _RP\n"
        "sys.modules['pymongo'] = pymongo\n"
    )
    # Insert stub immediately after any leading __future__ import block.
    out_lines = []
    inserted = False
    for ln in body.splitlines(True):
        out_lines.append(ln)
        if (not inserted) and ln.startswith("from __future__"):
            out_lines.append(stub)
            inserted = True
    if not inserted:
        out_lines.insert(0, stub)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write("".join(out_lines))
        path = fh.name
    spec = importlib.util.spec_from_file_location("ro_ats_ygy_357_live_preview_embedded", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_ro_onefile_embeds_357_and_refuses_synthetic_policy():
    text = RO_SCRIPT.read_text(encoding="utf-8")
    assert "READ-ONLY" in text
    assert "2026-11-04T22:45:00+07:00" in text
    assert "100463" in text and "100464" in text and "100488" in text and "100489" in text
    assert "SYNTHETIC_ID_REFUSED" in text
    assert "OWNER_STATED_FILE_EQUALS_CUTOFF_UNVERIFIED_BY_AGENT" in text
    assert "BASELINE_ONLY" in text
    assert "NOT claiming BASELINE_ONLY" in text or "NOT claiming BASELINE_ONLY as current" in text
    assert "PHYSICAL_WRITE=False" in text
    assert "/etc/eternal-packaging/backend.env" in text
    assert "INLINE_RO_V1_NO_RUNTIME_IMPORT" in text
    assert "DEPENDS_ON_PR730_RUNTIME=False" in text
    # Must not hardcode the wrong UTC day
    assert "2026-11-03T15:45:00Z" not in text
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
    for kode in ("100463", "100464", "100488", "100489"):
        row = next(r for r in rows if r["kode"] == kode)
        assert "master_item_id" not in row or not row.get("master_item_id")


def test_ro_self_contained_no_pr730_service_imports():
    import re

    body = _extract_ro_python()
    # Docstrings may mention services.*; only real imports are forbidden.
    assert re.search(r"(?m)^\s*from\s+services\.", body) is None
    assert re.search(r"(?m)^\s*import\s+services\b", body) is None
    assert "stock_ats_baseline_store" not in body
    assert "stock_ats_ygy_runtime" not in body
    assert "list_physical_stock_catalog" not in body
    assert "cutoff_utc_z" in body
    assert "txn_at_or_after_cutoff" in body


def test_cutoff_at_utc_equality_derived():
    mod = _load_ro_helpers()
    assert mod.CUTOFF_AT == "2026-11-04T22:45:00+07:00"
    # Derived UTC must be 2026-11-04T15:45:00Z (NOT 2026-11-03)
    assert mod.cutoff_utc_z(mod.CUTOFF_AT) == "2026-11-04T15:45:00Z"
    assert mod.CUTOFF_UTC == "2026-11-04T15:45:00Z"
    assert mod.CUTOFF_UTC == mod.cutoff_utc_z(mod.CUTOFF_AT)
    mod.assert_cutoff_pair_ok()
    # Round-trip: UTC Z +07h == CUTOFF_AT local wall
    utc = datetime.fromisoformat(mod.CUTOFF_UTC.replace("Z", "+00:00"))
    local = datetime.fromisoformat(mod.CUTOFF_AT)
    assert utc.astimezone(local.tzinfo) == local


def test_transaction_boundary_at_cutoff():
    mod = _load_ro_helpers()
    cutoff = mod._parse_dt(mod.CUTOFF_AT)
    # Inclusive lower bound (>= cutoff)
    assert mod.txn_at_or_after_cutoff("2026-11-04T22:45:00+07:00", cutoff) is True
    assert mod.txn_at_or_after_cutoff("2026-11-04T15:45:00Z", cutoff) is True
    assert mod.txn_at_or_after_cutoff("2026-11-04T22:45:01+07:00", cutoff) is True
    assert mod.txn_at_or_after_cutoff("2026-11-04T15:45:01Z", cutoff) is True
    # Before cutoff excluded
    assert mod.txn_at_or_after_cutoff("2026-11-04T22:44:59+07:00", cutoff) is False
    assert mod.txn_at_or_after_cutoff("2026-11-04T15:44:59Z", cutoff) is False
    assert mod.txn_before_cutoff("2026-11-04T22:44:59+07:00", cutoff) is True
    assert mod.txn_before_cutoff("2026-11-04T15:44:59Z", cutoff) is True
    assert mod.txn_before_cutoff("2026-11-04T22:45:00+07:00", cutoff) is False
    # Naive UTC treated as UTC
    assert mod.txn_at_or_after_cutoff(datetime(2026, 11, 4, 15, 45, 0), cutoff) is True
    assert mod.txn_at_or_after_cutoff(
        datetime(2026, 11, 4, 15, 44, 59, tzinfo=timezone.utc), cutoff
    ) is False


def test_install_pointer_script_no_reactivate_has_rollback():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "POINTER" in text
    assert "rollback" in text
    assert "ygy_main_2026-11-02T232200+0700_draft_162" in text
    assert "EXPECT_100032" in text or "9000" in text
    assert "ETERNAL_ATS_YGY_MODE" in text
    assert "does NOT" in text or "Do NOT" in text or "unchanged" in text
    assert "rewrite_ats_mode_file" not in text
    assert "physical_stock_balances" in text.lower() or "physical_stock_balances" in text
    assert "OWNER_ACK_RO_EVIDENCE_AND_SNAPSHOT" in text
    assert "matched_only" in text
    assert "MATCHED_321" in text or "matched-321" in text.lower() or "321" in text


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
