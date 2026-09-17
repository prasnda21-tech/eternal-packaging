#!/usr/bin/env python3
"""SAFE_AUTO_REPAIR planner/applier. Default dry-run. Per-PO CAS and backup.

Never repairs NEEDS_MANUAL_REVIEW or CRITICAL_BLOCKER.
One PO failure does not mutate other POs.

Database names in this Cursor environment:
- LOCAL EMPTY DB: eternal_packaging (empty local Mongo; not VPS production)
- PREVIEW DB: eternal_packaging_sj_fix
- PRODUCTION DB: the VPS production database. This script does not store its URI.

--apply requires an explicit --db-name (or --fixture-json) and is refused on the
LOCAL EMPTY DB. This agent does not run --apply against VPS production.

After merge/deploy, dry-run first on the VPS with that host's env:
  python3 scripts/repair_sj_integrity.py --mongo-url "$MONGO_URL" --db-name "$DB_NAME"
Do not pass --apply until a human reviews that dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.sj_integrity import apply_repair_ops, cas_token
from services.sj_integrity_audit import (
    SEVERITY_SAFE_AUTO_REPAIR,
    audit_order,
    audit_orders,
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def plan_orders(orders):
    report = audit_orders(orders)
    plans = []
    for row in report["orders"]:
        if row["severity"] != SEVERITY_SAFE_AUTO_REPAIR:
            continue
        plans.append({
            "po_id": row["po_id"],
            "po_number": row["po_number"],
            "cas_token": row["cas_token"],
            "ops": row.get("safe_repair_ops") or [],
        })
    return report, plans


LOCAL_EMPTY_DB = "eternal_packaging"
PREVIEW_DB = "eternal_packaging_sj_fix"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", ""))
    p.add_argument("--db-name", default=None)
    p.add_argument("--fixture-json", default=None)
    p.add_argument("--po-id", action="append", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--actor", default="sj.integrity.repair")
    p.add_argument("--backup-dir", default="backups/sj-integrity")
    args = p.parse_args(argv)
    explicit_db = args.db_name is not None
    args.db_name = args.db_name or os.environ.get("DB_NAME") or LOCAL_EMPTY_DB

    if args.apply and not args.fixture_json:
        if not explicit_db:
            print(json.dumps({
                "ok": False,
                "error": "--apply requires explicit --db-name (PREVIEW DB) or --fixture-json.",
                "wrote": False,
            }, indent=2))
            return 2
        if args.db_name == LOCAL_EMPTY_DB:
            print(json.dumps({
                "ok": False,
                "error": (
                    "Refusing --apply on LOCAL EMPTY DB "
                    f"(DB_NAME={LOCAL_EMPTY_DB} in this Cursor environment). "
                    f"Use PREVIEW DB {PREVIEW_DB} or --fixture-json. "
                    "This script does not connect to VPS production."
                ),
                "wrote": False,
            }, indent=2))
            return 2

    if args.fixture_json:
        data = json.loads(Path(args.fixture_json).read_text(encoding="utf-8"))
        orders = data if isinstance(data, list) else data.get("orders") or [data]
        db = None
    else:
        if not args.mongo_url:
            p.error("Provide --fixture-json or --mongo-url")
        from pymongo import MongoClient
        client = MongoClient(args.mongo_url, serverSelectionTimeoutMS=5000)
        db = client[args.db_name]
        query = {"id": {"$in": args.po_id}} if args.po_id else {}
        orders = list(db.orders.find(query, {"_id": 0}))

    if args.po_id:
        wanted = set(args.po_id)
        orders = [o for o in orders if str(o.get("id") or "") in wanted]

    report, plans = plan_orders(orders)
    result = {
        "dry_run": not args.apply,
        "wrote": False,
        "read_only": not args.apply,
        "counts": report["counts"],
        "plans": plans,
        "applied": [],
        "skipped": [],
        "failed": [],
    }
    if not args.apply:
        print(json.dumps(result, indent=2, default=str, ensure_ascii=True))
        return 0

    backup_root = Path(args.backup_dir)
    backup_root.mkdir(parents=True, exist_ok=True)
    by_id = {str(o.get("id") or ""): o for o in orders}
    for plan in plans:
        po_id = plan["po_id"]
        order = by_id.get(po_id)
        if not order:
            result["failed"].append({"po_id": po_id, "error": "missing"})
            continue
        if cas_token(order) != plan["cas_token"]:
            result["failed"].append({"po_id": po_id, "error": "cas_mismatch"})
            continue
        if not plan["ops"]:
            result["skipped"].append({"po_id": po_id, "reason": "no_ops"})
            continue
        backup = backup_root / f"{po_id}-{plan['cas_token'][:12]}.json"
        backup.write_text(json.dumps(order, indent=2, default=str), encoding="utf-8")
        try:
            apply_repair_ops(order, plan["ops"], actor={"email": args.actor}, now=_now())
        except Exception as exc:
            result["failed"].append({"po_id": po_id, "error": str(exc)})
            continue
        if db is not None:
            res = db.orders.update_one(
                {"id": po_id, "updated_at": order.get("updated_at")} if order.get("updated_at") else {"id": po_id},
                {"$set": {
                    "shipments": order.get("shipments"),
                    "surat_jalan_files": order.get("surat_jalan_files"),
                    "loading_confirmations": order.get("loading_confirmations"),
                    "delivery_confirmations": order.get("delivery_confirmations"),
                    "loading_discrepancies": order.get("loading_discrepancies"),
                    "sj_integrity_repaired_at": _now(),
                    "sj_integrity_repaired_by": args.actor,
                }},
            )
            if res.matched_count == 0:
                result["failed"].append({"po_id": po_id, "error": "cas_write_mismatch"})
                continue
        result["applied"].append({"po_id": po_id, "backup": str(backup), "ops": plan["ops"]})
        result["wrote"] = True
        result["read_only"] = False
    print(json.dumps(result, indent=2, default=str, ensure_ascii=True))
    return 0 if not result["failed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
