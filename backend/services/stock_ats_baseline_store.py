"""Versioned ATS YGY-MAIN baseline store (read-only relative to physical stock).

Baselines are keyed by warehouse + cutoff + source content hash.
Old baselines stay in archive/ for history and rollback — never overwrite.
Does NOT write physical_stock_balances or ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.stock_ats_cutover import CutoverError, quantity, timestamp

YGY_WAREHOUSE_ID = "b3d0bf36-1140-4eac-b0f7-b5c924ed0d24"
YGY_WAREHOUSE_CODE = "YGY-MAIN"

_BASELINE_DIR = Path(__file__).resolve().parents[1] / "data" / "ats_baselines"
_REGISTRY_NAME = "registry.json"
_LEGACY_DEFAULT = Path(__file__).resolve().parents[1] / "data" / "ats_baseline_ygy_main_draft_162.json"

_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "manifest": None, "cutoff": None}


def baselines_dir() -> Path:
    return _BASELINE_DIR


def registry_path() -> Path:
    return _BASELINE_DIR / _REGISTRY_NAME


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry() -> dict:
    path = registry_path()
    if not path.is_file():
        raise CutoverError(f"ATS baseline registry missing: {path}")
    reg = _read_json(path)
    if str(reg.get("warehouse_id") or "") != YGY_WAREHOUSE_ID:
        raise CutoverError("Registry warehouse_id must be YGY-MAIN")
    return reg


def resolve_active_baseline_path(explicit: Optional[Path] = None) -> Path:
    """Resolve active baseline file.

    Priority:
    1. explicit path argument
    2. env ETERNAL_ATS_YGY_BASELINE_PATH
    3. registry active_relpath under ats_baselines/
    4. legacy draft file (compat)
    """
    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise CutoverError(f"Baseline path not found: {p}")
        return p
    env = str(os.environ.get("ETERNAL_ATS_YGY_BASELINE_PATH") or "").strip()
    if env:
        p = Path(env)
        if not p.is_file():
            raise CutoverError(f"ETERNAL_ATS_YGY_BASELINE_PATH not found: {p}")
        return p
    if registry_path().is_file():
        reg = load_registry()
        rel = str(reg.get("active_relpath") or "").strip()
        if not rel:
            raise CutoverError("Registry active_relpath empty")
        p = (_BASELINE_DIR / rel).resolve()
        # Refuse path escape outside baselines dir
        if _BASELINE_DIR.resolve() not in p.parents and p != _BASELINE_DIR.resolve():
            raise CutoverError("Active baseline path escapes ats_baselines/")
        if not p.is_file():
            raise CutoverError(f"Active baseline missing: {p}")
        return p
    if _LEGACY_DEFAULT.is_file():
        return _LEGACY_DEFAULT
    raise CutoverError("No ATS baseline configured")


def validate_baseline_manifest(raw: dict, *, expect_cutoff: Optional[str] = None) -> dict:
    if str(raw.get("warehouse_id") or "") != YGY_WAREHOUSE_ID:
        raise CutoverError("Baseline warehouse_id must be YGY-MAIN")
    if str(raw.get("warehouse_code") or "") not in ("", YGY_WAREHOUSE_CODE):
        # Allow missing code; if present must match.
        if str(raw.get("warehouse_code")) != YGY_WAREHOUSE_CODE:
            raise CutoverError("Baseline warehouse_code must be YGY-MAIN")
    cutoff = str(raw.get("cutoff_at") or "").strip()
    if not cutoff:
        raise CutoverError("Baseline cutoff_at required")
    timestamp(cutoff)  # timezone-aware ISO
    if expect_cutoff is not None and cutoff != expect_cutoff:
        raise CutoverError(
            f"Baseline cutoff_at {cutoff!r} != expected {expect_cutoff!r}"
        )
    sha = str(raw.get("source_sha256") or "").strip().lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise CutoverError("Baseline source_sha256 required")
    rows = raw.get("rows")
    if not isinstance(rows, list) or not rows:
        raise CutoverError("Baseline rows required")
    seen_kode: set = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CutoverError(f"Baseline row {idx} invalid")
        kode = str(row.get("kode") or "").strip()
        satuan = str(row.get("satuan") or "").strip()
        if not kode or not satuan:
            raise CutoverError(f"Baseline row {idx} requires kode+satuan")
        key = f"{kode}|{satuan}"
        if key in seen_kode:
            raise CutoverError(f"Duplicate kode+satuan in baseline: {key}")
        seen_kode.add(key)
        # Preserve zeros and negatives from Owner Accurate free-stock export.
        quantity(row.get("accurate_ats"))
    meaning = str(raw.get("source_meaning") or "")
    if "free" not in meaning.lower() and "stok bebas" not in meaning.lower():
        # Soft marker — still accept Owner files that set free-stock meaning explicitly later
        raw.setdefault(
            "source_meaning_warning",
            "expected free-stock meaning (orders already netted at cutoff)",
        )
    return raw


def load_active_baseline_manifest(
    path: Optional[Path] = None,
    *,
    expect_cutoff: Optional[str] = None,
) -> Tuple[dict, Path]:
    p = resolve_active_baseline_path(path)
    mtime = p.stat().st_mtime
    if (
        _CACHE["manifest"] is not None
        and _CACHE["path"] == str(p)
        and _CACHE["mtime"] == mtime
        and expect_cutoff is None
    ):
        return _CACHE["manifest"], p
    raw = validate_baseline_manifest(_read_json(p), expect_cutoff=expect_cutoff)
    _CACHE["path"] = str(p)
    _CACHE["mtime"] = mtime
    _CACHE["manifest"] = raw
    _CACHE["cutoff"] = str(raw.get("cutoff_at"))
    return raw, p


def active_cutoff_at(path: Optional[Path] = None) -> str:
    man, _ = load_active_baseline_manifest(path)
    return str(man.get("cutoff_at"))


def clear_baseline_cache() -> None:
    _CACHE["path"] = None
    _CACHE["mtime"] = None
    _CACHE["manifest"] = None
    _CACHE["cutoff"] = None


def _slug_cutoff(cutoff_at: str) -> str:
    # 2026-11-04T22:15:00+07:00 → 2026-11-04T221500+0700
    dt = timestamp(cutoff_at)
    off = dt.strftime("%z") or "+0000"
    return dt.strftime("%Y-%m-%dT%H%M%S") + off


def compute_rows_content_sha256(rows: List[dict], *, cutoff_at: str, warehouse_id: str) -> str:
    """Stable hash over identity fields Owner cares about."""
    payload = {
        "warehouse_id": warehouse_id,
        "cutoff_at": cutoff_at,
        "rows": sorted(
            [
                {
                    "kode": str(r.get("kode") or "").strip(),
                    "satuan": str(r.get("satuan") or "").strip(),
                    "accurate_ats": str(quantity(r.get("accurate_ats"))),
                    "master_item_id": str(r.get("master_item_id") or "").strip(),
                    "eligible_master": bool(r.get("eligible_master")),
                }
                for r in rows
                if isinstance(r, dict)
            ],
            key=lambda x: (x["kode"], x["satuan"]),
        ),
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_baseline_document(
    *,
    cutoff_at: str,
    rows: List[dict],
    source_file_name: str,
    source_file_sha256: str,
    status: str = "DRAFT_NOT_ACTIVATED",
    source_meaning: str = (
        "free blank goods already net of open orders at cutoff; "
        "Owner Accurate Stok Dapat Dijual; do not re-subtract open POs"
    ),
    inactive_report: Optional[List[dict]] = None,
    unmatched_report: Optional[List[dict]] = None,
) -> dict:
    timestamp(cutoff_at)
    content_sha = compute_rows_content_sha256(
        rows, cutoff_at=cutoff_at, warehouse_id=YGY_WAREHOUSE_ID
    )
    doc = {
        "status": status,
        "warehouse_id": YGY_WAREHOUSE_ID,
        "warehouse_code": YGY_WAREHOUSE_CODE,
        "source_date": str(timestamp(cutoff_at).date()),
        "cutoff_at": cutoff_at,
        "cutoff_at_utc": timestamp(cutoff_at).astimezone(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_sha256": content_sha,
        "source_file_name": source_file_name,
        "source_file_sha256": source_file_sha256,
        "source_meaning": source_meaning,
        "baseline_semantics": "FREE_STOCK_AT_CUTOFF",
        "open_po_policy": "ALREADY_NETTED_IN_FILE_DO_NOT_RE_SUBTRACT",
        "outside_file_policy": "DO_NOT_ZERO",
        "inactive_policy": "REPORT_SEPARATELY_EXCLUDED",
        "rows": rows,
        "inactive_rows": list(inactive_report or []),
        "unmatched_rows": list(unmatched_report or []),
        "row_count": len(rows),
        "built_at": datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return validate_baseline_manifest(doc)


def write_new_baseline_version(
    doc: dict,
    *,
    activate: bool = False,
    baseline_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a new versioned baseline under ats_baselines/ (never overwrite archive).

    activate=False keeps registry on previous active (preview/install dry-run).
    """
    cutoff = str(doc["cutoff_at"])
    bid = baseline_id or f"ygy_main_{_slug_cutoff(cutoff)}_{doc['source_sha256'][:12]}"
    if not re.fullmatch(r"[A-Za-z0-9._+\-]+", bid):
        raise CutoverError("invalid baseline_id")
    out_dir = _BASELINE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "archive").mkdir(parents=True, exist_ok=True)
    rel = f"{bid}.json"
    out = out_dir / rel
    if out.exists():
        raise CutoverError(f"Baseline version already exists (refuse overwrite): {out}")
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out.chmod(0o600)

    reg_path = registry_path()
    if reg_path.is_file():
        reg = load_registry()
    else:
        reg = {
            "schema": "eternal.ats_ygy_baseline_registry.v1",
            "warehouse_id": YGY_WAREHOUSE_ID,
            "warehouse_code": YGY_WAREHOUSE_CODE,
            "active_baseline_id": None,
            "active_relpath": None,
            "history": [],
        }
    entry = {
        "baseline_id": bid,
        "cutoff_at": cutoff,
        "relpath": rel,
        "source_sha256": doc["source_sha256"],
        "row_count": len(doc.get("rows") or []),
        "status": doc.get("status"),
        "source_meaning": doc.get("source_meaning"),
    }
    # Keep prior active in history if activating a new one
    hist = list(reg.get("history") or [])
    hist_ids = {h.get("baseline_id") for h in hist}
    if bid not in hist_ids:
        hist.insert(0, entry)
    if activate:
        prev_id = reg.get("active_baseline_id")
        prev_rel = reg.get("active_relpath")
        if prev_id and prev_rel and prev_id != bid:
            # Move previous into archive/ if it lives at top-level
            prev_path = out_dir / str(prev_rel)
            if prev_path.is_file() and not str(prev_rel).startswith("archive/"):
                arch_rel = f"archive/{Path(prev_rel).name}"
                arch_path = out_dir / arch_rel
                if not arch_path.exists():
                    arch_path.write_bytes(prev_path.read_bytes())
                    arch_path.chmod(0o600)
                for h in hist:
                    if h.get("baseline_id") == prev_id:
                        h["relpath"] = arch_rel
                        h["archived_reason"] = f"superseded_by:{bid}"
        reg["active_baseline_id"] = bid
        reg["active_relpath"] = rel
    reg["history"] = hist
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    clear_baseline_cache()
    return {
        "baseline_id": bid,
        "path": str(out),
        "activated": bool(activate),
        "active_baseline_id": reg.get("active_baseline_id"),
        "registry": str(reg_path),
    }


def rollback_registry_to(baseline_id: str) -> Dict[str, Any]:
    """Point active registry at an archived/history baseline (no file deletes)."""
    reg = load_registry()
    hit = None
    for h in reg.get("history") or []:
        if h.get("baseline_id") == baseline_id:
            hit = h
            break
    if not hit:
        raise CutoverError(f"baseline_id not in history: {baseline_id}")
    rel = str(hit.get("relpath") or "")
    p = (_BASELINE_DIR / rel).resolve()
    if not p.is_file():
        raise CutoverError(f"history baseline file missing: {p}")
    validate_baseline_manifest(_read_json(p))
    reg["active_baseline_id"] = baseline_id
    reg["active_relpath"] = rel
    registry_path().write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    clear_baseline_cache()
    return {"active_baseline_id": baseline_id, "active_relpath": rel, "path": str(p)}
