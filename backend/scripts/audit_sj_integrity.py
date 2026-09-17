#!/usr/bin/env python3
"""Read-only global SJ integrity auditor. Never writes the database.

Database names in this Cursor environment:
- LOCAL EMPTY DB: eternal_packaging (empty local Mongo; not VPS production)
- PREVIEW DB: eternal_packaging_sj_fix
- PRODUCTION DB: the VPS production database. This script does not store its URI
  and is not run against it here.

After merge/deploy, a human can run read-only audit on the VPS with that host's env:
  python3 scripts/audit_sj_integrity.py --mongo-url "$MONGO_URL" --db-name "$DB_NAME" \\
      --json-out /tmp/sj_audit.json --csv-out /tmp/sj_audit.csv
Do not pass credentials on the command line; use the VPS environment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.sj_integrity_audit import audit_orders, report_to_csv, report_to_json


def _load_orders_from_mongo(url: str, db_name: str, po_id: str | None):
    from pymongo import MongoClient
    client = MongoClient(url, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    query = {"id": po_id} if po_id else {}
    # Projection omits proof/signature bytes.
    proj = {
        "_id": 0,
        "id": 1,
        "po_number": 1,
        "customer": 1,
        "customer_name": 1,
        "updated_at": 1,
        "line_items": 1,
        "total_quantity": 1,
        "packing_info": 1,
        "shipments": 1,
        "surat_jalan_files.id": 1,
        "surat_jalan_files.shipment_id": 1,
        "surat_jalan_files.sj_number": 1,
        "surat_jalan_files.number": 1,
        "surat_jalan_files.name": 1,
        "surat_jalan_files.lines": 1,
        "surat_jalan_files.status": 1,
        "surat_jalan_files.uploaded_at": 1,
        "surat_jalan_files.source": 1,
        "loading_confirmations": 1,
        "loading_snapshots": 1,
        "loading_discrepancies": 1,
        "loading_info": 1,
        "delivery_confirmations": 1,
        "qty_delivered": 1,
        "delivery_status": 1,
        "delivery_proofs.id": 1,
        "delivery_proofs.shipment_id": 1,
        "delivery_recipient_signature.shipment_id": 1,
        "delivery_recipient_signature.recipient_name": 1,
        "delivery_driver_signature.shipment_id": 1,
    }
    return list(db.orders.find(query, proj))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read-only SJ integrity auditor")
    p.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", ""))
    p.add_argument("--db-name", default=os.environ.get("DB_NAME", "eternal_packaging"))
    p.add_argument("--po-id", default=None)
    p.add_argument("--fixture-json", default=None, help="Read orders from a JSON file instead of Mongo")
    p.add_argument("--json-out", default=None)
    p.add_argument("--csv-out", default=None)
    args = p.parse_args(argv)

    if args.fixture_json:
        data = json.loads(Path(args.fixture_json).read_text(encoding="utf-8"))
        orders = data if isinstance(data, list) else data.get("orders") or [data]
    elif args.mongo_url:
        orders = _load_orders_from_mongo(args.mongo_url, args.db_name, args.po_id)
    else:
        p.error("Provide --fixture-json or --mongo-url")

    if args.po_id and args.fixture_json:
        orders = [o for o in orders if str(o.get("id") or "") == args.po_id]

    report = audit_orders(orders)
    text = report_to_json(report)
    csv_text = report_to_csv(report)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    if args.csv_out:
        Path(args.csv_out).write_text(csv_text, encoding="utf-8")
    if not args.json_out:
        print(text)
    print(
        f"read_only={report['read_only']} wrote={report['wrote']} "
        f"orders={report['order_count']} counts={report['counts']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
