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


def test_registry_points_at_archived_nov2_and_loads():
    clear_baseline_cache()
    reg = load_registry()
    assert reg["warehouse_id"] == YGY_WAREHOUSE_ID
    assert reg["active_baseline_id"]
    man, path = load_active_baseline_manifest()
    assert path.is_file()
    assert man["cutoff_at"] == "2026-11-02T23:22:00+07:00"
    assert man["warehouse_id"] == YGY_WAREHOUSE_ID
    # 100032 free-stock sample
    hit = next(r for r in man["rows"] if r.get("kode") == "100032")
    assert float(hit["accurate_ats"]) == 20000.0
    assert runtime.active_cutoff_at() == man["cutoff_at"]


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
