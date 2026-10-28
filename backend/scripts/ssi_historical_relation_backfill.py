"""SSI historical relation backfill CLI — DRY-RUN by default.

Plan Add On parent dual-keys and SJ/confirmation line identity from explicit
signals only. Never invents by name/qty. Never touches stock/ledger/qty.

Usage (local / UAT only):
  python -m scripts.ssi_historical_relation_backfill --fixture-json orders.json
  python -m scripts.ssi_historical_relation_backfill --db-name eternal_ssi_prb_uat
  python -m scripts.ssi_historical_relation_backfill --db-name ... --apply \\
      --i-understand-local-uat-only

Refuse production DB names and remote Mongo hosts on --apply.
Do NOT run against VPS production.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ssi_historical_relation_backfill import (  # noqa: E402
    STATUS_VERIFIED_CANDIDATE,
    apply_order_relation_ops,
    assert_no_stock_side_effects,
    build_order_cas_filter,
    build_relation_identity_mongo_set,
    plan_order_relation_backfill,
    plan_orders_relation_backfill,
    snapshot_ssi_columns_for_order,
)

PRODUCTION_DB_DENYLIST = {
    "eternal_packaging",
    "eternal_packaging_prod",
    "eternal_packaging_production",
    "production",
}

# --apply may only target loopback Mongo. UAT-like DB names alone are not enough.
APPLY_ALLOWED_MONGO_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
APPLY_DB_NAME_TOKENS = ("uat", "test", "local", "dev", "preview")


def _refuse_production_db(db_name: Optional[str]) -> Optional[str]:
    name = str(db_name or "").strip().lower()
    if not name:
        return None
    if name in PRODUCTION_DB_DENYLIST or name.endswith("_prod") or "production" in name:
        return f"Refusing DB_NAME={db_name!r} — production-like name blocked."
    return None


def _mongo_url() -> str:
    return (os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017").strip()


def _mongo_host(mongo_url: str) -> str:
    raw = str(mongo_url or "").strip()
    if not raw:
        return ""
    # mongodb+srv://host/... or mongodb://user:pass@host:port/...
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    if host:
        return host
    # Fallback for atypical URIs without netloc
    if "://" in raw:
        rest = raw.split("://", 1)[1]
        authority = rest.split("/", 1)[0]
        if "@" in authority:
            authority = authority.rsplit("@", 1)[-1]
        hostport = authority.split(",")[0]
        return hostport.split(":")[0].strip().lower()
    return ""


def refuse_apply_database_target(
    db_name: Optional[str],
    mongo_url: Optional[str] = None,
) -> Optional[str]:
    """Hard guard for --apply: local loopback + non-production UAT-like DB name.

    A UAT-like DB name pointed at a remote/production Mongo host is refused.
    """
    err = _refuse_production_db(db_name)
    if err:
        return err
    name = str(db_name or "").strip().lower()
    if not name:
        return "Refusing --apply without --db-name."
    if not any(tok in name for tok in APPLY_DB_NAME_TOKENS):
        return (
            f"Refusing --apply to DB_NAME={db_name!r}; "
            "name must include uat/test/local/dev/preview."
        )
    url = mongo_url if mongo_url is not None else _mongo_url()
    host = _mongo_host(url)
    if host not in APPLY_ALLOWED_MONGO_HOSTS:
        return (
            f"Refusing --apply to Mongo host {host!r} "
            f"(url={url!r}); loopback only (127.0.0.1/localhost)."
        )
    return None


def _load_fixture(path: Path) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("orders"), list):
        return [o for o in data["orders"] if isinstance(o, dict)]
    if isinstance(data, list):
        return [o for o in data if isinstance(o, dict)]
    raise SystemExit("Fixture must be a JSON list of orders or {orders: [...]}.")


async def _load_db_orders(db_name: str, *, limit: int = 500) -> List[dict]:
    from motor.motor_asyncio import AsyncIOMotorClient

    url = _mongo_url()
    client = AsyncIOMotorClient(url)
    try:
        db = client[db_name]
        cur = db.orders.find({}, {"_id": 0}).limit(int(limit))
        return await cur.to_list(length=int(limit))
    finally:
        client.close()


async def _apply_db_ops(db_name: str, plans: List[dict]) -> Dict[str, Any]:
    """Apply VERIFIED_CANDIDATE ops via targeted $set + CAS on updated_at.

    Never uses whole-document replace_one. Concurrent order mutations that
    change updated_at cause matched_count=0 (stale plan skipped).
    """
    guard = refuse_apply_database_target(db_name, _mongo_url())
    if guard:
        raise RuntimeError(guard)

    from motor.motor_asyncio import AsyncIOMotorClient

    url = _mongo_url()
    client = AsyncIOMotorClient(url)
    updated = 0
    skipped = 0
    stale = 0
    failed: List[dict] = []
    try:
        db = client[db_name]
        for plan in plans:
            oid = plan.get("order_id")
            ops = plan.get("ops") or []
            if not oid or not ops:
                skipped += 1
                continue
            try:
                set_fields = build_relation_identity_mongo_set(ops)
            except ValueError as exc:
                failed.append({"order_id": oid, "error": str(exc)})
                continue
            if not set_fields:
                skipped += 1
                continue

            # Re-read for side-effect assertion + CAS token freshness check.
            doc = await db.orders.find_one({"id": oid}, {"_id": 0})
            if not doc:
                skipped += 1
                continue
            expected = plan.get("expected_updated_at", doc.get("updated_at"))
            # If plan token disagrees with live doc before write → stale.
            plan_token = plan.get("expected_updated_at")
            live_token = doc.get("updated_at")
            if plan_token is not None and str(plan_token).strip() != "":
                if live_token != plan_token:
                    stale += 1
                    continue

            before = dict(doc)
            after = apply_order_relation_ops(doc, ops)
            assert_no_stock_side_effects(before, after)

            filt = build_order_cas_filter(oid, expected_updated_at=expected)
            result = await db.orders.update_one(filt, {"$set": set_fields})
            if result.matched_count == 0:
                stale += 1
                continue
            if result.modified_count == 0 and result.matched_count == 1:
                # Idempotent re-apply: matched but already equal.
                skipped += 1
                continue
            updated += 1
        return {
            "updated_orders": updated,
            "skipped_orders": skipped,
            "stale_orders": stale,
            "failed": failed,
        }
    finally:
        client.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SSI historical relation backfill (DRY-RUN default).",
    )
    parser.add_argument("--fixture-json", type=str, help="Local orders JSON fixture")
    parser.add_argument("--db-name", type=str, help="Isolated UAT Mongo DB name")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write VERIFIED_CANDIDATE ops (requires --i-understand-local-uat-only)",
    )
    parser.add_argument(
        "--i-understand-local-uat-only",
        action="store_true",
        help="Required safety latch for --apply",
    )
    parser.add_argument(
        "--report-json",
        type=str,
        help="Optional path to write full plan JSON",
    )
    parser.add_argument(
        "--compare-ssi",
        action="store_true",
        help="Include before/after five-column SSI snapshots for planned orders",
    )
    args = parser.parse_args(argv)

    if not args.fixture_json and not args.db_name:
        parser.error("Provide --fixture-json and/or --db-name")

    if args.db_name:
        err = _refuse_production_db(args.db_name)
        if err:
            print(err, file=sys.stderr)
            return 2

    if args.apply and not args.i_understand_local_uat_only:
        print(
            "Refusing --apply without --i-understand-local-uat-only",
            file=sys.stderr,
        )
        return 2
    if args.apply and not args.db_name and not args.fixture_json:
        print("Apply requires --db-name (UAT) or --fixture-json", file=sys.stderr)
        return 2
    if args.apply and args.db_name:
        guard = refuse_apply_database_target(args.db_name, _mongo_url())
        if guard:
            print(guard, file=sys.stderr)
            return 2

    orders: List[dict] = []
    if args.fixture_json:
        orders.extend(_load_fixture(Path(args.fixture_json)))
    if args.db_name and not args.fixture_json:
        orders.extend(asyncio.run(_load_db_orders(args.db_name, limit=args.limit)))

    report = plan_orders_relation_backfill(orders)
    report["mode"] = "APPLY" if args.apply else "DRY-RUN"
    report["source"] = {
        "fixture": args.fixture_json,
        "db_name": args.db_name,
        "limit": args.limit,
    }

    if args.compare_ssi:
        comparisons = []
        for plan, order in zip(report["plans"], orders):
            before = snapshot_ssi_columns_for_order(order)
            after_order = apply_order_relation_ops(order, plan.get("ops") or [])
            after = snapshot_ssi_columns_for_order(after_order)
            comparisons.append({
                "order_id": order.get("id"),
                "before": before,
                "after": after,
                "ops_count": len(plan.get("ops") or []),
            })
            assert_no_stock_side_effects(order, after_order)
        report["ssi_column_comparisons"] = comparisons

    print("=== SSI historical relation backfill ===")
    print(f"mode={report['mode']}")
    print(f"orders={report['order_count']}")
    print(f"counts={json.dumps(report['counts'], ensure_ascii=False)}")
    print(f"inference_policy={json.dumps(report['inference_policy'])}")
    safe = report["counts"].get(STATUS_VERIFIED_CANDIDATE, 0)
    print(
        f"safe_candidates={safe} "
        f"ambiguous={report['counts'].get('AMBIGUOUS', 0)} "
        f"missing={report['counts'].get('MISSING', 0)} "
        f"conflict={report['counts'].get('CONFLICT', 0)}"
    )

    if args.apply:
        if args.fixture_json and not args.db_name:
            # In-memory apply for fixture path — rewrite fixture only when asked.
            applied = []
            for plan, order in zip(report["plans"], orders):
                after = apply_order_relation_ops(order, plan.get("ops") or [])
                assert_no_stock_side_effects(order, after)
                applied.append(after)
            out_path = Path(args.fixture_json)
            backup = out_path.with_suffix(out_path.suffix + ".bak")
            backup.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
            out_path.write_text(
                json.dumps(applied, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"fixture_apply=ok backup={backup}")
        elif args.db_name:
            try:
                result = asyncio.run(_apply_db_ops(args.db_name, report["plans"]))
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            report["apply_result"] = result
            print(f"db_apply={json.dumps(result)}")
            # Idempotency check
            orders2 = asyncio.run(_load_db_orders(args.db_name, limit=args.limit))
            report2 = plan_orders_relation_backfill(orders2)
            report["post_apply_counts"] = report2["counts"]
            print(f"post_apply_counts={json.dumps(report2['counts'])}")
    else:
        print("dry-run: zero writes performed")

    if args.report_json:
        Path(args.report_json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"report_json={args.report_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
