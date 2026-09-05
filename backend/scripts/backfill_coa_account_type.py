"""Explicit, idempotent backfill of ledger_accounts.account_type from seed JSON.

Default: DRY-RUN (zero-write).
Apply: python -m scripts.backfill_coa_account_type --apply

Source of truth: backend/data/chart_of_accounts_seed.json (not Excel at runtime).
Never runs on API GET or app startup. Do NOT run against production without review.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from services.chart_of_accounts import (  # noqa: E402
    ACCOUNT_TYPE_SET,
    COLLECTION,
    SEED_JSON,
    ensure_indexes,
    load_seed_rows,
    normalize_account_type,
    normalize_code,
    now_iso,
)


def _is_missing_type(value: Any) -> bool:
    if value is None:
        return True
    return not str(value).strip()


async def plan_backfill(db) -> Dict[str, Any]:
    """Preflight: resolve exact record IDs and classify each seed code."""
    seed_rows = load_seed_rows(SEED_JSON)
    by_code = {normalize_code(r["code"]): r for r in seed_rows}

    mapped = 0
    already_correct: List[dict] = []
    would_update: List[dict] = []
    missing_coa: List[dict] = []
    conflicts: List[dict] = []
    unknown_type: List[dict] = []

    for code, row in by_code.items():
        wanted = normalize_account_type(row.get("account_type"))
        if wanted is None or not wanted:
            unknown_type.append({"code": code, "account_type": row.get("account_type")})
            continue
        if wanted not in ACCOUNT_TYPE_SET:
            unknown_type.append({"code": code, "account_type": wanted})
            continue

        docs = await db[COLLECTION].find({"code": code}, {"_id": 0}).to_list(5)
        if not docs:
            missing_coa.append({"code": code, "account_type": wanted, "name": row.get("name")})
            continue
        if len(docs) > 1:
            conflicts.append(
                {
                    "code": code,
                    "reason": "duplicate_code_docs",
                    "ids": [d.get("id") for d in docs],
                    "seed_account_type": wanted,
                }
            )
            continue

        doc = docs[0]
        mapped += 1
        current = doc.get("account_type")
        entry = {
            "id": doc.get("id"),
            "code": code,
            "name": doc.get("name"),
            "current_account_type": current if not _is_missing_type(current) else None,
            "seed_account_type": wanted,
        }
        if _is_missing_type(current):
            would_update.append(entry)
            continue
        current_norm = normalize_account_type(current)
        if current_norm == wanted:
            already_correct.append(entry)
            continue
        conflicts.append(
            {
                **entry,
                "reason": "conflicting_non_null_account_type",
            }
        )

    return {
        "seed_total": len(seed_rows),
        "mapped": mapped,
        "already_correct": already_correct,
        "would_update": would_update,
        "missing_coa": missing_coa,
        "conflicts": conflicts,
        "unknown_type": unknown_type,
    }


async def apply_backfill(db, plan: Dict[str, Any], *, actor: str) -> Dict[str, Any]:
    """Update exact records one-by-one. Only missing/null account_type."""
    if plan["conflicts"] or plan["unknown_type"]:
        raise RuntimeError(
            "Backfill blocked: conflicts or unknown types present. Fix before --apply."
        )

    updated: List[dict] = []
    now = now_iso()
    for entry in plan["would_update"]:
        aid = str(entry.get("id") or "").strip()
        code = normalize_code(entry.get("code"))
        wanted = entry.get("seed_account_type")
        if not aid or not code or not wanted:
            raise RuntimeError(f"Invalid plan entry: {entry}")

        # Re-resolve exact record immediately before write.
        doc = await db[COLLECTION].find_one({"id": aid, "code": code}, {"_id": 0})
        if not doc:
            raise RuntimeError(f"Record vanished before apply: id={aid} code={code}")
        if not _is_missing_type(doc.get("account_type")):
            current = normalize_account_type(doc.get("account_type"))
            if current != wanted:
                raise RuntimeError(
                    f"Conflict appeared before apply: code={code} "
                    f"current={doc.get('account_type')} seed={wanted}"
                )
            continue

        result = await db[COLLECTION].update_one(
            {
                "id": aid,
                "code": code,
                "$or": [
                    {"account_type": {"$exists": False}},
                    {"account_type": None},
                    {"account_type": ""},
                ],
            },
            {
                "$set": {
                    "account_type": wanted,
                    "updated_at": now,
                    "updated_by": actor,
                }
            },
        )
        if result.matched_count != 1:
            raise RuntimeError(f"Exact update miss for id={aid} code={code}")
        updated.append({"id": aid, "code": code, "account_type": wanted})

    return {"updated": updated, "updated_count": len(updated)}


def _summarize(plan: Dict[str, Any], apply_result: Optional[Dict[str, Any]], *, applied: bool) -> str:
    lines = [
        f"seed_total={plan['seed_total']}",
        f"mapped={plan['mapped']}",
        f"already_correct={len(plan['already_correct'])}",
        f"would_update={len(plan['would_update'])}",
        f"missing_coa={len(plan['missing_coa'])}",
        f"conflicts={len(plan['conflicts'])}",
        f"unknown_type={len(plan['unknown_type'])}",
    ]
    if applied and apply_result is not None:
        lines.append(f"updated={apply_result['updated_count']}")
    else:
        lines.append("updated=0 (dry-run)" if not applied else "updated=0")
    return "\n".join(lines)


async def main_async(apply: bool) -> int:
    mongo = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    db_name = os.environ.get("DB_NAME") or "eternal_packaging"
    client = AsyncIOMotorClient(mongo)
    db = client[db_name]
    await ensure_indexes(db)

    plan = await plan_backfill(db)
    apply_result = None
    blocked = bool(plan["conflicts"] or plan["unknown_type"])

    print("=== COA account_type backfill preflight ===")
    print(_summarize(plan, None, applied=False))
    if plan["missing_coa"]:
        print(f"missing_coa sample: {plan['missing_coa'][:5]}")
    if plan["conflicts"]:
        print(f"conflicts sample: {plan['conflicts'][:5]}")
    if plan["unknown_type"]:
        print(f"unknown_type sample: {plan['unknown_type'][:5]}")

    if apply:
        if blocked:
            print("STOP: conflicts/unknown_type — no writes performed.")
            client.close()
            return 2
        apply_result = await apply_backfill(
            db, plan, actor="backfill:coa_account_type"
        )
        print("=== apply ===")
        print(_summarize(plan, apply_result, applied=True))
        # Idempotent check
        plan2 = await plan_backfill(db)
        print("=== post-apply rerun classification ===")
        print(_summarize(plan2, None, applied=False))
        if plan2["would_update"] or plan2["conflicts"] or plan2["unknown_type"]:
            print("WARN: post-apply still has pending/conflict items (missing_coa may remain).")
    else:
        print("DRY-RUN: zero writes. Re-run with --apply to update.")

    client.close()
    return 2 if blocked and apply else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill ledger_accounts.account_type")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform exact per-record updates (default is dry-run)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(main_async(apply=bool(args.apply)))


if __name__ == "__main__":
    raise SystemExit(main())
