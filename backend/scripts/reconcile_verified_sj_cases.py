#!/usr/bin/env python3
"""Repair two user-verified legacy SJ cases. Dry-run by default.

The repair is intentionally closed over exact PO and shipment UUIDs. It never
creates photos, signatures, recipient names, or delivery evidence. Production
apply requires an explicit DB name, CAS token, and confirmation phrase.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from quantity_rules import canonical_shipment_id, shipment_is_active, shipment_qty_total  # noqa: E402
from services.sj_integrity import cas_token  # noqa: E402
from services.sj_integrity_audit import audit_order  # noqa: E402


ACTOR = "sj.integrity.verified-repair"
CONFIRM_PHRASE = "APPLY_VERIFIED_SJ_REPAIR"

NUMANI = {
    "case": "numani",
    "po_id": "258b4eac-d91a-4623-b206-d38c2c2e4d2f",
    "po_number": "PO/2026/09/12/000002",
    "shipments": {
        "c992dfa4-8c67-496c-87e7-bf7eb22a0ea6": ("SJ/2026/09/12/000002", 1000),
        "ee1e3067-33f2-4623-8796-161c76db6690": ("SJ/2026/09/12/000002_2", 4000),
    },
    "supersede": {
        "26c9e4f0-8616-4c06-ab68-c11c4bbca3c5": "ee1e3067-33f2-4623-8796-161c76db6690",
    },
    "deliveries": {
        "6111f185-2f31-4a37-a133-bf5ac2743500": (
            "ee1e3067-33f2-4623-8796-161c76db6690", "SJ/2026/09/12/000002_2", 4000, None
        ),
        "db0719a8-9800-48a5-9b58-5fd4dfd31712": (
            "c992dfa4-8c67-496c-87e7-bf7eb22a0ea6", "SJ/2026/09/12/000002", 1000, None
        ),
    },
    "expected_loaded": 5000,
    "expected_delivered": 5000,
}

SPECIAL_LALAP = {
    "case": "special-lalap",
    "po_id": "56cb3ac6-9f34-4a77-be0f-b24f9d303845",
    "po_number": "PO/2026/09/07/000031",
    "shipments": {
        "03070d62-d6a8-47b4-b549-91a2f71562df": ("SJ/2026/09/07/000031", 3000),
        "d7433f67-361d-4783-a2c4-ce5d0f860ec7": ("SJ/2026/09/07/000031_2", 6000),
        "3ab43353-5705-476b-97fd-f3beb1b1314c": ("SJ/2026/09/07/000031_3", 11000),
    },
    "supersede": {
        "9478d304-71ab-4367-a5c1-ce0caff6cbb6": "3ab43353-5705-476b-97fd-f3beb1b1314c",
    },
    "deliveries": {
        "2eb1f349-4b85-47a8-8ddc-71d751b02b52": (
            "03070d62-d6a8-47b4-b549-91a2f71562df", "SJ/2026/09/07/000031", 3000, None
        ),
        "1aba1afc-5cd3-41e2-8444-5aa0398de8fd": (
            "d7433f67-361d-4783-a2c4-ce5d0f860ec7", "SJ/2026/09/07/000031_2", 6000, []
        ),
        "0c2b6e78-27c4-4e2c-8aab-4def59748a22": (
            "3ab43353-5705-476b-97fd-f3beb1b1314c", "SJ/2026/09/07/000031_3", 11000,
            ["78f5493c-7183-4d9b-9461-8d2f6761573c"],
        ),
    },
    "expected_loaded": 20000,
    "expected_delivered": 20000,
}

CASES = {NUMANI["case"]: NUMANI, SPECIAL_LALAP["case"]: SPECIAL_LALAP}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _by_id(rows: Iterable[dict], record_id: str) -> Optional[dict]:
    return next((row for row in rows if isinstance(row, dict) and str(row.get("id") or "") == record_id), None)


def _set_lines_qty(record: dict, qty: int) -> None:
    lines = record.get("lines") or []
    if len(lines) != 1 or not isinstance(lines[0], dict):
        raise ValueError(f"Expected one line on {record.get('id')}")
    lines[0]["qty"] = qty
    if "quantity" in lines[0]:
        lines[0]["quantity"] = qty
    record["total_qty"] = qty


def _stamp_loading(order: dict, shipment_id: str, sj_number: str) -> int:
    changed = 0
    for collection in ("loading_confirmations", "loading_snapshots"):
        for row in order.get(collection) or []:
            if not isinstance(row, dict) or row.get("cancelled"):
                continue
            source_id = str(row.get("surat_jalan_id") or row.get("shipment_id") or "")
            if source_id != shipment_id:
                continue
            row["shipment_id"] = shipment_id
            row["sj_number"] = sj_number
            changed += 1
    return changed


def _supersede(record: dict, successor_id: str, now: str, reason: str) -> None:
    record["status"] = "superseded"
    record["cancelled"] = True
    record["cancelled_at"] = now
    record["cancelled_by"] = ACTOR
    record["cancelled_reason"] = reason
    record["superseded_at"] = now
    record["superseded_by"] = ACTOR
    record["superseded_by_shipment_id"] = successor_id


def verify_preconditions(order: dict, spec: dict) -> List[str]:
    errors: List[str] = []
    if str(order.get("id") or "") != spec["po_id"]:
        errors.append("PO UUID mismatch")
    if str(order.get("po_number") or "") != spec["po_number"]:
        errors.append("PO number mismatch")
    expected_ids = set(spec["shipments"]) | set(spec["supersede"])
    actual_ids = {canonical_shipment_id(row) for row in order.get("shipments") or [] if isinstance(row, dict)}
    if actual_ids != expected_ids:
        errors.append(f"shipment set mismatch: {sorted(actual_ids)}")
    for sid, (_number, qty) in spec["shipments"].items():
        row = _by_id(order.get("shipments") or [], sid)
        if not row:
            errors.append(f"missing shipment {sid}")
        elif qty <= 0:
            errors.append(f"invalid expected qty {sid}")
    for sid in spec["supersede"]:
        row = _by_id(order.get("shipments") or [], sid)
        if not row:
            errors.append(f"missing superseded candidate {sid}")
        elif not shipment_is_active(row):
            errors.append(f"candidate already inactive {sid}")
    for confirmation_id in spec["deliveries"]:
        if not _by_id(order.get("delivery_confirmations") or [], confirmation_id):
            errors.append(f"missing delivery confirmation {confirmation_id}")
    return errors


def apply_verified_repair(order: dict, spec: dict, *, now: str) -> List[dict]:
    errors = verify_preconditions(order, spec)
    if errors:
        raise ValueError("; ".join(errors))
    ops: List[dict] = []
    reason = f"Verified legacy SJ repair: {spec['case']}"

    for sid, (number, qty) in spec["shipments"].items():
        shipment = _by_id(order.get("shipments") or [], sid)
        sj_file = _by_id(order.get("surat_jalan_files") or [], sid)
        if not shipment or not sj_file:
            raise ValueError(f"Missing shipment/file pair {sid}")
        _set_lines_qty(shipment, qty)
        _set_lines_qty(sj_file, qty)
        shipment["number"] = number
        sj_file["shipment_id"] = sid
        sj_file["sj_number"] = number
        stamped = _stamp_loading(order, sid, number)
        ops.append({"op": "restore_and_stamp", "shipment_id": sid, "qty": qty, "loading_rows": stamped})

    for sid, successor_id in spec["supersede"].items():
        shipment = _by_id(order.get("shipments") or [], sid)
        sj_file = _by_id(order.get("surat_jalan_files") or [], sid)
        if not shipment or not sj_file:
            raise ValueError(f"Missing supersede shipment/file pair {sid}")
        _supersede(shipment, successor_id, now, reason)
        _supersede(sj_file, successor_id, now, reason)
        ops.append({"op": "supersede", "shipment_id": sid, "successor_id": successor_id})

    for confirmation_id, (sid, number, qty, proof_ids) in spec["deliveries"].items():
        rec = _by_id(order.get("delivery_confirmations") or [], confirmation_id)
        if not rec:
            raise ValueError(f"Missing delivery confirmation {confirmation_id}")
        rec["shipment_id"] = sid
        rec["sj_number"] = number
        rec["sj_file_id"] = sid
        rec["qty"] = qty
        items = rec.get("items") or []
        if len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError(f"Expected one delivery item on {confirmation_id}")
        items[0]["qty"] = qty
        if proof_ids is not None:
            rec["proof_ids"] = list(proof_ids)
            rec["photo_count"] = len(proof_ids)
        shipment = _by_id(order.get("shipments") or [], sid)
        shipment["qty_delivered"] = qty
        shipment["delivery_status"] = "Terkirim"
        shipment["delivered_at"] = rec.get("confirmed_at")
        ops.append({"op": "rebind_delivery", "confirmation_id": confirmation_id, "shipment_id": sid, "qty": qty})

    for discrepancy in order.get("loading_discrepancies") or []:
        if isinstance(discrepancy, dict) and str(discrepancy.get("status") or "") in ("", "open", "held"):
            discrepancy["status"] = "superseded"
            discrepancy["resolved_at"] = now
            discrepancy["resolved_by"] = ACTOR
            discrepancy["resolved_reason"] = reason
            ops.append({"op": "resolve_discrepancy", "discrepancy_id": discrepancy.get("id")})

    order["qty_delivered"] = spec["expected_delivered"]
    order["delivery_status"] = "Terkirim"
    order["current_stage"] = max(15, int(order.get("current_stage") or 0))
    order["updated_at"] = now
    order["sj_integrity_repair"] = {
        "actor": ACTOR,
        "case": spec["case"],
        "reason": reason,
        "verified_business_truth": True,
        "timestamp": now,
        "ops": copy.deepcopy(ops),
    }
    return ops


def summarize(order: dict) -> Dict[str, Any]:
    audit = audit_order(order)
    return {
        "severity": audit.get("severity"),
        "po_qty": audit.get("po_qty"),
        "active_sj_qty": audit.get("active_sj_qty"),
        "loaded_qty": audit.get("loaded_qty"),
        "delivered_qty": audit.get("delivered_qty"),
        "remaining_delivery": audit.get("remaining_delivery"),
        "shipments": [
            {
                "id": canonical_shipment_id(row),
                "active": shipment_is_active(row),
                "qty": shipment_qty_total(row),
                "delivery_status": row.get("delivery_status"),
                "qty_delivered": row.get("qty_delivered"),
            }
            for row in order.get("shipments") or [] if isinstance(row, dict)
        ],
        "findings": [row.get("reason_code") for row in audit.get("findings") or []],
    }


def build_report(order: dict, spec: dict) -> dict:
    before_token = cas_token(order)
    working = copy.deepcopy(order)
    ops = apply_verified_repair(working, spec, now=_now())
    return {
        "ok": True,
        "case": spec["case"],
        "po_id": spec["po_id"],
        "dry_run": True,
        "read_only": True,
        "wrote": False,
        "cas": before_token,
        "before": summarize(order),
        "ops": ops,
        "after_if_applied": summarize(working),
        "working_document": working,
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair verified NUMANI or SPECIAL LALAP SJ history")
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", ""))
    parser.add_argument("--db-name", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cas", default="")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--backup-dir", default="/var/backups/eternal-packaging/sj-integrity")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    load_dotenv(ROOT / ".env")
    mongo_url = args.mongo_url or os.environ.get("MONGO_URL")
    db_name = args.db_name or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL/DB_NAME required")
    if args.apply and (not args.db_name or not args.cas or args.confirm != CONFIRM_PHRASE):
        print(json.dumps({"ok": False, "wrote": False, "error": "apply requires explicit --db-name, --cas, and confirmation phrase"}, indent=2))
        return 2

    from pymongo import MongoClient

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
    report.pop("working_document", None)
    if not args.apply:
        print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
        return 0
    if args.cas != report["cas"]:
        print(json.dumps({"ok": False, "wrote": False, "error": "CAS mismatch", "current_cas": report["cas"]}, indent=2))
        return 2

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{args.case}-{spec['po_id']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    from bson import json_util

    backup.write_text(json_util.dumps(order, indent=2, ensure_ascii=False), encoding="utf-8")
    working = copy.deepcopy(order)
    apply_verified_repair(working, spec, now=_now())
    selector = {"_id": order["_id"]}
    if "updated_at" in order:
        selector["updated_at"] = order["updated_at"]
    result = db.orders.replace_one(selector, working)
    if result.matched_count != 1:
        print(json.dumps({"ok": False, "wrote": False, "error": "atomic CAS write mismatch", "backup": str(backup)}, indent=2))
        return 2
    final = db.orders.find_one({"_id": order["_id"]})
    print(json.dumps({"ok": True, "wrote": True, "case": args.case, "backup": str(backup), "after": summarize(final)}, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
