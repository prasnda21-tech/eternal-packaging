"""Explicit Finance posting for financial inventory issues (COGS).

One PENGIRIMAN movement → one primary inventory_issue journal.
Dr COGS / Cr Inventory. No reversal in Phase 4B.3.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.accounting_periods import PeriodLockedError
from services.inventory_financial_locks import (
    LOCK_CONFLICT_CODE,
    LOCK_CONFLICT_MESSAGE,
    acquire_item_lock,
    release_item_lock,
    renew_item_lock,
)
from services.inventory_issue_accounting import (
    ACCOUNTING_CLAIM_TTL_SECONDS,
    CLAIM_AT_FIELD,
    CLAIM_FIELD,
    CODE_LOADING_CANCEL_ISSUE_POSTED,
    CODE_LOADING_CANCEL_POSTING_IN_PROGRESS,
    CODE_LOADING_REVERSED,
    CODE_LOCK_CONFLICT,
    COLLECTION,
    CONTROL_COLLECTION,
    CONTROL_KEY,
    InventoryIssueError,
    METHOD_CHOICES,
    MOVEMENT_PENGIRIMAN,
    MSG_LOADING_CANCEL_ISSUE_POSTED,
    MSG_LOADING_CANCEL_POSTING_IN_PROGRESS,
    POSTING_BASIS_FIELD,
    SOURCE_TYPE,
    STATUS_PENDING,
    STATUS_POSTED,
    _blocker,
    actor_snapshot,
    assert_no_later_posted_issue,
    build_cogs_posting_basis,
    evaluate_inventory_issue,
    find_active_inventory_issue_journal,
    find_inventory_issue_source_journal,
    find_loading_pengiriman_reversal,
    infer_frozen_method_from_posted,
    issue_item_key,
    journal_lines_from_basis,
    load_movement,
    movement_id_of,
    parse_financial_hpp_method,
    public_issue_view,
    read_raw_hpp_method,
    unjournaled_basis_still_coverable,
)
from services.journal_core import (
    JournalConflictError,
    JournalValidationError,
    post_balanced_journal,
)
from services.purchase_accounting_settings import get_purchase_accounting_settings
from services.purchase_common import now_iso

PERM_VIEW = "inventory_issue.view"
PERM_POST = "inventory_issue.post"


def _http_error(exc: InventoryIssueError) -> HTTPException:
    return HTTPException(exc.status_code, exc.as_detail())


def _claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=ACCOUNTING_CLAIM_TTL_SECONDS)
    return {
        "$or": [
            {CLAIM_FIELD: {"$exists": False}},
            {CLAIM_FIELD: None},
            {CLAIM_FIELD: ""},
            {CLAIM_AT_FIELD: {"$exists": False}},
            {CLAIM_AT_FIELD: None},
            {CLAIM_AT_FIELD: ""},
            {CLAIM_AT_FIELD: {"$lte": cutoff.isoformat()}},
        ]
    }


async def acquire_posting_claim(
    db, movement_id: str, *, token: Optional[str] = None
) -> Optional[dict]:
    mid = str(movement_id or "").strip()
    claim_token = str(token or uuid.uuid4())
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    try:
        doc = await db[COLLECTION].find_one_and_update(
            {
                "movement_id": mid,
                **_claim_absent_or_stale_clause(now=now_dt),
            },
            {
                "$set": {
                    "movement_id": mid,
                    CLAIM_FIELD: claim_token,
                    CLAIM_AT_FIELD: now,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                    "status": "claiming",
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return None
    except Exception:
        return None
    if not doc:
        return None
    if str(doc.get(CLAIM_FIELD) or "") != claim_token:
        return None
    doc.pop("_id", None)
    return doc


async def clear_posting_claim(db, movement_id: str, *, token: Optional[str] = None) -> None:
    filt: Dict[str, Any] = {"movement_id": str(movement_id)}
    if token:
        filt[CLAIM_FIELD] = str(token)
    await db[COLLECTION].update_one(
        filt,
        {
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def renew_posting_claim(db, movement_id: str, token: str) -> bool:
    """Refresh claim_at while holding token — prevents TTL steal during cancel reverse."""
    mid = str(movement_id or "").strip()
    tok = str(token or "").strip()
    if not mid or not tok:
        return False
    now = datetime.now(timezone.utc).isoformat()
    result = await db[COLLECTION].update_one(
        {"movement_id": mid, CLAIM_FIELD: tok},
        {"$set": {CLAIM_AT_FIELD: now, "updated_at": now}},
    )
    return int(getattr(result, "matched_count", 0) or 0) > 0


async def release_loading_cancel_inventory_issue_fence(
    db, held: List[Tuple[str, str]]
) -> None:
    for mid, token in held or []:
        try:
            await clear_posting_claim(db, mid, token=token)
        except Exception:
            continue


async def touch_loading_cancel_inventory_issue_fence(
    db, held: List[Tuple[str, str]]
) -> None:
    for mid, token in held or []:
        ok = await renew_posting_claim(db, mid, token)
        if not ok:
            raise HTTPException(409, {
                "message": MSG_LOADING_CANCEL_POSTING_IN_PROGRESS,
                "code": CODE_LOADING_CANCEL_POSTING_IN_PROGRESS,
                "movement_id": mid,
            })


async def fence_loading_cancel_against_inventory_issues(
    db, movements: List[dict]
) -> List[Tuple[str, str]]:
    """Claim inventory_issue posting fences for Loading PENGIRIMAN before reverse.

    Fail-closed:
    - Active posted inventory_issue journal → HTTP 409 (Finance must handle).
    - Concurrent posting/cancel holding the claim → HTTP 409.
    Caller must release returned (movement_id, token) pairs in finally.
    """
    held: List[Tuple[str, str]] = []
    try:
        for mov in movements or []:
            m_type = str(mov.get("movement_type") or "").strip().upper()
            if m_type not in (MOVEMENT_PENGIRIMAN, "STOK_KELUAR"):
                continue
            mid = movement_id_of(mov)
            if not mid:
                continue
            claimed = await acquire_posting_claim(db, mid)
            if claimed is None:
                active = await find_active_inventory_issue_journal(db, mid)
                if active:
                    raise HTTPException(409, {
                        "message": MSG_LOADING_CANCEL_ISSUE_POSTED,
                        "code": CODE_LOADING_CANCEL_ISSUE_POSTED,
                        "movement_id": mid,
                        "accounting_journal_id": active.get("id"),
                    })
                raise HTTPException(409, {
                    "message": MSG_LOADING_CANCEL_POSTING_IN_PROGRESS,
                    "code": CODE_LOADING_CANCEL_POSTING_IN_PROGRESS,
                    "movement_id": mid,
                })
            token = str(claimed.get(CLAIM_FIELD) or "")
            held.append((mid, token))
            active = await find_active_inventory_issue_journal(db, mid)
            if active:
                raise HTTPException(409, {
                    "message": MSG_LOADING_CANCEL_ISSUE_POSTED,
                    "code": CODE_LOADING_CANCEL_ISSUE_POSTED,
                    "movement_id": mid,
                    "accounting_journal_id": active.get("id"),
                })
        return held
    except Exception:
        await release_loading_cancel_inventory_issue_fence(db, held)
        raise


async def assert_movement_not_loading_reversed(db, movement_id: str) -> None:
    """Reject inventory_issue post when Loading REVERSAL_PENGIRIMAN exists."""
    mid = str(movement_id or "").strip()
    if not mid:
        return
    rev = await find_loading_pengiriman_reversal(db, mid)
    if rev:
        code, msg = _blocker(CODE_LOADING_REVERSED)
        raise InventoryIssueError(code, msg)


async def persist_accounting_posting_basis(
    db, movement_id: str, token: str, basis: dict, *, extra: Optional[dict] = None
) -> bool:
    """Persist basis only once. An existing basis is immutable."""
    patch = {
        POSTING_BASIS_FIELD: basis,
        "financial_inventory_item_key": basis.get("financial_inventory_item_key"),
        "updated_at": now_iso(),
    }
    if extra:
        patch.update(extra)
    result = await db[COLLECTION].update_one(
        {
            "movement_id": str(movement_id),
            CLAIM_FIELD: str(token),
            "$or": [
                {POSTING_BASIS_FIELD: {"$exists": False}},
                {POSTING_BASIS_FIELD: None},
            ],
        },
        {"$set": patch},
    )
    return int(getattr(result, "matched_count", 0) or 0) > 0


async def require_held_item_lock(db, item_key: str, token: str) -> dict:
    held = await renew_item_lock(db, item_key, token)
    if not held:
        raise InventoryIssueError(LOCK_CONFLICT_CODE, LOCK_CONFLICT_MESSAGE)
    return held


async def _heal_posted_metadata(
    db, movement_id: str, journal: dict, user: Optional[dict] = None
) -> None:
    now = now_iso()
    patch: Dict[str, Any] = {
        "updated_at": now,
        "accounting_journal_id": journal.get("id"),
        "status": STATUS_POSTED,
        "blocker_code": None,
        "blocker_message": None,
    }
    stored = await db[COLLECTION].find_one({"movement_id": str(movement_id)}, {"_id": 0}) or {}
    if not stored.get("posted_at"):
        patch["posted_at"] = stored.get("posted_at") or journal.get("posted_at") or now
    if user and not stored.get("posted_by"):
        patch["posted_by"] = actor_snapshot(user)
    elif not stored.get("posted_by") and journal.get("posted_by"):
        patch["posted_by"] = {"id": "", "name": str(journal.get("posted_by") or ""), "role": ""}
    await db[COLLECTION].update_one(
        {"movement_id": str(movement_id)},
        {
            "$set": patch,
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
            "$setOnInsert": {
                "movement_id": str(movement_id),
                "created_at": now,
            },
        },
        upsert=True,
    )


async def establish_cogs_policy(db, method: str, user: Optional[dict]) -> str:
    wanted = parse_financial_hpp_method(method)
    if wanted not in METHOD_CHOICES:
        raise InventoryIssueError("INVALID_HPP_METHOD", "Metode HPP finansial tidak valid.")
    existing = await infer_frozen_method_from_posted(db)
    if existing and existing != wanted:
        raise InventoryIssueError(
            "FINANCIAL_COGS_METHOD_MISMATCH",
            "Metode HPP finansial tidak sama dengan kebijakan yang sudah dibekukan.",
        )
    actor = actor_snapshot(user)
    now = now_iso()
    try:
        await db[CONTROL_COLLECTION].find_one_and_update(
            {"key": CONTROL_KEY, "costing_method": wanted},
            {
                "$setOnInsert": {
                    "key": CONTROL_KEY,
                    "costing_method": wanted,
                    "frozen_at": now,
                    "frozen_by": actor,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        current = await db[CONTROL_COLLECTION].find_one({"key": CONTROL_KEY}, {"_id": 0})
        frozen = parse_financial_hpp_method((current or {}).get("costing_method"))
        if frozen and frozen != wanted:
            raise InventoryIssueError(
                "FINANCIAL_COGS_METHOD_MISMATCH",
                "Metode HPP finansial tidak sama dengan kebijakan yang sudah dibekukan.",
            )
        if not frozen:
            raise InventoryIssueError(
                "FINANCIAL_COGS_METHOD_MISMATCH",
                "Metode HPP finansial tidak sama dengan kebijakan yang sudah dibekukan.",
            )
    frozen_now = await infer_frozen_method_from_posted(db)
    if frozen_now and frozen_now != wanted:
        raise InventoryIssueError(
            "FINANCIAL_COGS_METHOD_MISMATCH",
            "Metode HPP finansial tidak sama dengan kebijakan yang sudah dibekukan.",
        )
    return wanted


async def _write_source_journal_from_basis(db, movement: dict, user: dict, basis: dict) -> None:
    lines = journal_lines_from_basis(basis)
    journal_date = str(basis.get("journal_date") or "")
    if not journal_date:
        raise InventoryIssueError("INVALID_MOVEMENT_TIMESTAMP", "Tanggal jurnal tidak valid.")
    try:
        await post_balanced_journal(
            db,
            date=journal_date,
            source_type=SOURCE_TYPE,
            source_id=movement_id_of(movement),
            description=f"HPP / Pengeluaran Persediaan {basis.get('reference_number') or movement_id_of(movement)}",
            lines=lines,
            posted_by=user,
        )
    except PeriodLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JournalConflictError:
        existing = await find_inventory_issue_source_journal(db, movement_id_of(movement))
        if existing:
            return
        raise HTTPException(409, "Jurnal HPP untuk mutasi ini sudah ada.")


async def _require_postable_evaluation(db, movement_id: str) -> Dict[str, Any]:
    evaluation = await evaluate_inventory_issue(db, movement_id)
    if not evaluation:
        raise HTTPException(404, f"Mutasi '{movement_id}' tidak ditemukan.")
    if evaluation.get("accounting_journal_id") or evaluation.get("status") == STATUS_POSTED:
        return evaluation
    if not evaluation.get("can_post") or evaluation.get("status") != STATUS_PENDING:
        raise InventoryIssueError(
            str(evaluation.get("blocker_code") or "BLOCKED"),
            str(evaluation.get("blocker_message") or "Pengeluaran tidak dapat diposting."),
        )
    return evaluation


async def post_inventory_issue_accounting(
    db, movement_id: str, user: dict
) -> Dict[str, Any]:
    mid = str(movement_id or "").strip()
    movement = await load_movement(db, mid)
    if not movement:
        raise HTTPException(404, f"Mutasi '{mid}' tidak ditemukan.")

    existing = await find_inventory_issue_source_journal(db, mid)
    if existing and str(existing.get("status") or "") == STATUS_POSTED:
        await _heal_posted_metadata(db, mid, existing, user)
        evaluation = await evaluate_inventory_issue(db, mid)
        return public_issue_view(evaluation)

    claimed = await acquire_posting_claim(db, mid)
    if claimed is None:
        existing = await find_inventory_issue_source_journal(db, mid)
        if existing:
            await _heal_posted_metadata(db, mid, existing, user)
            evaluation = await evaluate_inventory_issue(db, mid)
            return public_issue_view(evaluation)
        raise _http_error(InventoryIssueError(
            CODE_LOCK_CONFLICT,
            "Posting HPP gagal karena perubahan bersamaan.",
        ))

    post_token = str(claimed.get(CLAIM_FIELD) or "")
    item_key = issue_item_key(movement)
    item_lock = None
    try:
        # After claim: Loading cancel reverse must not race a journal write.
        await assert_movement_not_loading_reversed(db, mid)

        existing = await find_inventory_issue_source_journal(db, mid)
        if existing:
            await _heal_posted_metadata(db, mid, existing, user)
            return public_issue_view(await evaluate_inventory_issue(db, mid))

        item_lock = await acquire_item_lock(db, item_key, mid)
        if item_lock is None:
            raise InventoryIssueError(LOCK_CONFLICT_CODE, LOCK_CONFLICT_MESSAGE)

        movement = await load_movement(db, mid) or movement
        await assert_no_later_posted_issue(db, movement)
        evaluation = await _require_postable_evaluation(db, mid)
        raw_method = await read_raw_hpp_method(db)
        if not raw_method:
            raise InventoryIssueError("INVALID_HPP_METHOD", "Metode HPP finansial tidak valid.")
        method = await establish_cogs_policy(db, raw_method, user)
        evaluation = await _require_postable_evaluation(db, mid)
        if evaluation.get("hpp_method") and evaluation.get("hpp_method") != method:
            raise InventoryIssueError(
                "FINANCIAL_COGS_METHOD_MISMATCH",
                "Metode HPP finansial tidak sama dengan kebijakan yang sudah dibekukan.",
            )

        stored = await db[COLLECTION].find_one({"movement_id": mid}, {"_id": 0}) or {}
        stored_basis = stored.get(POSTING_BASIS_FIELD)
        settings = await get_purchase_accounting_settings(db)
        await assert_no_later_posted_issue(db, movement, basis=stored_basis)
        recomputed = await evaluate_inventory_issue(db, mid)
        if not recomputed or not recomputed.get("can_post"):
            raise InventoryIssueError(
                str((recomputed or {}).get("blocker_code") or "BLOCKED"),
                str((recomputed or {}).get("blocker_message") or "Pengeluaran tidak dapat diposting."),
            )

        if stored_basis and not await find_inventory_issue_source_journal(db, mid):
            basis = stored_basis
        else:
            await require_held_item_lock(db, item_key, str(item_lock.get("token") or ""))
            computed_eval = await evaluate_inventory_issue(db, mid)
            rows = await _reload_computed(db, mid)
            if not rows:
                raise InventoryIssueError(
                    "INSUFFICIENT_FINANCIAL_INVENTORY",
                    "Persediaan finansial tidak mencukupi untuk HPP.",
                )
            basis = build_cogs_posting_basis(
                movement=movement,
                evaluation=computed_eval or evaluation,
                computed=rows,
                settings=settings,
                user=user,
                claim_token=post_token,
            )
            persisted = await persist_accounting_posting_basis(db, mid, post_token, basis)
            if not persisted:
                existing_basis = (
                    await db[COLLECTION].find_one({"movement_id": mid}, {"_id": 0}) or {}
                ).get(POSTING_BASIS_FIELD)
                if existing_basis:
                    basis = existing_basis
                else:
                    raise InventoryIssueError(
                        CODE_LOCK_CONFLICT,
                        "Posting HPP gagal karena perubahan bersamaan.",
                    )

        existing = await find_inventory_issue_source_journal(db, mid)
        if existing:
            await _heal_posted_metadata(db, mid, existing, user)
            return public_issue_view(await evaluate_inventory_issue(db, mid))

        await require_held_item_lock(db, item_key, str(item_lock.get("token") or ""))
        await assert_no_later_posted_issue(db, movement, basis=basis)
        cover_err = await unjournaled_basis_still_coverable(db, basis)
        if cover_err:
            code, msg = _blocker(cover_err)
            raise InventoryIssueError(code, msg)
        # Final fence before journal write (cancel may have reversed under claim race).
        await assert_movement_not_loading_reversed(db, mid)
        await _write_source_journal_from_basis(db, movement, user, basis)
        written = await find_inventory_issue_source_journal(db, mid)
        if written:
            try:
                await _heal_posted_metadata(db, mid, written, user)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(500, "Posting HPP belum selesai disimpan.") from exc
        return public_issue_view(await evaluate_inventory_issue(db, mid))
    except InventoryIssueError as exc:
        still = await find_inventory_issue_source_journal(db, mid)
        if still:
            await _heal_posted_metadata(db, mid, still, user)
            return public_issue_view(await evaluate_inventory_issue(db, mid))
        await clear_posting_claim(db, mid, token=post_token)
        raise _http_error(exc) from exc
    except HTTPException:
        still = await find_inventory_issue_source_journal(db, mid)
        if still:
            await _heal_posted_metadata(db, mid, still, user)
            if still.get("status") == STATUS_POSTED:
                return public_issue_view(await evaluate_inventory_issue(db, mid))
        else:
            await clear_posting_claim(db, mid, token=post_token)
        raise
    except Exception:
        still = await find_inventory_issue_source_journal(db, mid)
        if still:
            await _heal_posted_metadata(db, mid, still, user)
            return public_issue_view(await evaluate_inventory_issue(db, mid))
        await clear_posting_claim(db, mid, token=post_token)
        raise
    finally:
        if item_lock:
            await release_item_lock(db, item_key, str(item_lock.get("token") or ""))
        await clear_posting_claim(db, mid, token=post_token)


async def _reload_computed(db, movement_id: str) -> Optional[dict]:
    """Rebuild FIFO/RATA-RATA computed payload after lock + revalidation."""
    from services.inventory_issue_accounting import (
        _compute_issue_cost,
        load_live_acquisitions,
        load_live_issue_bases,
        validate_cogs_account,
    )

    movement = await load_movement(db, movement_id)
    if not movement:
        return None
    settings = await get_purchase_accounting_settings(db)
    cogs_id, cogs_err = await validate_cogs_account(db, settings.get("cogs_account_id"))
    if cogs_err:
        return None
    method = await infer_frozen_method_from_posted(db) or await read_raw_hpp_method(db)
    if method not in METHOD_CHOICES:
        return None
    computed, err = await _compute_issue_cost(
        db,
        movement,
        method=method,
        acquisitions=await load_live_acquisitions(db),
        live_issue_bases=await load_live_issue_bases(db),
        cogs_account_id=cogs_id,
    )
    if err or not computed:
        return None
    return computed
