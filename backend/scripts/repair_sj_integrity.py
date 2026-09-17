#!/usr/bin/env python3
"""SAFE_AUTO_REPAIR planner/applier. Default dry-run. Per-PO CAS and backup.

Never repairs NEEDS_MANUAL_REVIEW or CRITICAL_BLOCKER.
One PO failure does not mutate other POs.

Database names may be identical across local and production environments.  A
database named ``eternal_packaging`` is treated as production-sensitive: an
apply is allowed only when it is explicitly authorized and constrained to
reviewed PO ids, plan count, and operation types.

After merge/deploy, dry-run first on the VPS with that host's env:
  python3 scripts/repair_sj_integrity.py --mongo-url "$MONGO_URL" --db-name "$DB_NAME"
Do not pass --apply until a human reviews that dry-run. Production also needs
--allow-production, the exact confirmation phrase, explicit --po-id values,
--expected-plan-count, and --allowed-op constraints.
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
from quantity_rules import canonical_shipment_id, shipment_is_active, shipments_stored
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


PRODUCTION_DB_NAME = "eternal_packaging"
PREVIEW_DB = "eternal_packaging_sj_fix"
PRODUCTION_CONFIRMATION = "APPLY_SAFE_SJ_STAMPS"
PRODUCTION_BACKUP_ROOT = Path("/var/backups/eternal-packaging/sj-integrity")
PRODUCTION_ALLOWED_OPS = frozenset({"stamp_shipment_id"})
PRODUCTION_ALLOWED_COLLECTIONS = frozenset({"loading_confirmations"})


def _production_argument_error(args) -> str:
    if not args.allow_production:
        return "Production DB requires --allow-production."
    if args.confirm != PRODUCTION_CONFIRMATION:
        return f"Production DB requires --confirm {PRODUCTION_CONFIRMATION}."
    if not args.po_id:
        return "Production DB requires explicit --po-id for every reviewed PO."
    if args.expected_plan_count is None or args.expected_plan_count <= 0:
        return "Production DB requires positive --expected-plan-count."
    if not args.allowed_op:
        return "Production DB requires at least one --allowed-op."
    requested_ops = set(args.allowed_op)
    if requested_ops != PRODUCTION_ALLOWED_OPS:
        return (
            "Production DB permits only --allowed-op stamp_shipment_id "
            f"(received={sorted(requested_ops)})."
        )
    backup = Path(args.backup_dir).expanduser().resolve()
    root = PRODUCTION_BACKUP_ROOT.resolve()
    if backup != root and root not in backup.parents:
        return f"Production backup directory must be under {root}."
    return ""


def _production_plan_error(args, plans) -> str:
    requested = set(args.po_id or [])
    planned = {str(plan.get("po_id") or "") for plan in plans}
    if planned != requested:
        return (
            "Production plan PO ids differ from reviewed --po-id values "
            f"(requested={len(requested)}, planned={len(planned)})."
        )
    if len(plans) != args.expected_plan_count:
        return (
            "Production plan count differs from --expected-plan-count "
            f"(expected={args.expected_plan_count}, planned={len(plans)})."
        )
    allowed = set(args.allowed_op or [])
    operations = [
        op
        for plan in plans
        for op in (plan.get("ops") or [])
    ]
    if not operations:
        return "Production plan contains no operations."
    if len(operations) != len(plans):
        return "Production requires exactly one operation per reviewed PO."
    unexpected = sorted({
        str(op.get("op") or "")
        for op in operations
        if str(op.get("op") or "") not in allowed
    })
    if unexpected:
        return f"Production plan contains disallowed operations: {unexpected}."
    invalid_targets = [
        op for op in operations
        if str(op.get("collection") or "") not in PRODUCTION_ALLOWED_COLLECTIONS
        or not str(op.get("record_id") or "").strip()
        or not str(op.get("shipment_id") or "").strip()
    ]
    if invalid_targets:
        return "Production stamp targets must be explicit loading_confirmations rows."
    target_keys = {
        (str(op.get("collection")), str(op.get("record_id")))
        for op in operations
    }
    if len(target_keys) != len(operations):
        return "Production plan contains duplicate stamp targets."
    return ""


def _production_order_state_error(orders, plans) -> str:
    """Validate every targeted row before the first production write."""
    by_id = {str(order.get("id") or ""): order for order in orders}
    for plan in plans:
        po_id = str(plan.get("po_id") or "")
        order = by_id.get(po_id)
        if order is None:
            return f"Production PO {po_id} is missing from the reviewed snapshot."
        if order.get("updated_at") is None:
            return f"Production PO {po_id} has no updated_at CAS field."
        op = (plan.get("ops") or [None])[0]
        if not isinstance(op, dict):
            return f"Production PO {po_id} has an invalid operation."
        record_id = str(op.get("record_id") or "")
        shipment_id = str(op.get("shipment_id") or "")
        rows = [
            row for row in order.get("loading_confirmations") or []
            if isinstance(row, dict) and str(row.get("id") or "") == record_id
        ]
        if len(rows) != 1:
            return f"Production PO {po_id} must have exactly one target Loading row."
        if str(rows[0].get("shipment_id") or "").strip():
            return f"Production PO {po_id} target Loading row is already bound."
        active_ids = {
            canonical_shipment_id(shipment)
            for shipment in shipments_stored(order)
            if shipment_is_active(shipment)
        }
        if shipment_id not in active_ids:
            return f"Production PO {po_id} target shipment is not active."
    return ""


def _apply_production_stamp(db, *, po_id, updated_at, op, actor):
    """Atomically stamp one reviewed Loading row without rewriting other arrays."""
    repaired_at = _now()
    return db.orders.update_one(
        {
            "id": po_id,
            "updated_at": updated_at,
            "loading_confirmations": {"$elemMatch": {
                "id": op["record_id"],
                "shipment_id": {"$in": [None, ""]},
            }},
        },
        {"$set": {
            "loading_confirmations.$.shipment_id": op["shipment_id"],
            "sj_integrity_repaired_at": repaired_at,
            "sj_integrity_repaired_by": actor,
        }},
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", ""))
    p.add_argument("--db-name", default=None)
    p.add_argument("--fixture-json", default=None)
    p.add_argument("--po-id", action="append", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--actor", default="sj.integrity.repair")
    p.add_argument("--backup-dir", default="backups/sj-integrity")
    p.add_argument("--allow-production", action="store_true")
    p.add_argument("--confirm", default="")
    p.add_argument("--expected-plan-count", type=int, default=None)
    p.add_argument("--allowed-op", action="append", default=None)
    args = p.parse_args(argv)
    explicit_db = args.db_name is not None
    args.db_name = args.db_name or os.environ.get("DB_NAME") or PRODUCTION_DB_NAME

    if args.apply and not args.fixture_json:
        if not explicit_db:
            print(json.dumps({
                "ok": False,
                "error": "--apply requires explicit --db-name (PREVIEW DB) or --fixture-json.",
                "wrote": False,
            }, indent=2))
            return 2
        if args.db_name == PRODUCTION_DB_NAME:
            error = _production_argument_error(args)
            if error:
                print(json.dumps({
                    "ok": False,
                    "error": error,
                    "wrote": False,
                }, indent=2))
                return 2
        elif args.allow_production:
            print(json.dumps({
                "ok": False,
                "error": "--allow-production is valid only with the production DB name.",
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
    production_apply = bool(
        args.apply and not args.fixture_json and args.db_name == PRODUCTION_DB_NAME
    )
    if production_apply:
        error = _production_plan_error(args, plans)
        if not error:
            error = _production_order_state_error(orders, plans)
        if error:
            print(json.dumps({
                "ok": False,
                "error": error,
                "wrote": False,
                "plans": plans,
            }, indent=2, default=str))
            return 2
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
        original_updated_at = order.get("updated_at")
        if production_apply:
            op = plan["ops"][0]
            res = _apply_production_stamp(
                db,
                po_id=po_id,
                updated_at=original_updated_at,
                op=op,
                actor=args.actor,
            )
            if res.matched_count == 0 or res.modified_count != 1:
                result["failed"].append({"po_id": po_id, "error": "cas_write_mismatch"})
                continue
            result["applied"].append({"po_id": po_id, "backup": str(backup), "ops": plan["ops"]})
            result["wrote"] = True
            result["read_only"] = False
            continue
        try:
            apply_repair_ops(order, plan["ops"], actor={"email": args.actor}, now=_now())
        except Exception as exc:
            result["failed"].append({"po_id": po_id, "error": str(exc)})
            continue
        if db is not None:
            res = db.orders.update_one(
                {"id": po_id, "updated_at": original_updated_at} if original_updated_at else {"id": po_id},
                {"$set": {
                    "shipments": order.get("shipments"),
                    "surat_jalan_files": order.get("surat_jalan_files"),
                    "loading_confirmations": order.get("loading_confirmations"),
                    "legacy_loading_allocations": order.get("legacy_loading_allocations"),
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
