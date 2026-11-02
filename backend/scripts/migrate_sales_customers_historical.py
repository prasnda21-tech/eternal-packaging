"""One-time sales_customers historical reconciliation CLI.

DEFAULT = dry-run, fully zero-write (sales_customers / orders / migration audit).

Apply (Owner-approved only; durable audit written):
    SALES_CUSTOMER_MIGRATE_OWNER_APPROVED=YES \\
      python -m scripts.migrate_sales_customers_historical --apply --owner-approved

Selective rollback (Owner-approved only; durable audit written):
    SALES_CUSTOMER_MIGRATE_OWNER_APPROVED=YES \\
      python -m scripts.migrate_sales_customers_historical --rollback BATCH_ID --owner-approved

Never called from server startup / deploy / restart.
Does not mutate orders, invoices, AR, Accurate, inventory, or journals.
Does not replace the entire sales_customers collection.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.sales_customer_historical_migration import (  # noqa: E402
    apply_historical_customer_migration,
    dump_report,
    plan_historical_customer_migration,
    rollback_historical_customer_migration,
)


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    mongo_url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    db_name = os.environ.get("DB_NAME") or "eternal_packaging"
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        if args.rollback:
            report = await rollback_historical_customer_migration(
                db,
                args.rollback,
                owner_approved=bool(args.owner_approved),
            )
        elif args.apply:
            report = await apply_historical_customer_migration(
                db,
                owner_approved=bool(args.owner_approved),
                create_backup=not args.no_backup,
            )
        else:
            # Dry-run is fully zero-write by default (including migration audit).
            report = await plan_historical_customer_migration(
                db,
                write_audit=bool(args.write_audit),
            )
        text = dump_report(report, path=args.report)
        print(text)
        counts = report.to_dict()["counts"]
        print(
            f"mode={report.mode} batch_id={report.batch_id} "
            f"created={counts['created']} skipped={counts['skipped']} "
            f"ambiguous={counts['ambiguous']} incomplete={counts['incomplete']} "
            f"errors={counts['errors']} excluded={counts['excluded']} "
            f"rolled_back={counts.get('rolled_back', 0)}"
        )
        if report.backup_collection:
            print(f"backup_collection={report.backup_collection}")
        return 1 if counts["errors"] else 0
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write new masters (requires --owner-approved + env token).",
    )
    parser.add_argument(
        "--rollback",
        default=None,
        metavar="BATCH_ID",
        help="Selective rollback of one migration batch (requires --owner-approved).",
    )
    parser.add_argument(
        "--owner-approved",
        action="store_true",
        help="Confirm Owner approval for apply/rollback.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup collection on apply (not recommended).",
    )
    parser.add_argument(
        "--write-audit",
        action="store_true",
        help="Dry-run only: optionally write sales_customer_migration_runs (default: zero-write).",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Optional path to write JSON report.",
    )
    args = parser.parse_args(argv)
    if args.apply and args.rollback:
        parser.error("Pilih --apply atau --rollback, bukan keduanya.")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
