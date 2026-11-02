"""One-time historical reconciliation into sales_customers.

Default mode is dry-run: fully zero-write (orders / sales_customers /
``sales_customer_migration_runs`` unchanged). Apply and rollback write
durable audit rows. Apply requires explicit Owner approval.

Never invoked from server startup.
Does NOT mutate orders, invoices, AR, Accurate, inventory, or journals.
Does NOT replace the entire sales_customers collection.
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pymongo.errors import DuplicateKeyError

from services.customer_identity import (
    build_identity_context,
    display_customer_name,
    normalize_customer_name,
    resolve_customer_identity,
)
from services.sales_customers import COLLECTION, now_iso

logger = logging.getLogger(__name__)

OWNER_APPROVAL_ENV = "SALES_CUSTOMER_MIGRATE_OWNER_APPROVED"
OWNER_APPROVAL_VALUE = "YES"
RUNS_COLLECTION = "sales_customer_migration_runs"
MIGRATION_ACTOR = "historical_migration"

_ORDER_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
    "customer_phone": 1,
    "customer_pic": 1,
    "customer_email": 1,
    "customer_city": 1,
    "address": 1,
    "recipient": 1,
    "outlet": 1,
    "cancelled": 1,
    "is_test": 1,
    "is_demo": 1,
    "is_preview": 1,
    "test_record": 1,
    "created_at": 1,
}

_HISTORY_PROJECTION = {
    "_id": 0,
    "id": 1,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
    "customer_phone": 1,
    "address": 1,
    "outlet": 1,
    "cancelled": 1,
    "is_test": 1,
    "is_demo": 1,
    "is_preview": 1,
    "test_record": 1,
}


def _usable_identity_record(record: dict) -> bool:
    if not record:
        return False
    if record.get("cancelled") is True:
        return False
    if record.get("is_test") or record.get("is_demo") or record.get("is_preview") or record.get("test_record"):
        return False
    return bool(display_customer_name(record))


def _profile_from_record(record: dict) -> Dict[str, str]:
    phone = str(record.get("customer_phone") or record.get("phone") or "").strip()
    address = str(record.get("address") or record.get("customer_address") or "").strip()
    pic = (
        str(record.get("customer_pic") or "").strip()
        or str(record.get("pic") or "").strip()
        or str(record.get("recipient") or "").strip()
    )
    email = str(record.get("customer_email") or record.get("email") or "").strip()
    city = str(record.get("customer_city") or record.get("city") or "").strip()
    name = display_customer_name(record)
    return {
        "name": name,
        "phone": phone,
        "address": address,
        "pic": pic,
        "email": email,
        "city": city,
    }


def _profile_complete(profile: dict) -> bool:
    return bool(str(profile.get("name") or "").strip()) and bool(
        str(profile.get("phone") or "").strip()
    ) and bool(str(profile.get("address") or "").strip())


@dataclass
class MigrationRow:
    action: str
    customer_key: str = ""
    normalized_name: str = ""
    display_name: str = ""
    proposed_id: Optional[str] = None
    existing_id: Optional[str] = None
    reason: str = ""
    source: str = ""
    sample_refs: List[str] = field(default_factory=list)
    seed_profile: Optional[Dict[str, str]] = None


@dataclass
class MigrationReport:
    mode: str
    batch_id: str = ""
    created: List[MigrationRow] = field(default_factory=list)
    skipped: List[MigrationRow] = field(default_factory=list)
    ambiguous: List[MigrationRow] = field(default_factory=list)
    incomplete: List[MigrationRow] = field(default_factory=list)
    errors: List[MigrationRow] = field(default_factory=list)
    excluded: int = 0
    backup_collection: Optional[str] = None
    applied: bool = False
    rolled_back: List[str] = field(default_factory=list)
    rollback_skipped: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "batch_id": self.batch_id,
            "applied": self.applied,
            "backup_collection": self.backup_collection,
            "counts": {
                "created": len(self.created),
                "skipped": len(self.skipped),
                "ambiguous": len(self.ambiguous),
                "incomplete": len(self.incomplete),
                "errors": len(self.errors),
                "excluded": self.excluded,
                "rolled_back": len(self.rolled_back),
                "rollback_skipped": len(self.rollback_skipped),
            },
            "created": [asdict(r) for r in self.created],
            "skipped": [asdict(r) for r in self.skipped],
            "ambiguous": [asdict(r) for r in self.ambiguous],
            "incomplete": [asdict(r) for r in self.incomplete],
            "errors": [asdict(r) for r in self.errors],
            "rolled_back": list(self.rolled_back),
            "rollback_skipped": list(self.rollback_skipped),
        }


def assert_owner_approved(owner_approved: bool, env_value: Optional[str] = None) -> None:
    """Apply/rollback requires explicit owner_approved AND matching env token."""
    import os

    env = (env_value if env_value is not None else os.environ.get(OWNER_APPROVAL_ENV) or "").strip()
    if not owner_approved or env != OWNER_APPROVAL_VALUE:
        raise RuntimeError(
            "Operasi ditolak: butuh flag Owner (--owner-approved) dan "
            f"env {OWNER_APPROVAL_ENV}={OWNER_APPROVAL_VALUE}."
        )


def new_batch_id() -> str:
    return f"mig-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


async def ensure_migration_indexes(db) -> None:
    # One audit row per run (id unique). batch_id is shared across dry-run /
    # apply / rollback entries for the same migration batch — not unique.
    await db[RUNS_COLLECTION].create_index("id", unique=True)
    await db[RUNS_COLLECTION].create_index([("batch_id", 1), ("created_at", -1)])
    await db[RUNS_COLLECTION].create_index("created_at")
    await db[COLLECTION].create_index("migration_batch_id")
    # Drop legacy unique batch_id index if present from earlier drafts.
    try:
        info = await db[RUNS_COLLECTION].index_information()
        for name, meta in info.items():
            keys = meta.get("key") or []
            if meta.get("unique") and keys == [("batch_id", 1)]:
                await db[RUNS_COLLECTION].drop_index(name)
                break
    except Exception:  # noqa: BLE001
        logger.debug("sales_customer_migration_runs index cleanup skipped", exc_info=True)


async def _load_sources(db) -> Tuple[List[dict], List[dict], List[dict], int]:
    masters = await db[COLLECTION].find({}, {"_id": 0}).to_list(20000)
    orders_raw = await db.orders.find({}, _ORDER_PROJECTION).to_list(20000)
    history_raw = await db.order_prediction_history.find(
        {"cancelled": {"$ne": True}}, _HISTORY_PROJECTION,
    ).to_list(20000)
    excluded = 0
    orders: List[dict] = []
    for o in orders_raw:
        if _usable_identity_record(o):
            orders.append(o)
        else:
            excluded += 1
    history: List[dict] = []
    for h in history_raw:
        if _usable_identity_record(h):
            history.append(h)
        else:
            excluded += 1
    return masters, orders, history, excluded


def _bucket_candidates(
    masters: Sequence[dict],
    orders: Sequence[dict],
    history: Sequence[dict],
) -> Dict[str, dict]:
    context = build_identity_context(masters, orders, history)
    buckets: Dict[str, dict] = {}

    for master in masters:
        resolved = resolve_customer_identity(master, context, kind="master")
        if not resolved.key:
            continue
        buckets[resolved.key] = {
            "kind": "existing_master",
            "resolved": resolved,
            "master": master,
            "records": [],
        }

    for rec, src in [(o, "live_po") for o in orders] + [(h, "prediction_history") for h in history]:
        resolved = resolve_customer_identity(rec, context)
        if not resolved.key:
            continue
        entry = buckets.get(resolved.key)
        if entry and entry.get("kind") == "existing_master":
            entry["records"].append((src, rec))
            continue
        if resolved.ambiguous:
            if not entry:
                buckets[resolved.key] = {
                    "kind": "ambiguous",
                    "resolved": resolved,
                    "records": [(src, rec)],
                }
            else:
                entry["records"].append((src, rec))
            continue
        if not entry:
            buckets[resolved.key] = {
                "kind": "candidate",
                "resolved": resolved,
                "records": [(src, rec)],
            }
        else:
            entry["records"].append((src, rec))
    return buckets


def _pick_seed_profile(records: Sequence[Tuple[str, dict]]) -> Tuple[Optional[dict], str]:
    incomplete = None
    incomplete_src = ""
    for src, rec in records:
        profile = _profile_from_record(rec)
        if _profile_complete(profile):
            return profile, src
        if incomplete is None and profile.get("name"):
            incomplete = profile
            incomplete_src = src
    return incomplete, incomplete_src


def _sample_refs(records: Sequence[Tuple[str, dict]], limit: int = 5) -> List[str]:
    out = []
    for src, rec in records[:limit]:
        ref = str(rec.get("po_number") or rec.get("id") or "").strip() or "?"
        out.append(f"{src}:{ref}")
    return out


def _build_plan_rows(
    masters: Sequence[dict],
    orders: Sequence[dict],
    history: Sequence[dict],
    excluded: int,
) -> MigrationReport:
    buckets = _bucket_candidates(masters, orders, history)
    report = MigrationReport(mode="dry-run", excluded=excluded)
    masters_by_norm: Dict[str, List[dict]] = {}
    for m in masters:
        norm = normalize_customer_name(m.get("name") or "")
        if norm:
            masters_by_norm.setdefault(norm, []).append(m)

    for key, entry in sorted(buckets.items(), key=lambda kv: kv[0]):
        resolved = entry["resolved"]
        records = entry.get("records") or []

        if entry["kind"] == "existing_master":
            report.skipped.append(MigrationRow(
                action="skipped_existing",
                customer_key=key,
                normalized_name=resolved.normalized_name,
                display_name=resolved.display_name,
                existing_id=str((entry.get("master") or {}).get("id") or "") or None,
                reason="Master sudah ada; profil tidak ditimpa.",
                source="master",
                sample_refs=_sample_refs(records),
            ))
            continue

        if entry["kind"] == "ambiguous" or resolved.ambiguous:
            report.ambiguous.append(MigrationRow(
                action="ambiguous",
                customer_key=key,
                normalized_name=resolved.normalized_name,
                display_name=resolved.display_name,
                reason="Identitas ambigu; review Operasional, jangan auto-merge.",
                source="mixed",
                sample_refs=_sample_refs(records),
            ))
            continue

        norm = resolved.normalized_name
        if norm and masters_by_norm.get(norm):
            existing = masters_by_norm[norm][0]
            report.skipped.append(MigrationRow(
                action="skipped_existing_name",
                customer_key=key,
                normalized_name=norm,
                display_name=resolved.display_name,
                existing_id=str(existing.get("id") or "") or None,
                reason="normalized_name sudah ada di master.",
                source=records[0][0] if records else "",
                sample_refs=_sample_refs(records),
            ))
            continue

        profile, src = _pick_seed_profile(records)
        if not profile or not _profile_complete(profile):
            report.incomplete.append(MigrationRow(
                action="incomplete_profile",
                customer_key=key,
                normalized_name=norm,
                display_name=resolved.display_name,
                reason="Profil tidak lengkap (nama/HP/alamat); review Operasional.",
                source=src or (records[0][0] if records else ""),
                sample_refs=_sample_refs(records),
            ))
            continue

        proposed_id = resolved.stable_id or str(uuid.uuid4())
        report.created.append(MigrationRow(
            action="would_create",
            customer_key=key,
            normalized_name=norm,
            display_name=profile["name"],
            proposed_id=proposed_id,
            reason="Kandidat master baru dari profil administrasi pertama yang valid.",
            source=src,
            sample_refs=_sample_refs(records),
            seed_profile=profile,
        ))
    return report


async def _write_run_audit(db, report: MigrationReport, *, status: str, extra: Optional[dict] = None) -> None:
    """Durable audit for migration runs. Does not touch sales_customers / orders.

    Each call inserts a new run row (unique ``id``). Multiple rows may share
    the same ``batch_id`` (dry-run → apply → rollback).
    """
    await ensure_migration_indexes(db)
    doc = {
        "id": str(uuid.uuid4()),
        "batch_id": report.batch_id,
        "mode": report.mode,
        "status": status,
        "created_at": now_iso(),
        "applied": report.applied,
        "backup_collection": report.backup_collection,
        "counts": report.to_dict()["counts"],
        "created_ids": [r.proposed_id for r in report.created if r.proposed_id and r.action == "created"],
        "would_create_ids": [r.proposed_id for r in report.created if r.proposed_id and r.action == "would_create"],
        "report": report.to_dict(),
    }
    if extra:
        doc.update(extra)
    await db[RUNS_COLLECTION].insert_one(doc)


async def plan_historical_customer_migration(
    db,
    *,
    batch_id: Optional[str] = None,
    write_audit: bool = False,
) -> MigrationReport:
    """Plan only. Fully zero-write by default (masters, orders, and audit runs).

    Apply/rollback keep durable audit separately. Opt-in ``write_audit=True``
    may record a dry-run row in sales_customer_migration_runs (ops metadata).
    """
    masters, orders, history, excluded = await _load_sources(db)
    report = _build_plan_rows(masters, orders, history, excluded)
    report.batch_id = batch_id or new_batch_id()
    report.mode = "dry-run"
    report.applied = False
    if write_audit:
        await _write_run_audit(db, report, status="dry_run")
    return report


async def backup_sales_customers(db, *, suffix: Optional[str] = None, batch_id: Optional[str] = None) -> str:
    """Point-in-time copy for disaster recovery — never used as wholesale restore."""
    stamp = suffix or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"sales_customers_backup_{stamp}"
    docs = await db[COLLECTION].find({}).to_list(50000)
    if docs:
        payload = []
        for d in docs:
            row = copy.deepcopy(d)
            row.pop("_id", None)
            if batch_id:
                row["_backup_batch_id"] = batch_id
            payload.append(row)
        if payload:
            await db[name].insert_many(payload)
    await db[name].create_index("id", unique=True)
    return name


def _doc_unmodified_since_migration(doc: dict, batch_id: str) -> bool:
    """True only when the master was created by this batch and never edited later."""
    if str(doc.get("migration_batch_id") or "") != batch_id:
        return False
    if str(doc.get("created_by") or "") != MIGRATION_ACTOR:
        return False
    if doc.get("updated_by"):
        return False
    # updated_at must still match created_at (no later profile edit).
    return str(doc.get("updated_at") or "") == str(doc.get("created_at") or "")


async def apply_historical_customer_migration(
    db,
    *,
    owner_approved: bool = False,
    env_value: Optional[str] = None,
    create_backup: bool = True,
    batch_id: Optional[str] = None,
) -> MigrationReport:
    """Apply planned creates. Idempotent / resume-safe / concurrency-safe."""
    assert_owner_approved(owner_approved, env_value=env_value)
    await ensure_migration_indexes(db)

    bid = batch_id or new_batch_id()
    masters, orders, history, excluded = await _load_sources(db)
    plan = _build_plan_rows(masters, orders, history, excluded)
    plan.batch_id = bid
    plan.mode = "apply"
    plan.applied = True

    if create_backup:
        plan.backup_collection = await backup_sales_customers(db, batch_id=bid)

    created_rows: List[MigrationRow] = []
    for row in plan.created:
        try:
            if row.proposed_id:
                existing_id = await db[COLLECTION].find_one({"id": row.proposed_id}, {"_id": 0, "id": 1})
                if existing_id:
                    plan.skipped.append(MigrationRow(
                        action="skipped_existing",
                        customer_key=row.customer_key,
                        normalized_name=row.normalized_name,
                        display_name=row.display_name,
                        existing_id=existing_id["id"],
                        reason="Sudah ada saat apply (resume/concurrent).",
                        source=row.source,
                        sample_refs=row.sample_refs,
                    ))
                    continue
            if row.normalized_name:
                existing_norm = await db[COLLECTION].find_one(
                    {"normalized_name": row.normalized_name}, {"_id": 0, "id": 1, "name": 1},
                )
                if existing_norm:
                    plan.skipped.append(MigrationRow(
                        action="skipped_existing_name",
                        customer_key=row.customer_key,
                        normalized_name=row.normalized_name,
                        display_name=row.display_name,
                        existing_id=existing_norm.get("id"),
                        reason="normalized_name bentrok saat apply; preserve existing.",
                        source=row.source,
                        sample_refs=row.sample_refs,
                    ))
                    continue

            profile = dict(row.seed_profile or {})
            if not _profile_complete(profile):
                plan.incomplete.append(MigrationRow(
                    action="incomplete_profile",
                    customer_key=row.customer_key,
                    normalized_name=row.normalized_name,
                    display_name=row.display_name,
                    reason="Profil tidak lengkap saat apply; dilewati.",
                    source=row.source,
                    sample_refs=row.sample_refs,
                ))
                continue

            now = now_iso()
            doc = {
                "id": row.proposed_id or str(uuid.uuid4()),
                "name": profile["name"],
                "normalized_name": normalize_customer_name(profile["name"]),
                "phone": profile["phone"],
                "address": profile["address"],
                "pic": profile.get("pic") or "",
                "email": profile.get("email") or "",
                "city": profile.get("city") or "",
                "notes": f"historical_migration:{bid}",
                "created_at": now,
                "updated_at": now,
                "created_by": MIGRATION_ACTOR,
                "migration_batch_id": bid,
            }

            try:
                await db[COLLECTION].insert_one(doc)
            except DuplicateKeyError:
                winner = await db[COLLECTION].find_one(
                    {"normalized_name": doc["normalized_name"]}, {"_id": 0, "id": 1},
                )
                plan.skipped.append(MigrationRow(
                    action="skipped_race_winner",
                    customer_key=row.customer_key,
                    normalized_name=row.normalized_name,
                    display_name=row.display_name,
                    existing_id=(winner or {}).get("id"),
                    reason="DuplicateKeyError; winner preserved.",
                    source=row.source,
                    sample_refs=row.sample_refs,
                ))
                continue

            created_rows.append(MigrationRow(
                action="created",
                customer_key=row.customer_key,
                normalized_name=doc["normalized_name"],
                display_name=doc["name"],
                proposed_id=doc["id"],
                reason="Master historis dibuat.",
                source=row.source,
                sample_refs=row.sample_refs,
                seed_profile=profile,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.exception("historical customer migration row failed: %s", row.customer_key)
            plan.errors.append(MigrationRow(
                action="error",
                customer_key=row.customer_key,
                normalized_name=row.normalized_name,
                display_name=row.display_name,
                reason=str(exc),
                source=row.source,
                sample_refs=row.sample_refs,
            ))

    plan.created = created_rows
    await _write_run_audit(db, plan, status="applied")
    return plan


async def rollback_historical_customer_migration(
    db,
    batch_id: str,
    *,
    owner_approved: bool = False,
    env_value: Optional[str] = None,
) -> MigrationReport:
    """Selective rollback: delete only unmodified masters created by this batch.

    Never drops or replaces the sales_customers collection. Masters created by
    later operational PO flows (no migration_batch_id / different actor) are
    protected. Masters edited after migration are skipped.
    """
    assert_owner_approved(owner_approved, env_value=env_value)
    bid = str(batch_id or "").strip()
    if not bid:
        raise RuntimeError("batch_id wajib untuk rollback selektif.")

    await ensure_migration_indexes(db)
    report = MigrationReport(mode="rollback", batch_id=bid, applied=False)

    run = await db[RUNS_COLLECTION].find_one(
        {"batch_id": bid, "status": "applied"},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    created_ids = list((run or {}).get("created_ids") or [])
    if not created_ids:
        # Fallback: discover by stamp on masters
        cursor = db[COLLECTION].find({"migration_batch_id": bid}, {"_id": 0, "id": 1})
        created_ids = [d["id"] for d in await cursor.to_list(20000) if d.get("id")]

    for cid in created_ids:
        doc = await db[COLLECTION].find_one({"id": cid}, {"_id": 0})
        if not doc:
            report.rollback_skipped.append({"id": cid, "reason": "not_found"})
            continue
        if not _doc_unmodified_since_migration(doc, bid):
            report.rollback_skipped.append({
                "id": cid,
                "reason": "protected_or_modified",
                "created_by": str(doc.get("created_by") or ""),
                "migration_batch_id": str(doc.get("migration_batch_id") or ""),
            })
            continue
        await db[COLLECTION].delete_one({
            "id": cid,
            "migration_batch_id": bid,
            "created_by": MIGRATION_ACTOR,
        })
        # Confirm gone
        still = await db[COLLECTION].find_one({"id": cid}, {"_id": 0, "id": 1})
        if still:
            report.rollback_skipped.append({"id": cid, "reason": "delete_raced_or_blocked"})
        else:
            report.rolled_back.append(cid)

    await _write_run_audit(
        db,
        report,
        status="rolled_back",
        extra={"rolled_back_ids": report.rolled_back, "rollback_skipped": report.rollback_skipped},
    )
    # Mark original apply run
    await db[RUNS_COLLECTION].update_one(
        {"batch_id": bid, "status": "applied"},
        {"$set": {
            "rolled_back_at": now_iso(),
            "rollback_result": {
                "rolled_back": report.rolled_back,
                "skipped": report.rollback_skipped,
            },
        }},
    )
    return report


def dump_report(report: MigrationReport, path: Optional[str] = None) -> str:
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload)
    return payload
