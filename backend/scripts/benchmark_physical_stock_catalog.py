#!/usr/bin/env python3
"""Local non-production benchmark for GET /physical-stock catalog path.

Seeds a representative synthetic dataset (~400 inventory masters) and reports:
- wall latency for list_physical_stock_catalog
- Mongo find/to_list call counts by collection
- row build scope (masters computed vs page size)

Usage (from backend/):
  python scripts/benchmark_physical_stock_catalog.py --label before
  python scripts/benchmark_physical_stock_catalog.py --label after
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
os.environ["DB_NAME"] = "eternal_packaging_bench_ssi_catalog_perf"

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.warehouse_stock_standalone import (  # noqa: E402
    set_warehouse_stock_standalone_enabled,
)
import server  # noqa: E402

TEST_DB = os.environ["DB_NAME"]
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

N_MASTERS = 387  # production catalog band from SSI post-deploy audit
PAGE_LIMIT = 50
ARTIFACT = Path("/opt/cursor/artifacts/ssi_catalog_perf_benchmark.json")


class CountingCollection:
    def __init__(self, coll, name: str, counts: Dict[str, int]):
        self._coll = coll
        self._name = name
        self._counts = counts

    def find(self, *args, **kwargs):
        self._counts[self._name] = self._counts.get(self._name, 0) + 1
        return self._coll.find(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._coll, item)


class CountingDB:
    def __init__(self, db):
        self._db = db
        self.counts: Dict[str, int] = {}

    def __getitem__(self, name):
        return CountingCollection(self._db[name], name, self.counts)

    def __getattr__(self, item):
        # attribute-style collections (db.orders)
        attr = getattr(self._db, item)
        if item.startswith("_"):
            return attr
        # Motor database attributes for collections
        if hasattr(attr, "find"):
            return CountingCollection(attr, item, self.counts)
        return attr


async def seed(db, n_masters: int) -> Dict[str, Any]:
    branch = {
        "id": "br-bench-ygy",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    wh_a = {
        "id": "wh-bench-a",
        "branch_id": branch["id"],
        "code": "MAIN",
        "name": "Gudang Utama",
        "active": True,
        "is_operational": True,
    }
    wh_b = {
        "id": "wh-bench-b",
        "branch_id": branch["id"],
        "code": "B",
        "name": "Gudang B",
        "active": True,
        "is_operational": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(branch)
    await db[WAREHOUSES_COLLECTION].insert_many([wh_a, wh_b])
    await set_r2_authority_active(db, active=False, actor={"id": "bench", "name": "bench"})
    await set_warehouse_stock_standalone_enabled(
        db, enabled=True, user={"id": "bench", "name": "bench"},
    )

    masters = []
    for i in range(n_masters):
        masters.append({
            "id": f"m-{i:04d}",
            "item_code": f"BENCH-{i:04d}",
            "item_name": f"Bench Item {i:04d}",
            "unit": "PCS",
            "item_type": "INVENTORY",
            "is_active": True,
            "accurate_item_id": f"ACC-{i:04d}",
        })
    # Noise types that catalog must exclude after type scan.
    masters.append({
        "id": "m-svc",
        "item_code": "SVC-1",
        "item_name": "Service Add On",
        "unit": "PCS",
        "item_type": "SERVICE",
        "is_active": True,
    })
    await db[MASTER_ITEMS_COLLECTION].insert_many(masters)

    # Direct balances for speed (catalog reads balances; not a production write path).
    # Covers: missing baseline (no doc), dual WH, deficit qty.
    balance_docs = []
    for i in range(0, n_masters, 3):
        mid = f"m-{i:04d}"
        balance_docs.append({
            "id": f"bal-a-{mid}",
            "master_item_id": mid,
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100 + (i % 17),
            "baseline_status": "RECONCILED",
            "updated_at": "2026-10-16T10:00:00+00:00",
            "last_movement_id": f"mov-a-{mid}",
        })
    for i in range(0, n_masters, 7):
        mid = f"m-{i:04d}"
        balance_docs.append({
            "id": f"bal-b-{mid}",
            "master_item_id": mid,
            "branch_id": branch["id"],
            "warehouse_id": wh_b["id"],
            "physical_qty": -5 if i % 14 == 0 else 40,
            "baseline_status": "RECONCILED",
            "updated_at": "2026-10-16T12:00:00+00:00",
            "last_movement_id": f"mov-b-{mid}",
        })
    if balance_docs:
        await db[BALANCE_COLLECTION].insert_many(balance_docs)

    # Branch-scoped active orders (~1/4 of masters) for demand work.
    orders = []
    for i in range(0, n_masters, 4):
        mid = f"m-{i:04d}"
        orders.append({
            "id": f"ord-{i:04d}",
            "branch_id": branch["id"],
            "status": "confirmed",
            "cancelled": False,
            "lines": [{
                "master_item_id": mid,
                "qty": 10,
                "process": "print",
                "qty_completed": 0,
            }],
        })
    if orders:
        await db.orders.insert_many(orders)

    return {
        "branch_id": branch["id"],
        "warehouse_ids": [wh_a["id"], wh_b["id"]],
        "n_masters": n_masters,
        "n_orders": len(orders),
        "balance_docs": await db[BALANCE_COLLECTION].count_documents({}),
    }


async def run_once(label: str, repeats: int = 3) -> Dict[str, Any]:
    assert TEST_DB != "eternal_packaging"
    client = AsyncIOMotorClient(MONGO_URL)
    raw = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.db = raw
    server.client = client
    await ensure_indexes(raw)
    meta = await seed(raw, N_MASTERS)

    cdb = CountingDB(raw)
    # Warm indexes / caches outside timed window.
    await list_physical_stock_catalog(
        raw, branch_id=meta["branch_id"], page=1, limit=PAGE_LIMIT,
    )

    latencies: List[float] = []
    last_result = None
    last_counts: Dict[str, int] = {}
    for _ in range(repeats):
        cdb.counts.clear()
        t0 = time.perf_counter()
        last_result = await list_physical_stock_catalog(
            cdb, branch_id=meta["branch_id"], page=1, limit=PAGE_LIMIT,
        )
        latencies.append((time.perf_counter() - t0) * 1000.0)
        last_counts = dict(cdb.counts)

    # Second page for pagination sanity.
    page2 = await list_physical_stock_catalog(
        raw, branch_id=meta["branch_id"], page=2, limit=PAGE_LIMIT,
    )

    report = {
        "label": label,
        "dataset": {
            **meta,
            "note": "synthetic local RS; not production data",
            "page_limit": PAGE_LIMIT,
        },
        "latency_ms": {
            "samples": [round(x, 2) for x in latencies],
            "min": round(min(latencies), 2),
            "median": round(sorted(latencies)[len(latencies) // 2], 2),
            "max": round(max(latencies), 2),
        },
        "mongo_find_counts": last_counts,
        "mongo_find_total": sum(last_counts.values()),
        "response": {
            "total": last_result.get("total"),
            "page": last_result.get("page"),
            "page_count": last_result.get("page_count"),
            "items_len": len(last_result.get("items") or []),
            "stock_read_mode": last_result.get("stock_read_mode"),
            "page2_first_code": (page2.get("items") or [{}])[0].get("item_code"),
            "page1_codes_sample": [r.get("item_code") for r in (last_result.get("items") or [])[:5]],
        },
    }
    await client.drop_database(TEST_DB)
    client.close()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    report = asyncio.run(run_once(args.label, repeats=args.repeats))
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if ARTIFACT.exists():
        try:
            existing = json.loads(ARTIFACT.read_text())
        except Exception:
            existing = {}
    existing[args.label] = report
    ARTIFACT.write_text(json.dumps(existing, indent=2))
    print(json.dumps(report, indent=2))
    print(f"Wrote {ARTIFACT}", file=sys.stderr)


if __name__ == "__main__":
    main()
