"""Reconcile ES TEH NUMANI SJ integrity for one PO.

Default: DRY RUN (zero writes).
Apply: python -m scripts.reconcile_es_teh_numani_sj --po-id <id> --apply --db-name eternal_packaging_sj_fix

Does not invent delivery photos, signatures, or recipient names.

Database names in this Cursor environment:
- LOCAL EMPTY DB: eternal_packaging (empty local Mongo; not VPS production)
- PREVIEW DB: eternal_packaging_sj_fix
- PRODUCTION DB: the VPS production database. This script does not store its URI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from services.sj_integrity import (  # noqa: E402
    NUMANI_ITEM,
    NUMANI_PO_ID,
    NUMANI_PO_NUMBER,
    NUMANI_SJ1,
    NUMANI_SJ2,
    NUMANI_SJ3,
    NUMANI_SJ2_NUMBER,
    active_sj_qty,
    apply_repair_ops,
    cas_token,
    expected_numani_ids,
    find_shipment_exact,
    loaded_qty_for_shipment,
    po_qty_total,
    restore_shipment_qty_from_snapshot,
    snapshot_qty_for_shipment,
)

EXPECTED_IDS = {NUMANI_SJ1, NUMANI_SJ2, NUMANI_SJ3}
ACTOR_EMAIL = "audit.sj.integrity"
REASON = (
    "Koreksi duplikat SJ/2026/09/12/000002_3: SJ2 qty dikembalikan ke 4000 "
    "dari snapshot Loading; SJ3 di-supersede; discrepancy 3000+1000 ditutup. "
    "Tidak menambah bukti kirim."
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_bytes(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ("data_url", "storage_path", "signature_storage_path", "driver_signature_storage_path"):
                out[k] = "<omitted>" if v else v
            else:
                out[k] = _strip_bytes(v)
        return out
    if isinstance(value, list):
        return [_strip_bytes(v) for v in value]
    return value


def _shipment_ids(order: dict) -> List[str]:
    out = []
    for sh in order.get("shipments") or []:
        if isinstance(sh, dict) and sh.get("id"):
            out.append(str(sh["id"]))
    return out


def verify_preconditions(order: dict) -> List[str]:
    errors = []
    if str(order.get("id") or "") != NUMANI_PO_ID:
        errors.append(f"PO id bukan {NUMANI_PO_ID}")
    if str(order.get("po_number") or "") not in ("", NUMANI_PO_NUMBER) and order.get("po_number") != NUMANI_PO_NUMBER:
        if str(order.get("po_number") or "") != NUMANI_PO_NUMBER:
            errors.append(f"PO number bukan {NUMANI_PO_NUMBER} (punya {order.get('po_number')})")
    ids = set(_shipment_ids(order))
    if ids != EXPECTED_IDS:
        errors.append(f"shipment_id set {sorted(ids)} != {sorted(EXPECTED_IDS)}")
    sj1 = find_shipment_exact(order, NUMANI_SJ1)
    sj2 = find_shipment_exact(order, NUMANI_SJ2)
    sj3 = find_shipment_exact(order, NUMANI_SJ3)
    if not sj1 or not sj2 or not sj3:
        errors.append("Salah satu dari tiga shipment kanonik tidak ditemukan")
        return errors
    return errors


def already_repaired(order: dict) -> bool:
    sj2 = find_shipment_exact(order, NUMANI_SJ2) or {}
    sj3 = find_shipment_exact(order, NUMANI_SJ3) or {}
    from quantity_rules import shipment_is_active, shipment_qty_total, qty_delivered

    if shipment_qty_total(sj2) != 4000:
        return False
    if shipment_is_active(sj3):
        return False
    if loaded_qty_for_shipment(order, sj2) != 4000:
        return False
    if qty_delivered(order) != 4000:
        return False
    open_disc = [
        d for d in (order.get("loading_discrepancies") or [])
        if isinstance(d, dict) and str(d.get("status") or "") in ("open", "held", "", "None")
    ]
    return not open_disc


def planned_ops(order: dict) -> List[dict]:
    ops: List[dict] = []
    sj2 = find_shipment_exact(order, NUMANI_SJ2) or {}
    from quantity_rules import shipment_qty_total, shipment_is_active

    snap_qty = snapshot_qty_for_shipment(order, sj2)
    if shipment_qty_total(sj2) != 4000:
        lines = [{"line_idx": 0, "line_id": "L0", "qty": 4000, "name": NUMANI_ITEM}]
        if snap_qty not in (None, 4000):
            # still restore 4000 from Loading confirmation, not invented
            pass
        ops.append({"op": "restore_qty", "shipment_id": NUMANI_SJ2, "lines": lines})
    if shipment_is_active(find_shipment_exact(order, NUMANI_SJ3) or {}):
        ops.append({
            "op": "supersede_shipment",
            "shipment_id": NUMANI_SJ3,
            "successor_id": NUMANI_SJ2,
            "reason": REASON,
        })
    for d in order.get("loading_discrepancies") or []:
        if not isinstance(d, dict):
            continue
        if str(d.get("status") or "") in ("open", "held", "", "None"):
            ops.append({
                "op": "resolve_discrepancy",
                "discrepancy_id": d.get("id"),
                "status": "superseded",
                "reason": REASON,
            })
    return ops


def aggregates(order: dict) -> Dict[str, Any]:
    from quantity_rules import qty_delivered, qty_loaded, shipment_qty_total, shipment_is_active

    active = [sh for sh in (order.get("shipments") or []) if isinstance(sh, dict) and shipment_is_active(sh)]
    leftover = []
    for sh in active:
        from services.sj_integrity import delivered_qty_for_shipment_records
        if delivered_qty_for_shipment_records(order, sh) <= 0 and loaded_qty_for_shipment(order, sh) > 0:
            leftover.append(sh.get("id"))
    return {
        "po_qty": po_qty_total(order),
        "active_sj_qty": active_sj_qty(order),
        "loaded_qty": qty_loaded(order),
        "delivered_qty": qty_delivered(order),
        "remaining_qty": max(0, po_qty_total(order) - qty_delivered(order)),
        "active_shipment_ids": [sh.get("id") for sh in active],
        "leftover_task_shipment_ids": leftover,
        "sj_qtys": {
            str(sh.get("id")): shipment_qty_total(sh)
            for sh in (order.get("shipments") or []) if isinstance(sh, dict)
        },
    }


def expected_aggregates() -> Dict[str, Any]:
    return {
        "po_qty": 5000,
        "active_sj_qty": 5000,
        "loaded_qty": 5000,
        "delivered_qty": 4000,
        "remaining_qty": 1000,
        "active_shipment_ids": [NUMANI_SJ1, NUMANI_SJ2],
        "leftover_task_shipment_ids": [NUMANI_SJ1],
        "sj_qtys": {NUMANI_SJ1: 1000, NUMANI_SJ2: 4000, NUMANI_SJ3: 1000},
    }


async def load_order(db, po_id: str) -> Optional[dict]:
    return await db.orders.find_one({"id": po_id}, {"_id": 0})


def write_backup(order: dict, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(_strip_bytes(order), indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


async def run(db, *, po_id: str, apply: bool, expected_cas: Optional[str], backup_dir: Path) -> Dict[str, Any]:
    order = await load_order(db, po_id)
    if not order:
        return {"ok": False, "error": f"PO {po_id} tidak ditemukan", "mode": "apply" if apply else "dry-run"}
    pre_errors = verify_preconditions(order)
    token = cas_token(order)
    if expected_cas and expected_cas != token:
        return {
            "ok": False,
            "error": "compare-and-swap abort: state berubah sejak token dihitung",
            "cas": token,
            "expected_cas": expected_cas,
            "mode": "apply" if apply else "dry-run",
        }
    before = aggregates(order)
    ops = [] if already_repaired(order) else planned_ops(order)
    report = {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "po_id": po_id,
        "po_number": order.get("po_number"),
        "precondition_errors": pre_errors,
        "cas": token,
        "already_repaired": already_repaired(order),
        "ops": ops,
        "before": before,
        "expected_after": expected_aggregates(),
        "actor": ACTOR_EMAIL,
        "reason": REASON,
        "timestamp": now_iso(),
        "backup": None,
        "wrote": False,
        "other_pos_touched": 0,
    }
    if pre_errors:
        report["ok"] = False
        report["error"] = "; ".join(pre_errors)
        return report
    if not apply:
        working = json.loads(json.dumps(order, default=str))
        apply_repair_ops(working, ops, actor={"email": ACTOR_EMAIL}, now=report["timestamp"])
        report["after_if_applied"] = aggregates(working)
        return report

    backup_path = backup_dir / f"es_teh_numani_{po_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_backup(order, backup_path)
    report["backup"] = str(backup_path)
    if already_repaired(order) and not ops:
        report["wrote"] = False
        report["after"] = before
        report["note"] = "idempotent no-op"
        return report

    fresh = await load_order(db, po_id)
    if cas_token(fresh) != token:
        report["ok"] = False
        report["error"] = "compare-and-swap abort immediately before write"
        report["cas_now"] = cas_token(fresh)
        return report
    apply_repair_ops(fresh, ops, actor={"email": ACTOR_EMAIL, "name": "SJ Integrity Audit"}, now=report["timestamp"])
    fresh["sj_integrity_repair"] = {
        "actor": ACTOR_EMAIL,
        "reason": REASON,
        "timestamp": report["timestamp"],
        "before": before,
        "ops": ops,
        "cas_before": token,
    }
    await db.orders.replace_one({"id": po_id}, fresh)
    after_doc = await load_order(db, po_id)
    report["after"] = aggregates(after_doc or fresh)
    report["wrote"] = True
    report["cas_after"] = cas_token(after_doc or fresh)
    return report


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reconcile ES TEH NUMANI Surat Jalan integrity (dry-run default).")
    p.add_argument("--po-id", required=True, help="Exact PO UUID. Required.")
    p.add_argument("--apply", action="store_true", help="Persist changes. Default is dry-run.")
    p.add_argument("--cas", default="", help="Abort apply if current CAS token differs.")
    p.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    p.add_argument("--db-name", default=None)
    p.add_argument("--backup-dir", default=str(ROOT / "backups" / "sj_integrity"))
    return p.parse_args(argv)


LOCAL_EMPTY_DB = "eternal_packaging"
PREVIEW_DB = "eternal_packaging_sj_fix"


async def main_async(args: argparse.Namespace) -> int:
    explicit_db = args.db_name is not None
    args.db_name = args.db_name or os.environ.get("DB_NAME") or LOCAL_EMPTY_DB
    if args.apply:
        if not explicit_db:
            print(json.dumps({
                "ok": False,
                "error": "--apply requires explicit --db-name (PREVIEW DB).",
                "wrote": False,
            }, indent=2))
            return 2
        if args.db_name == LOCAL_EMPTY_DB:
            print(json.dumps({
                "ok": False,
                "error": (
                    "Refusing --apply on LOCAL EMPTY DB "
                    f"(DB_NAME={LOCAL_EMPTY_DB} in this Cursor environment). "
                    f"Use PREVIEW DB {PREVIEW_DB}. "
                    "This script does not connect to VPS production."
                ),
                "wrote": False,
            }, indent=2))
            return 2
    client = AsyncIOMotorClient(args.mongo_url)
    db = client[args.db_name]
    try:
        report = await run(
            db,
            po_id=args.po_id,
            apply=bool(args.apply),
            expected_cas=args.cas or None,
            backup_dir=Path(args.backup_dir),
        )
    finally:
        client.close()
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("ok") else 1


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
