#!/usr/bin/env python3
"""Repair three evidence-verified legacy SJ cases. Dry-run by default.

The script is closed over exact PO, Loading, and shipment UUIDs.  It never
creates or rewrites photos, signatures, recipient names, or delivery rows.
Production apply requires explicit database, case, CAS, and confirmation.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from quantity_rules import canonical_shipment_id, shipment_is_active, shipment_qty_total  # noqa: E402
from services.sj_integrity import cas_token  # noqa: E402
from services.sj_integrity_audit import audit_order  # noqa: E402


ACTOR = "sj.integrity.manual-verified-repair"
CONFIRM_PHRASE = "APPLY_MANUAL_SJ_REPAIR"
PRODUCTION_DB = "eternal_packaging"
BACKUP_ROOT = Path("/var/backups/eternal-packaging/sj-integrity")

CASES = {
    "soebakrie": {
        "po_id": "092f2d02-15c3-4985-8b24-d6c3271c5c94",
        "po_number": "PO/2026/09/14/000008",
        "loading_id": "dd318fa2-38a6-4142-8f40-57d7f47fb67d",
        "shipment_id": "7f301c23-d30d-40b4-a4b4-e399319177f1",
        "sj_number": "SJ/2026/09/14/000008",
        "expected_qty": 1000,
    },
    "locan": {
        "po_id": "d1d4c32f-dda1-4c55-b53a-5be27d5383ea",
        "po_number": "PO/2026/09/09/000022",
        "loading_id": "1bef7b84-8c32-47e9-91cf-5eceb58c1cff",
        "shipment_id": "0df8e3a2-e3c4-4933-aeb3-0f94445eced7",
        "sj_number": "SJ/2026/09/09/000022_2",
        "expected_qty": 800,
        "current_qty": 200,
        "sales_delivery_id": "0df8e3a2-e3c4-4933-aeb3-0f94445eced7",
    },
    "seven-corner": {
        "po_id": "e4ffc01f-da61-49bf-85df-2c7bea99fda0",
        "po_number": "PO/2026/09/09/000015",
        "loading_id": "910a32ad-8ec3-4293-8ed6-f6a552f586c8",
        "shipment_id": "0d15f141-5f70-4dcb-94fe-a13627c80e0d",
        "sj_number": "SJ/2026/09/09/000015_2",
        "expected_qty": 16000,
        "historical_proof_review": True,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _by_id(rows: Iterable[dict], record_id: str) -> Optional[dict]:
    return next(
        (row for row in rows if isinstance(row, dict) and str(row.get("id") or "") == record_id),
        None,
    )


def _sj_file_for_shipment(order: dict, shipment_id: str) -> Optional[dict]:
    matches = [
        row for row in order.get("surat_jalan_files") or []
        if isinstance(row, dict)
        and str(row.get("shipment_id") or row.get("id") or "") == shipment_id
    ]
    return matches[0] if len(matches) == 1 else None


def _set_single_line_qty(record: dict, qty: int) -> None:
    lines = record.get("lines")
    if isinstance(lines, list) and lines:
        if len(lines) != 1 or not isinstance(lines[0], dict):
            raise ValueError(f"Expected one line on {record.get('id')}")
        lines[0]["qty"] = qty
        if "quantity" in lines[0]:
            lines[0]["quantity"] = qty
    record["total_qty"] = qty


def verify_preconditions(order: dict, spec: dict) -> list[str]:
    errors: list[str] = []
    if str(order.get("id") or "") != spec["po_id"]:
        errors.append("PO UUID mismatch")
    if str(order.get("po_number") or "") != spec["po_number"]:
        errors.append("PO number mismatch")

    loading = _by_id(order.get("loading_confirmations") or [], spec["loading_id"])
    if loading is None:
        errors.append("target Loading confirmation missing")
    else:
        bound = str(loading.get("shipment_id") or "").strip()
        if bound and bound != spec["shipment_id"]:
            errors.append("target Loading confirmation bound to another shipment")
        if str(loading.get("surat_jalan_id") or "").strip() != spec["shipment_id"]:
            errors.append("Loading surat_jalan_id mismatch")

    shipment = _by_id(order.get("shipments") or [], spec["shipment_id"])
    sj_file = _sj_file_for_shipment(order, spec["shipment_id"])
    if shipment is None or not shipment_is_active(shipment):
        errors.append("target shipment missing or inactive")
    if sj_file is None:
        errors.append("target SJ file missing")

    current_qty = spec.get("current_qty")
    if current_qty is not None and shipment is not None:
        actual = shipment_qty_total(shipment)
        if actual not in (current_qty, spec["expected_qty"]):
            errors.append(f"target shipment qty mismatch: {actual}")
    elif shipment is not None and shipment_qty_total(shipment) != spec["expected_qty"]:
        errors.append("target shipment qty changed")
    return errors


def apply_manual_repair(order: dict, spec: dict, *, now: str) -> list[dict]:
    errors = verify_preconditions(order, spec)
    if errors:
        raise ValueError("; ".join(errors))

    loading = _by_id(order.get("loading_confirmations") or [], spec["loading_id"])
    shipment = _by_id(order.get("shipments") or [], spec["shipment_id"])
    sj_file = _sj_file_for_shipment(order, spec["shipment_id"])
    ops: list[dict] = []

    if not str(loading.get("shipment_id") or "").strip():
        loading["shipment_id"] = spec["shipment_id"]
        loading["sj_number"] = spec["sj_number"]
        loading["identity_backfill"] = {
            "reason": "verified exact surat_jalan_id binding",
            "canonical_shipment_id": spec["shipment_id"],
            "sj_number": spec["sj_number"],
            "applied_at": now,
            "actor": ACTOR,
        }
        ops.append({
            "op": "stamp_shipment_id",
            "record_id": spec["loading_id"],
            "shipment_id": spec["shipment_id"],
        })

    if spec.get("current_qty") is not None and shipment_qty_total(shipment) != spec["expected_qty"]:
        _set_single_line_qty(shipment, spec["expected_qty"])
        _set_single_line_qty(sj_file, spec["expected_qty"])
        ops.append({
            "op": "restore_qty_from_loading_snapshot",
            "shipment_id": spec["shipment_id"],
            "from": spec["current_qty"],
            "to": spec["expected_qty"],
        })

    order["updated_at"] = now
    order["sj_integrity_manual_repair"] = {
        "actor": ACTOR,
        "timestamp": now,
        "po_id": spec["po_id"],
        "ops": copy.deepcopy(ops),
        "preserved_delivery_evidence": True,
    }
    return ops


def _historical_proof_review(order: dict, spec: dict) -> list[dict]:
    if not spec.get("historical_proof_review"):
        return []
    sid = spec["shipment_id"]
    sj_file = _sj_file_for_shipment(order, sid) or {}
    canonical_sj_file_id = str(sj_file.get("id") or "")
    out = []
    recipients = {
        str(row.get("id") or ""): row
        for row in order.get("delivery_recipient_signatures") or []
        if isinstance(row, dict)
    }
    drivers = {
        str(row.get("id") or ""): row
        for row in order.get("delivery_driver_signatures") or []
        if isinstance(row, dict)
    }
    for rec in order.get("delivery_confirmations") or []:
        if not isinstance(rec, dict) or str(rec.get("shipment_id") or "") != sid:
            continue
        mismatches = []
        if str(rec.get("sj_file_id") or "") not in ("", canonical_sj_file_id):
            mismatches.append("sj_file_id")
        for field, lookup in (
            ("recipient_signature_id", recipients),
            ("driver_signature_id", drivers),
        ):
            record_id = str(rec.get(field) or "")
            record = lookup.get(record_id)
            if record_id and (not record or str(record.get("shipment_id") or "") != sid):
                mismatches.append(field)
        if mismatches:
            out.append({
                "confirmation_id": rec.get("id"),
                "shipment_id": sid,
                "reason_code": "HISTORICAL_PROOF_REVIEW",
                "mismatched_fields": mismatches,
                "recommendation": "Jangan mengarang bukti; pertahankan histori dan review dokumen asli.",
            })
    return out


def summarize(order: dict, spec: dict) -> dict:
    audit = audit_order(order)
    return {
        "severity": audit.get("severity"),
        "po_qty": audit.get("po_qty"),
        "active_sj_qty": audit.get("active_sj_qty"),
        "loaded_qty": audit.get("loaded_qty"),
        "delivered_qty": audit.get("delivered_qty"),
        "remaining_delivery": audit.get("remaining_delivery"),
        "findings": [row.get("reason_code") for row in audit.get("findings") or []],
        "historical_proof_review": _historical_proof_review(order, spec),
    }


def build_report(order: dict, spec: dict) -> dict:
    working = copy.deepcopy(order)
    ops = apply_manual_repair(working, spec, now=_now())
    return {
        "ok": True,
        "po_id": spec["po_id"],
        "dry_run": True,
        "read_only": True,
        "wrote": False,
        "cas": cas_token(order),
        "before": summarize(order, spec),
        "ops": ops,
        "after_if_applied": summarize(working, spec),
        "working_document": working,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Repair verified SOEBAKRIE, LOCAN, or Seven Corner SJ history")
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", ""))
    parser.add_argument("--db-name", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cas", default="")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--backup-dir", default=str(BACKUP_ROOT))
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv(ROOT / ".env")
    mongo_url = args.mongo_url or os.environ.get("MONGO_URL")
    db_name = args.db_name or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL/DB_NAME required")
    if args.apply:
        backup = Path(args.backup_dir).expanduser().resolve()
        root = BACKUP_ROOT.resolve()
        if (
            args.db_name != PRODUCTION_DB
            or not args.cas
            or args.confirm != CONFIRM_PHRASE
            or (backup != root and root not in backup.parents)
        ):
            print(json.dumps({"ok": False, "wrote": False, "error": "production apply guard failed"}, indent=2))
            return 2

    from pymongo import MongoClient
    from bson import json_util

    spec = CASES[args.case]
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    order = db.orders.find_one({"id": spec["po_id"]})
    if not order:
        print(json.dumps({"ok": False, "wrote": False, "error": "PO not found"}, indent=2))
        return 1
    errors = verify_preconditions(order, spec)
    if errors:
        print(json.dumps({"ok": False, "wrote": False, "errors": errors}, indent=2))
        return 1

    report = build_report(order, spec)
    working = report.pop("working_document")
    if not args.apply:
        print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
        return 0
    if args.cas != report["cas"]:
        print(json.dumps({"ok": False, "wrote": False, "error": "CAS mismatch", "current_cas": report["cas"]}, indent=2))
        return 2

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    sales_doc = None
    if spec.get("sales_delivery_id"):
        sales_doc = db.sales_deliveries.find_one({"id": spec["sales_delivery_id"]})
        if not sales_doc or int(sales_doc.get("total_qty") or 0) not in (spec["current_qty"], spec["expected_qty"]):
            print(json.dumps({"ok": False, "wrote": False, "error": "sales delivery precondition mismatch"}, indent=2))
            return 2
    backup = backup_dir / f"{args.case}-{spec['po_id']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    backup.write_text(json_util.dumps({"order": order, "sales_delivery": sales_doc}, indent=2, ensure_ascii=False), encoding="utf-8")

    selector = {"_id": order["_id"]}
    if "updated_at" in order:
        selector["updated_at"] = order["updated_at"]
    replaced = db.orders.replace_one(selector, working)
    if replaced.matched_count != 1:
        print(json.dumps({"ok": False, "wrote": False, "error": "atomic order CAS mismatch", "backup": str(backup)}, indent=2))
        return 2

    if sales_doc is not None and int(sales_doc.get("total_qty") or 0) != spec["expected_qty"]:
        sales_selector = {"_id": sales_doc["_id"], "total_qty": spec["current_qty"]}
        sales_update = {
            "$set": {
                "total_qty": spec["expected_qty"],
                "line_items.0.qty": spec["expected_qty"],
                "updated_at": working["updated_at"],
                "sj_integrity_repaired_at": working["updated_at"],
                "sj_integrity_repaired_by": ACTOR,
            }
        }
        updated = db.sales_deliveries.update_one(sales_selector, sales_update)
        if updated.matched_count != 1:
            rollback = db.orders.replace_one({"_id": order["_id"], "updated_at": working["updated_at"]}, order)
            error = "sales delivery CAS mismatch; order rolled back" if rollback.matched_count == 1 else "CRITICAL: sales delivery CAS mismatch and order rollback failed"
            print(json.dumps({"ok": False, "wrote": False, "error": error, "backup": str(backup)}, indent=2))
            return 2

    final = db.orders.find_one({"_id": order["_id"]})
    print(json.dumps({
        "ok": True,
        "wrote": True,
        "case": args.case,
        "backup": str(backup),
        "after": summarize(final, spec),
    }, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
