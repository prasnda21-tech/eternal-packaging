#!/usr/bin/env python3
"""Local SSI preview for YGY-MAIN ATS cutover (draft_read) — no production writes.

Seeds a throwaway Mongo DB from the Owner baseline draft (162 rows), optionally
loads post-cutoff orders/ledger from a RO dump path, then calls the real
list_physical_stock_catalog entrypoint (same path as list/detail/export).

Does NOT set production ETERNAL_ATS_YGY_MODE and does NOT touch
physical_stock_balances on any live DB.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure backend root is importable when run as a script.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
os.environ["DB_NAME"] = os.environ.get(
    "ATS_PREVIEW_DB_NAME", "eternal_packaging_preview_ats_ygy_local"
)
os.environ["ETERNAL_ATS_YGY_MODE"] = "draft_read"

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.mode_b_legacy_demand import configure_mode_b_legacy_demand  # noqa: E402
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_ats_ygy_runtime import (  # noqa: E402
    CUTOFF_AT,
    YGY_BRANCH_ID,
    YGY_WAREHOUSE_CODE,
    YGY_WAREHOUSE_ID,
    load_baseline_manifest,
)
from services.warehouse_stock_standalone import (  # noqa: E402
    set_warehouse_stock_standalone_enabled,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _seed_baseline(db, manifest: dict) -> list[str]:
    await db[BRANCHES_COLLECTION].delete_many({})
    await db[WAREHOUSES_COLLECTION].delete_many({})
    await db[MASTER_ITEMS_COLLECTION].delete_many({})
    await db[BALANCE_COLLECTION].delete_many({})
    await db[LEDGER_COLLECTION].delete_many({})
    await db.orders.delete_many({})

    await db[BRANCHES_COLLECTION].insert_one({
        "id": YGY_BRANCH_ID,
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    })
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": YGY_WAREHOUSE_ID,
        "branch_id": YGY_BRANCH_ID,
        "code": YGY_WAREHOUSE_CODE,
        "name": "YGY Main",
        "active": True,
        "is_operational": True,
        "is_default": True,
    })
    mids: list[str] = []
    for row in manifest.get("rows") or []:
        mid = str(row.get("master_item_id") or "").strip()
        if not mid:
            continue
        mids.append(mid)
        await db[MASTER_ITEMS_COLLECTION].insert_one({
            "id": mid,
            "item_code": str(row.get("kode") or ""),
            "item_name": str(row.get("nama") or ""),
            "unit": str(row.get("satuan") or "PCS"),
            "item_type": "INVENTORY",
            # Preview DB only: keep all 162 visible in SSI catalog. Live masters
            # that are inactive stay inactive — ATS marks them EXCLUDED, never
            # auto-activates production records.
            "is_active": True,
            "ats_eligible_master": bool(row.get("eligible_master")),
        })
        # Physical balance intentionally NOT set to Accurate ATS — ops already
        # running; ATS overlay must not rewrite balances.
        await db[BALANCE_COLLECTION].insert_one({
            "id": f"bal-{uuid.uuid4().hex[:10]}",
            "master_item_id": mid,
            "branch_id": YGY_BRANCH_ID,
            "warehouse_id": YGY_WAREHOUSE_ID,
            "physical_qty": 0,
            "version": 1,
            "updated_at": _now_iso(),
        })
    return mids


async def _load_ro_dump(db, dump_dir: Path) -> dict:
    """Load optional RO orders + ledger JSON dumps (post-cutoff filter applied by collector)."""
    stats = {"orders": 0, "ledger": 0}
    orders_path = dump_dir / "orders.json"
    ledger_path = dump_dir / "physical_stock_ledger.json"
    if orders_path.is_file():
        orders = json.loads(orders_path.read_text())
        if isinstance(orders, list) and orders:
            await db.orders.insert_many(orders)
            stats["orders"] = len(orders)
    if ledger_path.is_file():
        ledger = json.loads(ledger_path.read_text())
        if isinstance(ledger, list) and ledger:
            await db[LEDGER_COLLECTION].insert_many(ledger)
            stats["ledger"] = len(ledger)
    return stats


async def _page_all(db) -> list[dict]:
    page = 1
    limit = 100
    items: list[dict] = []
    while True:
        catalog = await list_physical_stock_catalog(
            db,
            branch_id=YGY_BRANCH_ID,
            warehouse_id=YGY_WAREHOUSE_ID,
            page=page,
            limit=limit,
        )
        batch = catalog.get("items") or []
        items.extend(batch)
        total = int(catalog.get("total") or 0)
        if not batch or len(items) >= total:
            break
        page += 1
    return items


def _row_preview(row: dict) -> dict:
    gaps = row.get("ats_coverage_gaps") or []
    return {
        "kode": row.get("item_code") or row.get("kode"),
        "nama": row.get("item_name") or row.get("nama"),
        "baseline": row.get("ats_baseline"),
        "penambahan": row.get("ats_penambahan"),
        "pengurangan": row.get("ats_pengurangan"),
        "ats_sekarang": row.get("ats_sekarang"),
        "status": row.get("ats_cutover_status"),
        "gap": "; ".join(str(g) for g in gaps) if gaps else "",
        "physical_qty": row.get("physical_qty"),
        "stock_dapat_dijual": row.get("stock_dapat_dijual"),
        "skipped": "; ".join(str(s) for s in (row.get("ats_cutover_skipped") or [])),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default="/opt/cursor/artifacts/ats_ygy_ssi_preview_local",
        help="Directory for TSV/JSON preview artifacts",
    )
    parser.add_argument(
        "--ro-dump-dir",
        default="",
        help="Optional dir with orders.json + physical_stock_ledger.json (RO)",
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="Do not drop the preview DB at the end",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_baseline_manifest()
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    await client.drop_database(db_name)
    await ensure_indexes(db)
    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    await configure_mode_b_legacy_demand(
        db,
        enabled=False,
        org_branch_id=YGY_BRANCH_ID,
        historical_cutoff_at=CUTOFF_AT,
        accurate_live_branch_map={
            "enabled": True,
            "org_branch_id": YGY_BRANCH_ID,
            "accurate_branch_ids": ["50"],
            "allowed_sources": ["accurate_live"],
            "require_accurate_id": True,
        },
    )
    await _seed_baseline(db, manifest)
    ro_stats = {"orders": 0, "ledger": 0}
    if args.ro_dump_dir:
        ro_stats = await _load_ro_dump(db, Path(args.ro_dump_dir))

    items = await _page_all(db)
    # Consistency: detail-style single-code fetch for first READY row
    ready = next((r for r in items if r.get("ats_cutover_status") == "READY"), None)
    consistency = {"checked": False}
    if ready:
        code = ready.get("item_code")
        detail_cat = await list_physical_stock_catalog(
            db,
            branch_id=YGY_BRANCH_ID,
            warehouse_id=YGY_WAREHOUSE_ID,
            q=str(code),
            page=1,
            limit=10,
        )
        detail = next(
            (r for r in (detail_cat.get("items") or []) if r.get("item_code") == code),
            None,
        )
        consistency = {
            "checked": True,
            "kode": code,
            "list_ats": ready.get("ats_sekarang"),
            "detail_ats": detail.get("ats_sekarang") if detail else None,
            "list_baseline": ready.get("ats_baseline"),
            "detail_baseline": detail.get("ats_baseline") if detail else None,
            "agree": bool(
                detail
                and detail.get("ats_sekarang") == ready.get("ats_sekarang")
                and detail.get("ats_baseline") == ready.get("ats_baseline")
                and detail.get("ats_cutover_status") == ready.get("ats_cutover_status")
            ),
        }

    preview_rows = [_row_preview(r) for r in items]
    status_counts: dict[str, int] = {}
    for r in preview_rows:
        status_counts[r["status"] or "?"] = status_counts.get(r["status"] or "?", 0) + 1

    gap_by_code = {
        r["kode"]: r["gap"] for r in preview_rows if r.get("gap")
    }

    summary = {
        "generated_at_utc": _now_iso(),
        "mode": "draft_read",
        "db_name": db_name,
        "cutoff_at": CUTOFF_AT,
        "warehouse": YGY_WAREHOUSE_CODE,
        "baseline_rows": len(manifest.get("rows") or []),
        "catalog_rows": len(preview_rows),
        "status_counts": status_counts,
        "ro_dump_loaded": ro_stats,
        "list_detail_consistency": consistency,
        "note": (
            "Without a post-cutoff RO dump, penambahan/pengurangan stay 0 and "
            "ATS=baseline for READY rows. Live ops after cutoff require Owner RO "
            "orders+ledger; this preview never rewrites physical_stock_balances."
        ),
        "unmapped_or_gap_codes": sorted(gap_by_code.keys()),
    }

    (out_dir / "PREVIEW_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    (out_dir / "PREVIEW_ROWS.json").write_text(
        json.dumps(preview_rows, indent=2, ensure_ascii=False) + "\n"
    )
    tsv_path = out_dir / "PREVIEW_TABLE.tsv"
    fields = [
        "kode",
        "nama",
        "baseline",
        "penambahan",
        "pengurangan",
        "ats_sekarang",
        "status",
        "gap",
        "physical_qty",
        "stock_dapat_dijual",
        "skipped",
    ]
    with tsv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in preview_rows:
            writer.writerow(row)

    if not args.keep_db:
        await client.drop_database(db_name)
    client.close()

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"TSV={tsv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
