#!/usr/bin/env python3
"""Replay Owner RO dump through PR#730 ATS runtime (isolated throwaway DB).

Builds matched-321 baseline + per-item exception reports from dump match files.
Does NOT switch production registry pointer. Does NOT write physical/ledger on live.
Does NOT invent synthetic master IDs.

Usage:
  python3 replay_ats_ygy_357_ro_dump.py \\
    --dump /path/eternal_ro_ats_ygy_357_20261104T163426Z.tgz \\
    --out-dir /opt/cursor/artifacts/ats_ygy_357_replay_163426Z
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CUTOFF_AT = "2026-11-04T22:45:00+07:00"
CUTOFF_UTC = "2026-11-04T15:45:00Z"
CRITICAL = ("100463", "100464", "100488", "100489")
WH = "b3d0bf36-1140-4eac-b0f7-b5c924ed0d24"


def _extract_dump(dump: Path, work: Path) -> Path:
    if dump.is_dir():
        return dump
    if not dump.is_file():
        raise SystemExit(f"STOP: dump not found: {dump}")
    if dump.suffix == ".tgz" or dump.name.endswith(".tar.gz"):
        with tarfile.open(dump, "r:gz") as tf:
            tf.extractall(work)
        # find directory containing manifest.json / MASTER_MATCH_REPORT.json
        for p in work.rglob("MASTER_MATCH_REPORT.json"):
            return p.parent
        for p in work.rglob("manifest.json"):
            return p.parent
        raise SystemExit("STOP: dump tarball missing MASTER_MATCH_REPORT/manifest")
    raise SystemExit(f"STOP: unsupported dump: {dump}")


def _load_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _sha_rows(rows: list) -> str:
    payload = [
        {
            "kode": r["kode"],
            "satuan": r["satuan"],
            "accurate_ats": float(r["accurate_ats"]),
            "master_item_id": r.get("master_item_id") or "",
            "eligible_master": bool(r.get("eligible_master")),
        }
        for r in sorted(rows, key=lambda x: (x["kode"], x["satuan"]))
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def build_artifacts(dump_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    match_report = _load_json(dump_dir / "MASTER_MATCH_REPORT.json", [])
    missing = _load_json(dump_dir / "MISSING.json", [])
    inactive = _load_json(dump_dir / "INACTIVE.json", [])
    mismatch = _load_json(dump_dir / "MISMATCH.json", [])
    matched_baseline_in = _load_json(dump_dir / "MATCHED_BASELINE.json", {})
    candidates = _load_json(dump_dir / "CANDIDATES_357.json", [])
    if not match_report and matched_baseline_in:
        # synthesize report from baseline rows + unmatched
        match_report = []
        for r in matched_baseline_in.get("rows") or []:
            st = "MATCHED" if r.get("eligible_master") else "INACTIVE"
            match_report.append({
                "kode": r.get("kode"),
                "satuan": r.get("satuan"),
                "nama": r.get("nama"),
                "qty": r.get("accurate_ats"),
                "status": st,
                "master_item_id": r.get("master_item_id") or "",
                "reason": "FROM_MATCHED_BASELINE",
            })

    matched = [m for m in match_report if m.get("status") == "MATCHED"]
    inactive_r = [m for m in match_report if m.get("status") == "INACTIVE"] or list(inactive)
    mismatch_r = [m for m in match_report if m.get("status") == "MISMATCH"] or list(mismatch)
    missing_r = [m for m in match_report if m.get("status") == "MISSING"] or list(missing)

    # Prefer units/qty from candidates / matched baseline
    cand_by_kode = {str(c.get("kode")): c for c in (candidates or []) if isinstance(c, dict)}
    base_rows_in = {
        str(r.get("kode")): r
        for r in (matched_baseline_in.get("rows") or [])
        if isinstance(r, dict)
    }

    install_rows = []
    for m in matched:
        kode = str(m.get("kode") or "")
        mid = str(m.get("master_item_id") or "").strip()
        if not mid or mid.startswith(("synthetic-", "pending-")):
            raise SystemExit(f"STOP: matched row {kode} has empty/synthetic id")
        src = base_rows_in.get(kode) or {}
        cand = cand_by_kode.get(kode) or {}
        satuan = str(m.get("satuan") or src.get("satuan") or cand.get("satuan") or "PCS")
        qty = m.get("qty")
        if qty is None:
            qty = src.get("accurate_ats", cand.get("qty", 0))
        install_rows.append({
            "kode": kode,
            "nama": m.get("nama") or src.get("nama") or cand.get("nama") or "",
            "satuan": satuan,
            "accurate_ats": float(qty),
            "master_item_id": mid,
            "eligible_master": True,
        })

    # Exception tables (do NOT invent READY for these)
    mismatch_table = []
    for m in mismatch_r:
        kode = str(m.get("kode") or "")
        cand = cand_by_kode.get(kode) or {}
        mismatch_table.append({
            "kode": kode,
            "nama": m.get("nama") or cand.get("nama") or "",
            "qty": m.get("qty") if m.get("qty") is not None else cand.get("qty"),
            "satuan_file": m.get("satuan") or cand.get("satuan") or "",
            "satuan_master": m.get("master_unit") or m.get("satuan_master") or "",
            "reason": m.get("reason") or "UNIT_MISMATCH",
            "master_item_id": m.get("master_item_id") or "",
        })

    missing_table = []
    for m in missing_r:
        kode = str(m.get("kode") or "")
        cand = cand_by_kode.get(kode) or {}
        missing_table.append({
            "kode": kode,
            "nama": m.get("nama") or cand.get("nama") or "",
            "qty": m.get("qty") if m.get("qty") is not None else cand.get("qty"),
            "satuan_file": m.get("satuan") or cand.get("satuan") or "",
            "reason": m.get("reason") or "NO_MASTER_ITEM",
        })

    inactive_table = []
    for m in inactive_r:
        kode = str(m.get("kode") or "")
        inactive_table.append({
            "kode": kode,
            "nama": m.get("nama") or "",
            "qty": m.get("qty"),
            "satuan": m.get("satuan") or "",
            "master_item_id": m.get("master_item_id") or "",
            "reason": m.get("reason") or "INACTIVE_OR_NON_INVENTORY",
            "status": "EXCLUDED",
        })

    critical = {}
    for ck in CRITICAL:
        hits = [m for m in match_report if str(m.get("kode")) == ck]
        if hits:
            critical[ck] = hits[0]
        elif any(str(x.get("kode")) == ck for x in missing_table):
            critical[ck] = {"kode": ck, "status": "MISSING", "reason": "NO_MASTER_ITEM"}
        else:
            critical[ck] = {"kode": ck, "status": "UNKNOWN"}

    source_sha = _sha_rows(install_rows)
    baseline_doc = {
        "warehouse_id": WH,
        "warehouse_code": "YGY-MAIN",
        "cutoff_at": CUTOFF_AT,
        "cutoff_at_utc": CUTOFF_UTC,
        "source_date": "2026-11-04",
        "source_file_name": "ATS_YGY_357_20261104.csv",
        "source_file_sha256": "31ec53244b8095044e336225681a17d654babe7c0303aa65aeadb9254ef6bfb3",
        "source_sha256": source_sha,
        "source_meaning": (
            "free blank goods already net of open orders at cutoff; "
            "Owner Accurate Stok Dapat Dijual; do not re-subtract open POs"
        ),
        "baseline_semantics": "FREE_STOCK_AT_CUTOFF",
        "status": "INSTALL_CANDIDATE_MATCHED_ONLY",
        "rows": install_rows,
        "inactive_rows": inactive_table,
        "unmatched_rows": mismatch_table + missing_table,
        "row_count": len(install_rows),
        "eligible_count": len(install_rows),
        "exception_counts": {
            "matched": len(install_rows),
            "inactive": len(inactive_table),
            "mismatch": len(mismatch_table),
            "missing": len(missing_table),
        },
        "critical_codes": critical,
        "install_gate": {
            "do_not_activate_pointer_until_owner_ack": True,
            "requires_runtime_preview_ack": True,
            "matched_only": True,
            "do_not_fake_357_ready": True,
            "no_synthetic_ids": True,
            "inactive_remain_excluded": True,
            "critical_ok_without_100489": all(
                critical[c].get("status") == "MATCHED" for c in ("100463", "100464", "100488")
            ),
            "missing_critical": [
                c for c in CRITICAL if critical.get(c, {}).get("status") != "MATCHED"
            ],
        },
        "rollback_baseline_id": "ygy_main_2026-11-02T232200+0700_draft_162",
        "built_from_ro_dump": str(dump_dir),
    }

    (out_dir / "MATCHED_321_BASELINE.json").write_text(
        json.dumps(baseline_doc, indent=2, ensure_ascii=False) + "\n"
    )
    (out_dir / "EXCEPTIONS_INACTIVE.json").write_text(
        json.dumps(inactive_table, indent=2, ensure_ascii=False) + "\n"
    )
    (out_dir / "EXCEPTIONS_MISMATCH.json").write_text(
        json.dumps(mismatch_table, indent=2, ensure_ascii=False) + "\n"
    )
    (out_dir / "EXCEPTIONS_MISSING.json").write_text(
        json.dumps(missing_table, indent=2, ensure_ascii=False) + "\n"
    )
    (out_dir / "CRITICAL_FOUR.json").write_text(
        json.dumps(critical, indent=2, ensure_ascii=False) + "\n"
    )

    # Owner decision table for 15 mismatches
    fields = ["kode", "nama", "qty", "satuan_file", "satuan_master", "reason", "master_item_id"]
    with (out_dir / "MISMATCH_15_OWNER_TABLE.tsv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for row in mismatch_table:
            w.writerow(row)

    summary = {
        "matched": len(install_rows),
        "inactive": len(inactive_table),
        "mismatch": len(mismatch_table),
        "missing": len(missing_table),
        "critical": {k: v.get("status") for k, v in critical.items()},
        "baseline_path": str(out_dir / "MATCHED_321_BASELINE.json"),
        "source_sha256": source_sha,
        "cutoff_at": CUTOFF_AT,
        "note": (
            "Matched-only install candidate. Exceptions visible separately. "
            "Do not claim 357 READY. Do not switch production pointer without preview ACK."
        ),
    }
    (out_dir / "INSTALL_PACKAGE_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    return summary


async def run_runtime_preview(baseline_path: Path, dump_dir: Path, out_dir: Path) -> dict:
    """Call existing local SSI preview with PR#730 runtime + RO orders/ledger."""
    os.environ["ETERNAL_ATS_YGY_MODE"] = "draft_read"
    os.environ["ETERNAL_ATS_YGY_BASELINE_PATH"] = str(baseline_path)
    os.environ["ATS_PREVIEW_DB_NAME"] = "eternal_packaging_preview_ats_ygy_357_replay"
    # Import after env set
    from scripts.preview_ats_ygy_ssi_local import main as preview_main  # type: ignore

    # preview script uses argparse via __main__; call by constructing argv
    import scripts.preview_ats_ygy_ssi_local as preview_mod

    preview_out = out_dir / "runtime_preview"
    preview_out.mkdir(parents=True, exist_ok=True)
    argv = [
        "preview_ats_ygy_ssi_local.py",
        "--ro-dump-dir",
        str(dump_dir),
        "--out-dir",
        str(preview_out),
    ]
    old = sys.argv
    try:
        sys.argv = argv
        # preview module defines async main behind argparse — invoke main()
        if hasattr(preview_mod, "main"):
            # The module's main is async when run as script; check
            code = await _run_preview_async(preview_mod, dump_dir, preview_out)
        else:
            code = 1
    finally:
        sys.argv = old
    summary_path = preview_out / "PREVIEW_SUMMARY.json"
    rows_path = preview_out / "PREVIEW_ROWS.json"
    summary = _load_json(summary_path, {})
    rows = _load_json(rows_path, [])
    hit = next((r for r in rows if str(r.get("kode")) == "100153"), None)
    proof = {
        "preview_exit": code,
        "preview_class": summary.get("preview_class"),
        "catalog_rows": summary.get("catalog_rows"),
        "list_detail_consistency": summary.get("list_detail_consistency"),
        "code_100153": hit,
        "code_100153_ok": bool(
            hit
            and float(hit.get("baseline") or -1) == 109000.0
            and (
                hit.get("ats_akhir") is None
                or float(hit.get("ats_akhir")) == 109000.0
                or float(hit.get("pengurangan") or 0) == 0.0
            )
        ),
    }
    (out_dir / "RUNTIME_PROOF_100153.json").write_text(
        json.dumps(proof, indent=2, ensure_ascii=False) + "\n"
    )
    return proof


async def _run_preview_async(preview_mod, dump_dir: Path, preview_out: Path) -> int:
    # Re-implement minimal call: the preview script's async main parses args.
    import argparse
    import asyncio

    # Prefer importing and calling the same path as CLI
    parser = argparse.ArgumentParser()
    # Steal by executing module main with patched argv already set
    if asyncio.iscoroutinefunction(getattr(preview_mod, "main", None)):
        return await preview_mod.main()
    # Fallback: run module as __main__ pattern
    return await preview_mod.main() if hasattr(preview_mod, "main") else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dump",
        required=True,
        help="Path to eternal_ro_ats_ygy_357_20261104T163426Z.tgz or extracted dir",
    )
    ap.add_argument(
        "--out-dir",
        default="/opt/cursor/artifacts/ats_ygy_357_replay_163426Z",
    )
    ap.add_argument(
        "--skip-runtime-preview",
        action="store_true",
        help="Only build matched-321 + exception tables (no Mongo preview)",
    )
    args = ap.parse_args()
    dump = Path(args.dump).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    work = Path(tempfile.mkdtemp(prefix="ats_ygy_357_dump_"))
    try:
        dump_dir = _extract_dump(dump, work)
        # copy dump identity
        shutil.copytree(dump_dir, out_dir / "dump_extract", dirs_exist_ok=True)
        summary = build_artifacts(dump_dir, out_dir)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if not args.skip_runtime_preview:
            import asyncio

            # Use preview_ats_ygy_ssi_local via subprocess for isolation
            import subprocess

            env = os.environ.copy()
            env["ETERNAL_ATS_YGY_MODE"] = "draft_read"
            env["ETERNAL_ATS_YGY_BASELINE_PATH"] = str(out_dir / "MATCHED_321_BASELINE.json")
            env["ATS_PREVIEW_DB_NAME"] = "eternal_packaging_preview_ats_ygy_357_replay"
            preview_out = out_dir / "runtime_preview"
            preview_out.mkdir(parents=True, exist_ok=True)
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "preview_ats_ygy_ssi_local.py"),
                "--ro-dump-dir",
                str(dump_dir),
                "--out-dir",
                str(preview_out),
            ]
            r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
            (out_dir / "runtime_preview_stdout.txt").write_text(r.stdout + "\n" + r.stderr)
            rows = _load_json(preview_out / "PREVIEW_ROWS.json", [])
            summary_prev = _load_json(preview_out / "PREVIEW_SUMMARY.json", {}) or {}
            hit = next((x for x in rows if str(x.get("kode")) == "100153"), None)
            hit_032 = next((x for x in rows if str(x.get("kode")) == "100032"), None)
            proof = {
                "preview_rc": r.returncode,
                "preview_class": summary_prev.get("preview_class"),
                "list_detail_export_consistency": summary_prev.get(
                    "list_detail_consistency"
                ),
                "code_100032": hit_032,
                "code_100153": hit,
                "expect_baseline_100153": 109000.0,
                "expect_baseline_100032": 9000.0,
                "no_double_cut": bool(
                    hit
                    and float(hit.get("baseline") or -1) == 109000.0
                    and float(hit.get("pengurangan") or 0) == 0.0
                    and float(hit.get("ats_akhir") or -1) == 109000.0
                    and hit.get("status") == "READY"
                    and "SKIP_OLD_PO_SHIP" in str(hit.get("skipped") or "")
                ),
                "code_100032_ok": bool(
                    hit_032
                    and float(hit_032.get("baseline") or -1) == 9000.0
                    and float(hit_032.get("ats_akhir") or -1) == 9000.0
                    and hit_032.get("status") == "READY"
                ),
            }
            (out_dir / "RUNTIME_PROOF_100153.json").write_text(
                json.dumps(proof, indent=2, ensure_ascii=False) + "\n"
            )
            print(json.dumps({"runtime_proof_100153": proof}, indent=2, ensure_ascii=False))
            if r.returncode != 0:
                return r.returncode
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
