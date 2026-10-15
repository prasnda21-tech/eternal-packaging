"""Purge unmatched bank statement lines for one account + period.

Does not touch journals, vouchers, GL, match documents, or import batches.
Physical delete of eligible sealed lines only. Uses the same per-account
import fence so purge and import cannot interleave writes.

Standalone Mongo: progress is recorded after each successful delete. Crash
between delete and progress write is reconciled under the fence using the
immutable eligible snapshot persisted before any deletion.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from services.cash_bank_master import get_cash_bank_raw
from services.cash_bank_reconciliation import RECON_MATCHES, require_claim_feature, ClaimFeatureNotReady
from services.cash_bank_statement import (
    DUP_CLEAR,
    DUP_KEPT,
    IMPORT_FENCES,
    IMPORTS,
    LINES,
    StatementConflictError,
    _acquire_import_fence,
    _release_import_fence,
    ensure_indexes as ensure_statement_indexes,
)
from services.purchase_common import actor_snapshot, now_iso

PURGES = "cash_bank_statement_line_purges"
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_CONFLICT = "conflict"  # terminal — do not auto-resume
STATUS_FAILED = "failed"  # transient — same-key recovery allowed
STATUS_REVIEW = "review_required"  # ambiguous outcome — manual review

_ELIGIBLE_DUP = frozenset({DUP_CLEAR, DUP_KEPT})
_JAKARTA = ZoneInfo("Asia/Jakarta")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Cap stored id lists; deleted_count remains the authoritative total.
MAX_STORED_LINE_IDS = 500


class PurgeValidationError(ValueError):
    pass


class PurgeConflictError(Exception):
    pass


async def ensure_purge_indexes(db) -> None:
    await ensure_statement_indexes(db)
    col = db[PURGES]
    await col.create_index("id", unique=True)
    await col.create_index(
        [("cash_bank_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="uniq_purge_idempotency",
    )
    await col.create_index([("cash_bank_id", 1), ("requested_at", -1)])


def _period(start: str, end: str) -> Tuple[str, str]:
    begin = str(start or "").strip()
    finish = str(end or "").strip()
    if len(begin) != 10 or len(finish) != 10 or begin > finish:
        raise PurgeValidationError("Periode tidak valid")
    if not _DATE_ONLY.match(begin) or not _DATE_ONLY.match(finish):
        raise PurgeValidationError("Periode tidak valid")
    return begin, finish


def _payload_hash(cash_bank_id: str, start: str, end: str) -> str:
    raw = "|".join([str(cash_bank_id), str(start), str(end)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _line_period_date(row: dict) -> Optional[str]:
    """Business calendar date for period scope (Asia/Jakarta for aware timestamps).

    Established cash-bank import stores YYYY-MM-DD on ``date``. Aware UTC values
    use Jakarta calendar date (same rule as BCA jakarta_import_date). Malformed
    or missing dates return None (fail-closed / out of scope).
    """
    raw = row.get("date")
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        moment = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
        return moment.astimezone(_JAKARTA).date().isoformat()
    text = str(raw).strip()
    if _DATE_ONLY.match(text):
        return text
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.date().isoformat()
        return parsed.astimezone(_JAKARTA).date().isoformat()
    return None


def _claim_absent_clause() -> Dict[str, Any]:
    """active_claim and claim_slot must be null/absent/empty — fail-closed otherwise."""
    return {
        "$and": [
            {"$or": [
                {"active_claim": None},
                {"active_claim": {"$exists": False}},
                {"active_claim": ""},
            ]},
            {"$or": [
                {"claim_slot": None},
                {"claim_slot": {"$exists": False}},
                {"claim_slot": ""},
            ]},
        ]
    }


def _is_ambiguous(row: dict) -> bool:
    if row.get("sealed") is False:
        return True
    if _line_period_date(row) is None:
        return True
    status = str(row.get("duplicate_status") or "")
    if status in ("unsealed",):
        return True
    if status not in _ELIGIBLE_DUP and status not in (
        "pending_review", "definite_duplicate", "excluded",
    ):
        return True
    auth = row.get("authoritative_txn_id")
    guarantee = str(row.get("identity_guarantee") or "")
    if auth and guarantee not in ("authoritative", "tidak_dijamin", ""):
        return True
    return False


async def _active_match_line_ids(db, line_ids: Sequence[str]) -> set:
    ids = [str(i) for i in line_ids if i]
    if not ids:
        return set()
    found = await db[RECON_MATCHES].find(
        {"statement_line_id": {"$in": ids}, "status": "active"},
        {"_id": 0, "statement_line_id": 1},
    ).to_list(len(ids) + 50)
    return {str(doc.get("statement_line_id")) for doc in found if doc.get("statement_line_id")}


async def _classify_lines(
    db, *, cash_bank_id: str, start: str, end: str,
) -> Dict[str, Any]:
    rows = await db[LINES].find(
        {"cash_bank_id": str(cash_bank_id), "sealed": {"$ne": False}},
        {
            "_id": 0,
            "id": 1,
            "import_id": 1,
            "date": 1,
            "direction": 1,
            "amount": 1,
            "duplicate_status": 1,
            "active_claim": 1,
            "claim_slot": 1,
            "sealed": 1,
            "authoritative_txn_id": 1,
            "identity_guarantee": 1,
            "bank_txn_id": 1,
            "row_number": 1,
        },
    ).to_list(100000)
    in_period: List[dict] = []
    for row in rows:
        biz = _line_period_date(row)
        if biz is None:
            # Missing/malformed date: only flag when the row looks otherwise in play.
            if row.get("date") not in (None, ""):
                in_period.append(row)  # will become ambiguous via _is_ambiguous
            continue
        if start <= biz <= end:
            in_period.append(row)
    matched_ids = await _active_match_line_ids(db, [r.get("id") for r in in_period])
    eligible: List[dict] = []
    protected: List[dict] = []
    ambiguous: List[dict] = []
    for row in in_period:
        lid = str(row.get("id") or "")
        if not lid:
            ambiguous.append(row)
            continue
        if _is_ambiguous(row):
            ambiguous.append(row)
            continue
        status = str(row.get("duplicate_status") or "")
        if status not in _ELIGIBLE_DUP:
            protected.append(row)
            continue
        claim = row.get("active_claim")
        slot = row.get("claim_slot")
        if (claim not in (None, "")) or (slot not in (None, "")):
            protected.append(row)
            continue
        if lid in matched_ids:
            protected.append(row)
            continue
        eligible.append(row)
    return {
        "eligible": eligible,
        "protected": protected,
        "ambiguous": ambiguous,
        "eligible_ids": [str(r["id"]) for r in eligible],
        "import_ids": sorted({str(r.get("import_id")) for r in eligible if r.get("import_id")}),
    }


async def preview_unmatched_purge(
    db, *, cash_bank_id: str, start_date: str, end_date: str, user: Optional[dict] = None,
) -> dict:
    """Read-only classification. Zero writes."""
    del user
    start, end = _period(start_date, end_date)
    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise PurgeValidationError("Kas/Bank tidak ditemukan")
    classified = await _classify_lines(db, cash_bank_id=cash_bank_id, start=start, end=end)
    fence = await db[IMPORT_FENCES].find_one({"cash_bank_id": str(cash_bank_id)}, {"_id": 0})
    fence_busy = bool(fence and fence.get("holder"))
    return {
        "cash_bank_id": str(cash_bank_id),
        "cash_bank_name": cb.get("name"),
        "start_date": start,
        "end_date": end,
        "eligible_count": len(classified["eligible"]),
        "protected_count": len(classified["protected"]),
        "ambiguous_count": len(classified["ambiguous"]),
        "fence_busy": fence_busy,
        "note": (
            "Jurnal, General Ledger, dan transaksi yang sudah cocok tidak diubah. "
            "Hanya mutasi rekening koran belum cocok yang eligible yang dapat dihapus."
        ),
        "zero_write": True,
    }


def _public_purge(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "operation_id": doc.get("id"),
        "idempotency_key": doc.get("idempotency_key"),
        "cash_bank_id": doc.get("cash_bank_id"),
        "cash_bank_name": doc.get("cash_bank_name"),
        "start_date": doc.get("start_date"),
        "end_date": doc.get("end_date"),
        "status": doc.get("status"),
        "requested_at": doc.get("requested_at"),
        "committed_at": doc.get("committed_at"),
        "eligible_count": doc.get("eligible_count"),
        "deleted_count": doc.get("deleted_count"),
        "protected_count": doc.get("protected_count"),
        "ambiguous_count": doc.get("ambiguous_count"),
        "skipped_count": doc.get("skipped_count"),
        "conflicted_count": doc.get("conflicted_count"),
        "actor": doc.get("actor"),
        "role": doc.get("role"),
        "message": doc.get("message") or "",
        "gap_fill_import_ids": list(doc.get("gap_fill_import_ids") or []),
        "deleted_line_ids_capped": bool(doc.get("deleted_line_ids_capped")),
        "totals_indeterminate": bool(doc.get("totals_indeterminate")),
    }


def _cap_ids(ids: Sequence[str]) -> Tuple[List[str], bool]:
    unique: List[str] = []
    seen: Set[str] = set()
    for item in ids:
        sid = str(item)
        if not sid or sid in seen:
            continue
        seen.add(sid)
        if len(unique) < MAX_STORED_LINE_IDS:
            unique.append(sid)
    capped = len(seen) > MAX_STORED_LINE_IDS
    return unique, capped


async def _record_deleted_line(db, op_id: str, line_id: str) -> None:
    """Durable cumulative progress. Idempotent for the same line_id."""
    lid = str(line_id)
    current = await db[PURGES].find_one(
        {"id": op_id},
        {"_id": 0, "deleted_line_ids": 1, "deleted_count": 1, "status": 1},
    )
    if not current or current.get("status") not in (STATUS_PENDING, STATUS_FAILED):
        return
    ids = [str(x) for x in (current.get("deleted_line_ids") or [])]
    if lid in ids:
        return
    if len(ids) < MAX_STORED_LINE_IDS:
        await db[PURGES].update_one(
            {"id": op_id, "deleted_line_ids": {"$nin": [lid]}},
            {
                "$addToSet": {"deleted_line_ids": lid},
                "$inc": {"deleted_count": 1},
                "$set": {"progress_at": now_iso()},
            },
        )
        return
    await db[PURGES].update_one(
        {"id": op_id, "deleted_line_ids": {"$nin": [lid]}},
        {
            "$inc": {"deleted_count": 1},
            "$set": {
                "deleted_line_ids_capped": True,
                "progress_at": now_iso(),
            },
        },
    )


async def _persist_snapshot(db, op: dict, classified: dict) -> dict:
    """Immutable eligibility evidence before any deletion. Idempotent once set."""
    if op.get("eligible_snapshot_ids") is not None:
        return op
    eligible_ids = [str(x) for x in classified["eligible_ids"]]
    if len(eligible_ids) > MAX_STORED_LINE_IDS:
        # Fail closed: crash reconcile needs the full id set on standalone Mongo.
        await db[PURGES].update_one(
            {"id": op["id"], "status": STATUS_PENDING},
            {"$set": {
                "status": STATUS_CONFLICT,
                "eligible_count": len(eligible_ids),
                "protected_count": len(classified["protected"]),
                "ambiguous_count": len(classified["ambiguous"]),
                "committed_at": now_iso(),
                "message": (
                    f"Terlalu banyak baris eligible ({len(eligible_ids)}). "
                    f"Maksimum per operasi {MAX_STORED_LINE_IDS}; persempit periode."
                ),
            }},
        )
        refreshed = await db[PURGES].find_one({"id": op["id"]}, {"_id": 0})
        return refreshed or op
    ids, capped = _cap_ids(eligible_ids)
    import_ids = sorted({
        str(r.get("import_id"))
        for r in classified["eligible"]
        if r.get("import_id")
    })
    await db[PURGES].update_one(
        {"id": op["id"], "eligible_snapshot_ids": {"$exists": False}},
        {"$set": {
            "eligible_snapshot_ids": ids,
            "eligible_snapshot_capped": capped,
            "eligible_count": len(eligible_ids),
            "protected_count": len(classified["protected"]),
            "ambiguous_count": len(classified["ambiguous"]),
            "snapshot_import_ids": import_ids,
            "snapshot_at": now_iso(),
            "deleted_line_ids": list(op.get("deleted_line_ids") or []),
            "deleted_count": int(op.get("deleted_count") or 0),
        }},
    )
    refreshed = await db[PURGES].find_one({"id": op["id"]}, {"_id": 0})
    return refreshed or op


async def _reconcile_missing_after_crash(db, op: dict) -> dict:
    """Attribute lines from the snapshot that are gone but not yet recorded.

    Under the per-account fence only this operation deletes statement lines for
    the account. Missing snapshot members are therefore this op's deletes.
    If the snapshot was never persisted, fail closed — do not guess.
    """
    if op.get("eligible_snapshot_ids") is None:
        return op
    snapshot = [str(x) for x in (op.get("eligible_snapshot_ids") or [])]
    if not snapshot and int(op.get("eligible_count") or 0) > 0 and op.get("eligible_snapshot_capped"):
        # Snapshot list empty but count > 0 with cap flag is inconsistent → review.
        await db[PURGES].update_one(
            {"id": op["id"]},
            {"$set": {
                "status": STATUS_REVIEW,
                "totals_indeterminate": True,
                "message": (
                    "Snapshot eligible terpotong dan tidak dapat direkonsiliasi "
                    "setelah gangguan. Perlu tinjauan manual."
                ),
                "committed_at": now_iso(),
            }},
        )
        return await db[PURGES].find_one({"id": op["id"]}, {"_id": 0}) or op

    recorded = set(str(x) for x in (op.get("deleted_line_ids") or []))
    for lid in snapshot:
        if lid in recorded:
            continue
        still = await db[LINES].find_one({"id": lid}, {"_id": 0, "id": 1})
        if still is None:
            await _record_deleted_line(db, op["id"], lid)
    return await db[PURGES].find_one({"id": op["id"]}, {"_id": 0}) or op


async def execute_unmatched_purge(
    db,
    *,
    cash_bank_id: str,
    start_date: str,
    end_date: str,
    idempotency_key: str,
    user: Optional[dict] = None,
) -> dict:
    """Guarded purge under the per-account import fence."""
    start, end = _period(start_date, end_date)
    key = str(idempotency_key or "").strip()
    if not key or len(key) < 8:
        raise PurgeValidationError("Idempotency key wajib (minimal 8 karakter)")
    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise PurgeValidationError("Kas/Bank tidak ditemukan")
    try:
        await require_claim_feature(db)
    except ClaimFeatureNotReady as exc:
        raise PurgeConflictError(str(exc)) from exc

    payload = _payload_hash(cash_bank_id, start, end)
    existing = await db[PURGES].find_one(
        {"cash_bank_id": str(cash_bank_id), "idempotency_key": key},
        {"_id": 0},
    )
    if existing:
        if existing.get("payload_hash") != payload:
            raise PurgeConflictError(
                "Kunci idempotensi sama dipakai untuk rekening/periode berbeda"
            )
        status = existing.get("status")
        if status == STATUS_COMPLETED:
            return _public_purge(existing)
        if status == STATUS_CONFLICT:
            return _public_purge(existing)
        if status == STATUS_REVIEW:
            return _public_purge(existing)
        if status in (STATUS_PENDING, STATUS_FAILED):
            return await _run_purge(db, existing, user=user)
        # Unknown status — fail closed, do not mutate.
        raise PurgeConflictError(f"Status operasi purge tidak dikenali: {status}")

    actor = actor_snapshot(user)
    role = str((user or {}).get("role") or "")
    op = {
        "id": str(uuid.uuid4()),
        "idempotency_key": key,
        "payload_hash": payload,
        "cash_bank_id": str(cash_bank_id),
        "cash_bank_name": cb.get("name"),
        "start_date": start,
        "end_date": end,
        "status": STATUS_PENDING,
        "requested_at": now_iso(),
        "committed_at": None,
        "actor": actor,
        "role": role,
        "eligible_count": 0,
        "deleted_count": 0,
        "protected_count": 0,
        "ambiguous_count": 0,
        "skipped_count": 0,
        "conflicted_count": 0,
        "deleted_line_ids": [],
        "deleted_line_ids_capped": False,
        "totals_indeterminate": False,
        "gap_fill_import_ids": [],
        "message": "",
    }
    try:
        await db[PURGES].insert_one(dict(op))
    except Exception:
        raced = await db[PURGES].find_one(
            {"cash_bank_id": str(cash_bank_id), "idempotency_key": key},
            {"_id": 0},
        )
        if raced:
            if raced.get("payload_hash") != payload:
                raise PurgeConflictError(
                    "Kunci idempotensi sama dipakai untuk rekening/periode berbeda"
                )
            if raced.get("status") == STATUS_COMPLETED:
                return _public_purge(raced)
            if raced.get("status") in (STATUS_CONFLICT, STATUS_REVIEW):
                return _public_purge(raced)
            if raced.get("status") in (STATUS_PENDING, STATUS_FAILED):
                return await _run_purge(db, raced, user=user)
            raise PurgeConflictError(
                f"Status operasi purge tidak dikenali: {raced.get('status')}"
            )
        raise
    op.pop("_id", None)
    return await _run_purge(db, op, user=user)


async def _run_purge(db, op: dict, *, user: Optional[dict] = None) -> dict:
    del user
    cash_bank_id = str(op["cash_bank_id"])
    start = op["start_date"]
    end = op["end_date"]
    attempt_id = f"purge:{op['id']}"
    file_hash = f"purge:{op['idempotency_key']}"

    # Ensure FAILED can re-enter the pending work loop without losing progress fields.
    if op.get("status") == STATUS_FAILED:
        await db[PURGES].update_one(
            {"id": op["id"], "status": STATUS_FAILED},
            {"$set": {"status": STATUS_PENDING, "message": "Melanjutkan setelah kegagalan sementara"}},
        )
        op = await db[PURGES].find_one({"id": op["id"]}, {"_id": 0}) or op

    try:
        await _acquire_import_fence(
            db,
            cash_bank_id,
            attempt_id,
            operation_id=str(op["id"]),
            file_hash=file_hash,
            wait=False,
            timeout=0.05,
        )
    except StatementConflictError as exc:
        # Transient fence busy — keep PENDING so the same key can resume later.
        # Do not steal the lock; do not mark terminal CONFLICT.
        await db[PURGES].update_one(
            {"id": op["id"], "status": STATUS_PENDING},
            {"$set": {
                "last_fence_conflict_at": now_iso(),
                "message": f"Fence sibuk: {exc}",
            }},
        )
        raise PurgeConflictError(str(exc)) from exc

    try:
        classified = await _classify_lines(
            db, cash_bank_id=cash_bank_id, start=start, end=end,
        )
        op = await _persist_snapshot(db, op, classified)
        if op.get("status") == STATUS_CONFLICT:
            raise PurgeConflictError(op.get("message") or "Operasi purge ditolak")
        if op.get("status") == STATUS_REVIEW:
            return _public_purge(op)

        op = await _reconcile_missing_after_crash(db, op)
        if op.get("status") == STATUS_REVIEW:
            return _public_purge(op)

        recorded = set(str(x) for x in (op.get("deleted_line_ids") or []))
        snapshot = set(str(x) for x in (op.get("eligible_snapshot_ids") or []))
        # On first wave, snapshot covers all eligible; on resume, only delete
        # still-present lines that remain claim-free.
        work_ids = list(classified["eligible_ids"])
        if snapshot:
            # Prefer snapshot order for stability; include any newly classified
            # only if they were in the original snapshot (no scope expansion).
            work_ids = [lid for lid in classified["eligible_ids"] if lid in snapshot]

        conflicted = int(op.get("conflicted_count") or 0)
        skipped = int(op.get("skipped_count") or 0)
        wave_import_ids: Set[str] = set(str(x) for x in (op.get("gap_fill_import_ids") or []))

        for line_id in work_ids:
            if line_id in recorded:
                continue
            active = await db[RECON_MATCHES].find_one(
                {"statement_line_id": line_id, "status": "active"},
                {"_id": 0, "id": 1},
            )
            if active:
                conflicted += 1
                continue
            claim_clause = _claim_absent_clause()
            filt = {
                "id": line_id,
                "cash_bank_id": cash_bank_id,
                "sealed": {"$ne": False},
                "duplicate_status": {"$in": [DUP_CLEAR, DUP_KEPT]},
                "$and": claim_clause["$and"],
            }
            # Period guard: only delete if business date still in range.
            row = await db[LINES].find_one({"id": line_id}, {"_id": 0, "date": 1, "import_id": 1})
            if not row:
                # Gone since classify — reconcile as our delete under fence.
                await _record_deleted_line(db, op["id"], line_id)
                recorded.add(line_id)
                skipped += 1
                continue
            biz = _line_period_date(row)
            if biz is None or not (start <= biz <= end):
                conflicted += 1
                continue
            result = await db[LINES].delete_one(filt)
            if result.deleted_count == 1:
                await _record_deleted_line(db, op["id"], line_id)
                recorded.add(line_id)
                if row.get("import_id"):
                    wave_import_ids.add(str(row["import_id"]))
            else:
                still = await db[LINES].find_one({"id": line_id}, {"_id": 0, "id": 1})
                if still:
                    conflicted += 1
                else:
                    # Ambiguous crash window: delete may have succeeded before progress.
                    await _record_deleted_line(db, op["id"], line_id)
                    recorded.add(line_id)
                    skipped += 1

        op = await db[PURGES].find_one({"id": op["id"]}, {"_id": 0}) or op
        deleted_count = int(op.get("deleted_count") or 0)
        import_ids = sorted(wave_import_ids | set(op.get("snapshot_import_ids") or []))
        # Only mark COMPLETE batches that contributed deleted rows.
        deleted_ids_for_import = set(str(x) for x in (op.get("deleted_line_ids") or []))
        mark_imports = sorted({
            str(r.get("import_id"))
            for r in classified["eligible"]
            if r.get("import_id") and str(r.get("id")) in deleted_ids_for_import
        })
        # Also mark snapshot import ids when ids were capped but deletes happened.
        if not mark_imports and deleted_count > 0:
            mark_imports = sorted(set(op.get("snapshot_import_ids") or []))
        if mark_imports:
            await db[IMPORTS].update_many(
                {"id": {"$in": mark_imports}, "status": "complete"},
                {"$set": {
                    "allows_unmatched_gap_fill": True,
                    "unmatched_purged_at": now_iso(),
                }},
            )

        message = (
            f"{deleted_count} mutasi belum cocok dihapus. "
            "Jurnal dan pencocokan tersimpan tidak diubah."
        )
        if conflicted:
            message += f" {conflicted} baris dilewati karena konflik konkurensi."
        if op.get("deleted_line_ids_capped"):
            message += " Daftar id terhapus dipotong; deleted_count tetap kumulatif."

        # Never COMPLETE if totals are marked indeterminate.
        if op.get("totals_indeterminate"):
            await db[PURGES].update_one(
                {"id": op["id"]},
                {"$set": {
                    "status": STATUS_REVIEW,
                    "committed_at": now_iso(),
                    "conflicted_count": conflicted,
                    "skipped_count": skipped,
                    "gap_fill_import_ids": mark_imports,
                    "message": message,
                }},
            )
        else:
            await db[PURGES].update_one(
                {"id": op["id"], "status": STATUS_PENDING},
                {"$set": {
                    "status": STATUS_COMPLETED,
                    "committed_at": now_iso(),
                    "protected_count": len(classified["protected"]),
                    "ambiguous_count": len(classified["ambiguous"]),
                    "skipped_count": skipped,
                    "conflicted_count": conflicted,
                    "gap_fill_import_ids": mark_imports,
                    "message": message,
                }},
            )
    except PurgeConflictError:
        raise
    except Exception as exc:
        await db[PURGES].update_one(
            {"id": op["id"], "status": STATUS_PENDING},
            {"$set": {
                "status": STATUS_FAILED,
                "message": str(exc)[:500],
                "failed_at": now_iso(),
            }},
        )
        raise
    finally:
        await _release_import_fence(db, cash_bank_id, attempt_id)

    current = await db[PURGES].find_one({"id": op["id"]}, {"_id": 0})
    return _public_purge(current or op)
