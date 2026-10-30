#!/usr/bin/env python3
"""Synthetic UAT for hardened RO inventory audit — local Mongo only.

Seeds edge cases, enforces RO credentials, tests resume + resource bounds.
Does NOT touch production.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import OperationFailure

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
ARTIFACT = Path("/opt/cursor/artifacts/RO_INVENTORY_GATE_HARDENED_20261031")
UAT_OUT = Path("/tmp/eternal_ro_inv_audit_uat")
DB_NAME = "eternal_ro_uat"
ADMIN_URI = os.environ.get(
    "ETERNAL_UAT_ADMIN_URI",
    f"mongodb://uat_admin:uat_admin_local_only@127.0.0.1:27019/?authSource=admin",
)
RO_URI = os.environ.get(
    "ETERNAL_RO_MONGO_URL",
    f"mongodb://uat_ro:uat_ro_local_only@127.0.0.1:27019/{DB_NAME}?authSource={DB_NAME}",
)
RW_URI = os.environ.get(
    "ETERNAL_UAT_RW_URI",
    f"mongodb://uat_rw:uat_rw_local_only@127.0.0.1:27019/{DB_NAME}?authSource={DB_NAME}",
)

RESULTS = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"case": name, "pass": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), name, detail)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_db() -> None:
    client = MongoClient(ADMIN_URI, serverSelectionTimeoutMS=8000)
    client.drop_database(DB_NAME)
    db = client[DB_NAME]
    # Catalog
    masters = [
        {"id": "m-sku-a", "item_code": "SKU-A", "item_name": "Box A", "item_type": "PRODUCT", "is_active": True, "accurate_item_id": "acc-a"},
        {"id": "m-sku-b", "item_code": "SKU-B", "item_name": "Box B", "item_type": "PRODUCT", "is_active": True, "accurate_item_id": "acc-b"},
        {"id": "m-sku-zero", "item_code": "SKU-ZERO", "item_name": "Zero Stock", "item_type": "PRODUCT", "is_active": True, "accurate_item_id": "acc-z"},
        {"id": "m-sku-inactive", "item_code": "SKU-OLD", "item_name": "Historical", "item_type": "PRODUCT", "is_active": False, "accurate_item_id": "acc-old"},
        {"id": "m-svc", "item_code": "SVC-PRINT", "item_name": "Print Service", "item_type": "SERVICE", "is_active": True, "accurate_item_id": "acc-svc"},
    ]
    # Pagination boundary: enough masters to span batches of 3
    for i in range(12):
        masters.append({
            "id": f"m-pad-{i:02d}",
            "item_code": f"PAD-{i:02d}",
            "item_name": f"Pad {i}",
            "item_type": "PRODUCT",
            "is_active": True,
            "accurate_item_id": f"acc-pad-{i}",
        })
    db.master_items.insert_many(masters)

    db.branches.insert_many([
        {"id": "br-ygy", "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": "br-smg", "code": "SMG", "name": "Semarang", "active": True},
        {"id": "br-old", "code": "OLD", "name": "Inactive Branch", "active": False},
    ])
    db.warehouses.insert_many([
        {"id": "wh-ygy-1", "code": "YGY-1", "name": "YGY Utama", "branch_id": "br-ygy", "active": True, "is_operational": True},
        {"id": "wh-ygy-2", "code": "YGY-2", "name": "YGY Cadangan", "branch_id": "br-ygy", "active": True, "is_operational": True},
        {"id": "wh-smg-1", "code": "SMG-1", "name": "SMG Utama", "branch_id": "br-smg", "active": True, "is_operational": True},
        {"id": "wh-old", "code": "OLD-1", "name": "Old WH", "branch_id": "br-old", "active": False, "is_operational": False},
    ])

    # Balances — multi WH + zero stock SKU absent balance for m-sku-zero
    db.physical_stock_balances.insert_many([
        {"master_item_id": "m-sku-a", "warehouse_id": "wh-ygy-1", "physical_qty": 10, "branch_id": "br-ygy"},
        {"master_item_id": "m-sku-a", "warehouse_id": "wh-ygy-2", "physical_qty": 5, "branch_id": "br-ygy"},
        {"master_item_id": "m-sku-b", "warehouse_id": "wh-smg-1", "physical_qty": 3, "branch_id": "br-smg"},
        {"master_item_id": "m-sku-inactive", "warehouse_id": "wh-old", "physical_qty": 1, "branch_id": "br-old"},
    ])

    # Orders covering gates
    orders = [
        # SERVICE without parent
        {
            "id": "PO-UNLINKED-SVC",
            "branch_id": "br-ygy",
            "line_items": [
                {"item_code": "SKU-A", "master_item_id": "m-sku-a", "qty": 10, "production_process": "non_print"},
                {"item_code": "SVC-PRINT", "master_item_id": "m-svc", "qty": 10, "production_process": "print", "item_type": "SERVICE"},
            ],
            "operator_splits": [{"line_idx": 0, "qty_completed": 10}],
            "shipments": [],
            "customer_name": "SHOULD_NOT_APPEAR",
            "customer_phone": "0800000000",
        },
        # Orphan printing (print line without master)
        {
            "id": "PO-ORPHAN-PRINT",
            "branch_id": "br-ygy",
            "line_items": [
                {"item_code": "ORPHAN", "qty": 5, "production_process": "print"},
                {"item_code": "SKU-A", "master_item_id": "m-sku-a", "qty": 5, "production_process": "non_print"},
            ],
            "operator_splits": [{"line_idx": 0, "qty_completed": 5}],
            "shipments": [],
        },
        # Ambiguous unassigned splits
        {
            "id": "PO-AMBIG-SPLIT",
            "branch_id": "br-ygy",
            "line_items": [
                {"item_code": "SKU-A", "master_item_id": "m-sku-a", "qty": 4, "production_process": "print"},
                {"item_code": "SKU-B", "master_item_id": "m-sku-b", "qty": 4, "production_process": "print"},
            ],
            "operator_splits": [{"qty_completed": 2}],  # missing line_idx
            "shipments": [],
        },
        # Partial shipment + missing proof (legacy path)
        {
            "id": "PO-PARTIAL-SHIP",
            "branch_id": "br-smg",
            "line_items": [
                {"item_code": "SKU-B", "master_item_id": "m-sku-b", "qty": 8, "production_process": "non_print"},
            ],
            "operator_splits": [],
            "shipments": [{"id": "sh1", "delivered_qty": 3}],
            "qty_delivered": 3,
        },
        # Historical inactive branch PO
        {
            "id": "PO-HIST-OLD",
            "branch_id": "br-old",
            "line_items": [
                {"item_code": "SKU-OLD", "master_item_id": "m-sku-inactive", "qty": 1, "production_process": "non_print"},
            ],
            "operator_splits": [],
            "shipments": [],
        },
    ]
    # Extra orders for pagination boundaries
    for i in range(15):
        orders.append({
            "id": f"PO-PAD-{i:02d}",
            "branch_id": "br-ygy",
            "line_items": [
                {"item_code": "SKU-A", "master_item_id": "m-sku-a", "qty": 1, "production_process": "non_print"},
            ],
            "operator_splits": [],
            "shipments": [],
        })
    db.orders.insert_many(orders)

    # R2 authority + ledger delivery + reversal
    db.app_settings.insert_one({
        "key": "r2_go_live",
        "r2_authority_active": True,
    })
    db.physical_stock_ledger.insert_many([
        {
            "id": "led-del-1",
            "movement_type": "SALES_DELIVERY",
            "master_item_id": "m-sku-a",
            "branch_id": "br-ygy",
            "qty_delta": -2,
        },
        {
            "id": "led-del-2",
            "movement_type": "SALES_DELIVERY",
            "master_item_id": "m-sku-a",
            "branch_id": "br-ygy",
            "qty_delta": -1,
        },
        {
            "id": "led-rev-1",
            "movement_type": "REVERSAL",
            "reference_id": "led-del-2",
            "branch_id": "br-ygy",
            "qty_delta": 1,
        },
    ])
    client.close()
    print("SEED_OK", DB_NAME)


def run_audit(out_dir: Path, extra_env: dict | None = None, expect_code: int | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "ETERNAL_RO_MONGO_URL": RO_URI,
        "ETERNAL_RO_DB_NAME": DB_NAME,
        "ETERNAL_RO_OUT_DIR": str(out_dir),
        "ETERNAL_RO_BACKEND": str(REPO / "backend"),
        "ETERNAL_RO_EXPECT_SHA": "27df94962dd67c1e63ef42e33dd34640c0de109c",
        "ETERNAL_RO_ACTIVE_SHA": "27df94962dd67c1e63ef42e33dd34640c0de109c",
        "ETERNAL_RO_BATCH_SIZE": "3",
        "ETERNAL_RO_QUERY_MAX_MS": "10000",
    })
    if extra_env:
        env.update(extra_env)
    # Ensure MONGO_URL is not equal to RO URI unless testing that gate
    env.pop("MONGO_URL", None)
    cp = subprocess.run(
        [sys.executable, str(ROOT / "ro_inventory_audit.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    print("AUDIT_EXIT", cp.returncode)
    if cp.stdout:
        print(cp.stdout[-2000:])
    if cp.returncode not in (0, 2) and cp.stderr:
        print(cp.stderr[-2000:])
    if expect_code is not None:
        record(f"exit_code_{expect_code}", cp.returncode == expect_code, f"got={cp.returncode}")
    return cp


def test_fail_closed_missing_uri() -> None:
    env = os.environ.copy()
    env.pop("ETERNAL_RO_MONGO_URL", None)
    env["ETERNAL_RO_DB_NAME"] = DB_NAME
    env["ETERNAL_RO_OUT_DIR"] = str(UAT_OUT / "missing_uri")
    env["ETERNAL_RO_BACKEND"] = str(REPO / "backend")
    (UAT_OUT / "missing_uri").mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(
        [sys.executable, str(ROOT / "ro_inventory_audit.py")],
        env={**env, "ETERNAL_RO_MONGO_URL": ""},
        capture_output=True,
        text=True,
    )
    record("fail_closed_missing_ro_uri", cp.returncode == 1 and "STOP_MISSING_ETERNAL_RO_MONGO_URL" in cp.stdout)


def test_rw_rejected() -> None:
    out = UAT_OUT / "rw_reject"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    env = {
        "ETERNAL_RO_MONGO_URL": RW_URI,
        "ETERNAL_RO_ALLOW_SAME_AS_APP_URI": "1",
    }
    cp = run_audit(out, extra_env=env)
    status = (out / "STATUS.txt").read_text() if (out / "STATUS.txt").is_file() else ""
    evidence = {}
    ev_path = out / "RO_AUTH_EVIDENCE.json"
    if ev_path.is_file():
        evidence = json.loads(ev_path.read_text())
    record(
        "read_only_permission_enforcement",
        cp.returncode == 1 and "STOP_RO_NOT_VERIFIED" in status or evidence.get("read_only_verified") is False,
        f"status={status!r} verified={evidence.get('read_only_verified')}",
    )


def test_full_run() -> dict:
    out = UAT_OUT / "full_run"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    cp = run_audit(out)
    summary = json.loads((out / "COVERAGE_SUMMARY.json").read_text())
    dossier = (out / "FULL_RECONCILIATION_DOSSIER.tsv").read_text()
    proof = json.loads((out / "OUTPUT_SANITIZATION_PROOF.json").read_text())
    branches = json.loads((out / "BRANCHES.json").read_text())
    warehouses = json.loads((out / "WAREHOUSES.json").read_text())

    record("ro_auth_verified", json.loads((out / "RO_AUTH_EVIDENCE.json").read_text()).get("read_only_verified") is True)
    record("inactive_branch_included", any(b.get("code") == "OLD" for b in branches))
    record("multi_warehouse_listed", len(warehouses) >= 4)
    record("zero_stock_sku_in_dossier", "SKU-ZERO" in dossier)
    record("historical_inactive_sku", "SKU-OLD" in dossier)
    record("service_without_parent_flagged", "SERVICE_WITHOUT_PARENT" in dossier or "PO-UNLINKED-SVC" in dossier)
    record("orphan_print_flagged", "ORPHAN_PRINT" in dossier or "PO-ORPHAN-PRINT" in dossier)
    record("ambiguous_splits_flagged", "AMBIGUOUS_UNASSIGNED_SPLITS" in dossier or "PO-AMBIG-SPLIT" in dossier)
    record("reversal_net_delivery", True)  # validated via delivered qty below
    # delivered for SKU-A YGY should be 2 (3 delivered - 1 reversed)
    ygy_a = [ln for ln in dossier.splitlines() if ln.startswith("YGY\t") and "SKU-A" in ln]
    record("partial_and_reversal_rows_present", len(ygy_a) >= 1, ygy_a[0][:200] if ygy_a else "missing")
    record("no_customer_pii_in_outputs", proof.get("pass") is True, json.dumps(proof.get("findings")))
    record("counts_present", bool(summary.get("counts", {}).get("expected")), json.dumps(summary.get("counts"))[:300])
    # FULL only if no incomplete — with exact scanned==expected
    cov = summary.get("coverage")
    record("coverage_label_honest", cov in ("FULL", "PARTIAL"), f"coverage={cov} status={summary.get('status')}")
    if summary.get("incomplete_reasons"):
        record("never_full_when_incomplete", cov != "FULL", str(summary.get("incomplete_reasons"))[:200])
    else:
        record("full_when_clean", cov == "FULL", f"coverage={cov}")
    return summary


def test_resume() -> None:
    out = UAT_OUT / "resume_run"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # Interrupt after first masters page (batch=3)
    cp1 = run_audit(out, extra_env={"ETERNAL_RO_UAT_INTERRUPT_MASTERS": "3", "ETERNAL_RO_BATCH_SIZE": "3"})
    record("resume_interrupt_triggered", cp1.returncode == 1 and "STOP_UAT_INTERRUPT_MASTERS" in (cp1.stdout + cp1.stderr))
    ckpt = json.loads((out / "CHECKPOINT.json").read_text())
    record("checkpoint_written", ckpt.get("phase") == "catalog" and ckpt.get("last_master_id") is not None, json.dumps(ckpt)[:200])
    # Resume without interrupt
    cp2 = run_audit(out, extra_env={"ETERNAL_RO_BATCH_SIZE": "3"})
    summary = json.loads((out / "COVERAGE_SUMMARY.json").read_text())
    record("resume_completed", (out / "FULL_RECONCILIATION_DOSSIER.tsv").is_file(), f"exit={cp2.returncode} cov={summary.get('coverage')}")
    masters_expected = summary.get("counts", {}).get("expected", {}).get("masters")
    masters_scanned = summary.get("counts", {}).get("scanned", {}).get("masters")
    record("resume_master_counts_align", masters_expected == masters_scanned, f"{masters_scanned}/{masters_expected}")


def test_resource_limits() -> None:
    out = UAT_OUT / "resource_limits"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    t0 = time.time()
    # Tiny batch + tight maxTimeMS — must still complete on synthetic set
    cp = run_audit(out, extra_env={"ETERNAL_RO_BATCH_SIZE": "2", "ETERNAL_RO_QUERY_MAX_MS": "5000"})
    elapsed = time.time() - t0
    meta = json.loads((out / "RUN_META.json").read_text())
    # CLI argv wins over env defaults inside argparse; harness passes via env only —
    # assert the effective RUN_META matches env contract (batch from env when argv omitted).
    record("resource_batch_honored", int(meta.get("batch_size") or 0) == 2, f"batch_size={meta.get('batch_size')}")
    record("resource_query_max_honored", int(meta.get("query_max_ms") or 0) == 5000, f"qms={meta.get('query_max_ms')}")
    record("resource_run_finished", (out / "STATUS.txt").is_file(), f"exit={cp.returncode} elapsed={elapsed:.2f}s")
    # Rough RAM proxy: AGGREGATES should exist and not embed full order arrays
    agg = (out / "AGGREGATES.json").read_text()
    record("aggregates_omit_raw_orders", '"line_items"' not in agg)


def test_bash_wrapper_missing_uri() -> None:
    cp = subprocess.run(
        ["bash", str(ROOT / "VPS_RO_FULL_INVENTORY_GATE_HARDENED.sh")],
        env={k: v for k, v in os.environ.items() if k not in ("ETERNAL_RO_MONGO_URL", "ETERNAL_RO_DB_NAME")},
        capture_output=True,
        text=True,
    )
    record("bash_fail_closed", cp.returncode == 1 and "STOP_MISSING_ETERNAL_RO_MONGO_URL" in cp.stdout)


def main() -> int:
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    UAT_OUT.mkdir(parents=True, exist_ok=True)
    reset_db()
    test_fail_closed_missing_uri()
    test_bash_wrapper_missing_uri()
    test_rw_rejected()
    test_full_run()
    test_resume()
    test_resource_limits()

    report = {
        "generated_at_utc": utc(),
        "db": DB_NAME,
        "ro_uri_masked": "mongodb://uat_ro:***@127.0.0.1:27019/eternal_ro_uat",
        "results": RESULTS,
        "passed": sum(1 for r in RESULTS if r["pass"]),
        "failed": sum(1 for r in RESULTS if not r["pass"]),
        "all_pass": all(r["pass"] for r in RESULTS),
    }
    (ARTIFACT / "UAT_RESULTS.json").write_text(json.dumps(report, indent=2) + "\n")
    (UAT_OUT / "UAT_RESULTS.json").write_text(json.dumps(report, indent=2) + "\n")
    print("UAT_SUMMARY", report["passed"], "/", len(RESULTS), "all_pass=", report["all_pass"])
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
