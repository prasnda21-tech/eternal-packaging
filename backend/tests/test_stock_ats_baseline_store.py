"""Versioned ATS baseline store — free-stock semantics, no physical writes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.stock_ats_baseline_store import (
    YGY_WAREHOUSE_ID,
    build_baseline_document,
    clear_baseline_cache,
    load_active_baseline_manifest,
    load_registry,
    rollback_registry_to,
    write_new_baseline_version,
)
from services.stock_ats_cutover import CutoverError
from services import stock_ats_ygy_runtime as runtime


def test_registry_loads_active_free_stock_baseline():
    clear_baseline_cache()
    reg = load_registry()
    assert reg["warehouse_id"] == YGY_WAREHOUSE_ID
    assert reg["active_baseline_id"]
    # Active pointer stays on Nov-2 until Owner-approved 357 install.
    assert "2026-11-02" in str(reg["active_baseline_id"])
    hist_ids = {h.get("baseline_id") for h in (reg.get("history") or [])}
    assert any("2026-11-02" in str(x) for x in hist_ids)
    assert any("2026-11-04" in str(x) for x in hist_ids)
    assert "2026-11-04" in str(reg.get("candidate_baseline_id") or "")
    man, path = load_active_baseline_manifest()
    assert path.is_file()
    assert man["warehouse_id"] == YGY_WAREHOUSE_ID
    assert man["cutoff_at"] == "2026-11-02T23:22:00+07:00"
    assert len(man.get("rows") or []) == 162
    # Active Nov-2 sample (pre-recutover)
    hit = next(r for r in man["rows"] if r.get("kode") == "100032")
    assert float(hit["accurate_ats"]) == 20000.0
    assert runtime.active_cutoff_at() == man["cutoff_at"]

    # Candidate 357 (not active): free-stock Owner CSV @ 2026-11-04T22:45+07
    cand_rel = str(reg.get("candidate_relpath") or "")
    cand_path = Path(__file__).resolve().parents[1] / "data" / "ats_baselines" / cand_rel
    cand = json.loads(cand_path.read_text(encoding="utf-8"))
    assert cand["cutoff_at"] == "2026-11-04T22:45:00+07:00"
    assert len(cand.get("rows") or []) == 357
    c32 = next(r for r in cand["rows"] if r.get("kode") == "100032")
    assert float(c32["accurate_ats"]) == 9000.0
    neg = next(r for r in cand["rows"] if r.get("kode") == "100001")
    assert float(neg["accurate_ats"]) == -4000.0
    roll = next(r for r in cand["rows"] if r.get("kode") == "100145")
    assert roll.get("satuan") == "ROLL"
    # Four codes must NOT carry synthetic IDs — pending live RO match.
    for kode in ("100463", "100464", "100488", "100489"):
        row = next(r for r in cand["rows"] if r.get("kode") == kode)
        mid = str(row.get("master_item_id") or "")
        assert mid == ""
        assert row.get("eligible_master") is False
        assert row.get("match_status") == "PENDING_LIVE_MASTER"


def test_build_and_write_new_version_without_overwrite(tmp_path, monkeypatch):
    clear_baseline_cache()
    # Redirect store dir into tmp by monkeypatching module constants carefully
    import services.stock_ats_baseline_store as store

    monkeypatch.setattr(store, "_BASELINE_DIR", tmp_path)
    (tmp_path / "archive").mkdir()
    # Seed minimal registry
    seed = {
        "schema": "eternal.ats_ygy_baseline_registry.v1",
        "warehouse_id": YGY_WAREHOUSE_ID,
        "warehouse_code": "YGY-MAIN",
        "active_baseline_id": "old",
        "active_relpath": "archive/old.json",
        "history": [{
            "baseline_id": "old",
            "cutoff_at": "2026-11-02T23:22:00+07:00",
            "relpath": "archive/old.json",
            "source_sha256": "a" * 64,
            "row_count": 1,
        }],
    }
    old_doc = {
        "status": "ARCHIVED",
        "warehouse_id": YGY_WAREHOUSE_ID,
        "warehouse_code": "YGY-MAIN",
        "cutoff_at": "2026-11-02T23:22:00+07:00",
        "source_sha256": "a" * 64,
        "source_meaning": "free blank goods",
        "rows": [{
            "kode": "100032",
            "nama": "x",
            "satuan": "PCS",
            "accurate_ats": 20000,
            "master_item_id": "m1",
            "eligible_master": True,
        }],
    }
    (tmp_path / "archive" / "old.json").write_text(json.dumps(old_doc), encoding="utf-8")
    (tmp_path / "registry.json").write_text(json.dumps(seed), encoding="utf-8")

    rows = [{
        "kode": "100032",
        "nama": "12 oz",
        "satuan": "PCS",
        "accurate_ats": 19000,
        "master_item_id": "m1",
        "eligible_master": True,
    }]
    doc = build_baseline_document(
        cutoff_at="2026-11-04T22:15:00+07:00",
        rows=rows,
        source_file_name="stok.xlsx",
        source_file_sha256="b" * 64,
        inactive_report=[{"kode": "999", "reason": "INACTIVE"}],
        unmatched_report=[{"kode": "888", "reason": "NO_MASTER_ITEM"}],
    )
    assert doc["baseline_semantics"] == "FREE_STOCK_AT_CUTOFF"
    assert doc["open_po_policy"] == "ALREADY_NETTED_IN_FILE_DO_NOT_RE_SUBTRACT"
    result = write_new_baseline_version(doc, activate=True, baseline_id="ygy_main_tonight_test")
    assert Path(result["path"]).is_file()
    # Refuse overwrite
    with pytest.raises(CutoverError):
        write_new_baseline_version(doc, activate=False, baseline_id="ygy_main_tonight_test")
    # Rollback to old
    rb = rollback_registry_to("old")
    assert rb["active_baseline_id"] == "old"
    man, _ = load_active_baseline_manifest()
    assert man["cutoff_at"] == "2026-11-02T23:22:00+07:00"
