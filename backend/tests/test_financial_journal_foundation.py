"""Tahap 2.1 — financial journal revision foundation (local Mongo only).

Feature flag defaults OFF. Allowlist defaults EMPTY.
Tests configure settings only via local app_settings writes — never production flags.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_journal_foundation_21_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")

from motor.motor_asyncio import AsyncIOMotorClient

from services.accounting_periods import (
    PeriodLockedError,
    close_period,
    ensure_indexes as ensure_period_indexes,
)
from services.financial_journal_foundation import (
    ALLOWLIST_KEY,
    CLAIM_COLL,
    CLAIM_HELD_MESSAGE,
    FLAG_KEY,
    REVISION_INDEX_PREFLIGHT_REQUIRED,
    acquire_source_post_claim,
    is_financial_journal_revision_enabled,
    release_source_post_claim,
    revision_activation_blockers,
    revision_enabled_for_source,
    revision_source_allowlist,
)
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    JournalConflictError,
    STATUS_POSTED,
    STATUS_REVERSED,
    ensure_indexes as ensure_journal,
    find_active_posted_primary_for_source,
    find_primary_journal_for_source,
    post_balanced_journal,
    reverse_journal,
)

pytestmark = pytest.mark.anyio

# Synthetic source only — never sales_invoice / payroll / etc.
SYNTH_SOURCE = "test_revision_source_synthetic"


async def _cfg(db, *, enabled: bool, allowlist=None):
    """Test-only settings writer. Not imported by production services."""
    await db.app_settings.update_one(
        {"key": FLAG_KEY},
        {"$set": {"key": FLAG_KEY, "enabled": bool(enabled)}},
        upsert=True,
    )
    types = list(allowlist) if allowlist is not None else []
    await db.app_settings.update_one(
        {"key": ALLOWLIST_KEY},
        {"$set": {"key": ALLOWLIST_KEY, "source_types": types}},
        upsert=True,
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    await ensure_journal(database)
    await ensure_period_indexes(database)
    for coll in (
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        CLAIM_COLL,
        "accounting_periods",
        "app_settings",
        "counters",
    ):
        await database[coll].delete_many({})
    await _cfg(database, enabled=False, allowlist=[])
    yield database
    for coll in (
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        CLAIM_COLL,
        "accounting_periods",
        "app_settings",
        "counters",
    ):
        await database[coll].delete_many({})
    client.close()


def _lines(amount="100.00", a="acc-dr", b="acc-cr"):
    return [
        {"account_id": a, "debit": amount, "credit": "0", "description": "Dr"},
        {"account_id": b, "debit": "0", "credit": amount, "description": "Cr"},
    ]


async def _post(db, *, source_id, source_type=SYNTH_SOURCE, date="2026-03-15", amount="100.00"):
    return await post_balanced_journal(
        db,
        date=date,
        source_type=source_type,
        source_id=source_id,
        description="test",
        lines=_lines(amount),
        posted_by={"email": "t@example.com"},
    )


# ---------------------------------------------------------------------------
# Flag / allowlist isolation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_01_flag_and_allowlist_default_off(db):
    await db.app_settings.delete_many({"key": {"$in": [FLAG_KEY, ALLOWLIST_KEY]}})
    assert await is_financial_journal_revision_enabled(db) is False
    assert await revision_source_allowlist(db) == frozenset()
    assert await revision_enabled_for_source(db, SYNTH_SOURCE) is False
    assert await revision_enabled_for_source(db, "sales_invoice") is False


@pytest.mark.anyio
async def test_02_global_on_empty_allowlist_is_legacy(db):
    await _cfg(db, enabled=True, allowlist=[])
    assert await is_financial_journal_revision_enabled(db) is True
    assert await revision_enabled_for_source(db, SYNTH_SOURCE) is False
    assert await revision_enabled_for_source(db, "sales_invoice") is False
    assert await revision_enabled_for_source(db, "payroll") is False

    sid = f"src-{uuid.uuid4().hex[:8]}"
    original = await _post(db, source_id=sid)
    await reverse_journal(
        db, original["id"], source_type=SYNTH_SOURCE, source_id=sid,
        posted_by={"email": "t@example.com"},
    )
    found = await find_primary_journal_for_source(db, SYNTH_SOURCE, sid)
    assert found is not None
    assert found["status"] == STATUS_REVERSED
    again = await _post(db, source_id=sid)
    assert again["id"] == original["id"]
    assert again["status"] == STATUS_REVERSED


@pytest.mark.anyio
async def test_03_wildcard_allowlist_ignored(db):
    await _cfg(db, enabled=True, allowlist=["*", "ALL", "ANY"])
    assert await revision_source_allowlist(db) == frozenset()
    assert await revision_enabled_for_source(db, SYNTH_SOURCE) is False


@pytest.mark.anyio
async def test_04_activation_blockers_document_index_preflight(db):
    blockers = revision_activation_blockers(global_enabled=True, allowlist=[SYNTH_SOURCE])
    assert REVISION_INDEX_PREFLIGHT_REQUIRED is True
    assert "production_index_preflight_required" in blockers


# ---------------------------------------------------------------------------
# Legacy find / status preservation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_05_legacy_find_shows_reversed_even_when_allowlisted(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"vis-{uuid.uuid4().hex[:8]}"
    original = await _post(db, source_id=sid)
    await reverse_journal(
        db, original["id"], source_type=SYNTH_SOURCE, source_id=sid,
        posted_by={"email": "t@example.com"},
    )
    legacy = await find_primary_journal_for_source(db, SYNTH_SOURCE, sid)
    assert legacy is not None
    assert legacy["status"] == STATUS_REVERSED
    assert legacy["id"] == original["id"]
    active = await find_active_posted_primary_for_source(db, SYNTH_SOURCE, sid)
    assert active is None


@pytest.mark.anyio
async def test_06_non_allowlisted_source_never_revises(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    other = "sales_invoice"
    sid = f"inv-{uuid.uuid4().hex[:8]}"
    original = await _post(db, source_id=sid, source_type=other)
    await reverse_journal(
        db, original["id"], source_type=other, source_id=sid,
        posted_by={"email": "t@example.com"},
    )
    assert await revision_enabled_for_source(db, other) is False
    again = await _post(db, source_id=sid, source_type=other)
    assert again["id"] == original["id"]
    assert again["status"] == STATUS_REVERSED


# ---------------------------------------------------------------------------
# Allowlisted revision path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_07_allowlisted_repost_after_reverse(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"rev-{uuid.uuid4().hex[:8]}"
    original = await _post(db, source_id=sid)
    await reverse_journal(
        db, original["id"], source_type=SYNTH_SOURCE, source_id=sid,
        posted_by={"email": "t@example.com"},
    )
    repost = await _post(db, source_id=sid)
    assert repost["status"] == STATUS_POSTED
    assert repost["id"] != original["id"]
    raw = await db[JOURNAL_ENTRIES].find_one({"id": repost["id"]}, {"_id": 0})
    assert int(raw.get("journal_revision") or 0) == 2
    assert raw.get("supersedes_journal_id") == original["id"]
    legacy = await find_primary_journal_for_source(db, SYNTH_SOURCE, sid)
    # Legacy find_one may return either primary; active posted must be unique
    assert await db[JOURNAL_ENTRIES].count_documents(
        {
            "source_type": SYNTH_SOURCE,
            "source_id": sid,
            "status": STATUS_POSTED,
            "reversal_of": None,
        }
    ) == 1
    old = await db[JOURNAL_ENTRIES].find_one({"id": original["id"]}, {"_id": 0})
    assert old["status"] == STATUS_REVERSED


@pytest.mark.anyio
async def test_08_posted_idempotency_allowlisted(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"idem-{uuid.uuid4().hex[:8]}"
    j1 = await _post(db, source_id=sid)
    j2 = await _post(db, source_id=sid)
    assert j1["id"] == j2["id"]


@pytest.mark.anyio
async def test_09_flag_off_no_revision_meta(db):
    sid = f"meta-{uuid.uuid4().hex[:8]}"
    j = await _post(db, source_id=sid)
    raw = await db[JOURNAL_ENTRIES].find_one({"id": j["id"]}, {"_id": 0})
    assert "journal_revision" not in raw
    assert "supersedes_journal_id" not in raw


# ---------------------------------------------------------------------------
# Claims / concurrency / idempotency
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_10_stale_claim_recovery_and_token_release(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    st, sid = SYNTH_SOURCE, f"claim-{uuid.uuid4().hex[:8]}"
    first = await acquire_source_post_claim(db, st, sid, ttl_seconds=60)
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    await db[CLAIM_COLL].update_one(
        {"source_type": st, "source_id": sid},
        {"$set": {"expires_at": past}},
    )
    second = await acquire_source_post_claim(db, st, sid, ttl_seconds=60)
    assert second["token"] != first["token"]
    assert await release_source_post_claim(db, st, sid, first["token"]) is False
    assert await release_source_post_claim(db, st, sid, second["token"]) is True


@pytest.mark.anyio
async def test_11_live_claim_without_journal_is_retryable_conflict(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"held-{uuid.uuid4().hex[:8]}"
    await acquire_source_post_claim(db, SYNTH_SOURCE, sid, ttl_seconds=120)
    with pytest.raises(JournalConflictError) as exc:
        await _post(db, source_id=sid)
    assert CLAIM_HELD_MESSAGE in str(exc.value)
    assert await db[JOURNAL_ENTRIES].count_documents(
        {"source_type": SYNTH_SOURCE, "source_id": sid}
    ) == 0


@pytest.mark.anyio
async def test_12_retry_after_claim_expires(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"exp-{uuid.uuid4().hex[:8]}"
    await acquire_source_post_claim(db, SYNTH_SOURCE, sid, ttl_seconds=60)
    past = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
    await db[CLAIM_COLL].update_one(
        {"source_type": SYNTH_SOURCE, "source_id": sid},
        {"$set": {"expires_at": past}},
    )
    j = await _post(db, source_id=sid)
    assert j["status"] == STATUS_POSTED


@pytest.mark.anyio
async def test_13_duplicate_key_returns_existing_posted(db):
    """Second concurrent insert conflict must return the winner idempotently."""
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"race-{uuid.uuid4().hex[:8]}"

    async def worker():
        return await _post(db, source_id=sid)

    results = await asyncio.gather(worker(), worker(), worker(), return_exceptions=True)
    ok = [r for r in results if isinstance(r, dict)]
    assert ok
    ids = {r["id"] for r in ok}
    assert len(ids) == 1
    assert all(r["status"] == STATUS_POSTED for r in ok)
    assert await db[JOURNAL_ENTRIES].count_documents(
        {
            "source_type": SYNTH_SOURCE,
            "source_id": sid,
            "status": STATUS_POSTED,
            "reversal_of": None,
        }
    ) == 1


@pytest.mark.anyio
async def test_14_concurrent_revision_one_active(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"revc-{uuid.uuid4().hex[:8]}"
    original = await _post(db, source_id=sid)
    await reverse_journal(
        db, original["id"], source_type=SYNTH_SOURCE, source_id=sid,
        posted_by={"email": "t@example.com"},
    )

    async def worker():
        try:
            return await _post(db, source_id=sid)
        except JournalConflictError as exc:
            return exc

    results = await asyncio.gather(worker(), worker(), worker())
    posted = [r for r in results if isinstance(r, dict)]
    assert posted
    assert len({r["id"] for r in posted}) == 1
    assert await db[JOURNAL_ENTRIES].count_documents(
        {
            "source_type": SYNTH_SOURCE,
            "source_id": sid,
            "status": STATUS_POSTED,
            "reversal_of": None,
        }
    ) == 1


@pytest.mark.anyio
async def test_15_different_sources_independent(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    a = await _post(db, source_id=f"a-{uuid.uuid4().hex[:6]}")
    b = await _post(db, source_id=f"b-{uuid.uuid4().hex[:6]}")
    assert a["id"] != b["id"]


# ---------------------------------------------------------------------------
# Crash / period / amounts / history
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_16_incomplete_lines_compensating_delete(db, monkeypatch):
    """Header without lines must not remain as successful posted journal."""
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"lines-{uuid.uuid4().hex[:8]}"
    from motor.motor_asyncio import AsyncIOMotorCollection

    real_insert_many = AsyncIOMotorCollection.insert_many

    async def boom(self, *args, **kwargs):
        raise RuntimeError("simulated_line_failure")

    monkeypatch.setattr(AsyncIOMotorCollection, "insert_many", boom)
    with pytest.raises(RuntimeError, match="simulated_line_failure"):
        await _post(db, source_id=sid)
    monkeypatch.setattr(AsyncIOMotorCollection, "insert_many", real_insert_many)

    assert await db[JOURNAL_ENTRIES].count_documents(
        {"source_type": SYNTH_SOURCE, "source_id": sid}
    ) == 0
    assert await db[JOURNAL_LINES].count_documents({}) == 0
    j = await _post(db, source_id=sid)
    assert j["status"] == STATUS_POSTED


@pytest.mark.anyio
async def test_17_reversal_without_repost_leaves_reversed_visible(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"half-{uuid.uuid4().hex[:8]}"
    original = await _post(db, source_id=sid)
    await reverse_journal(
        db, original["id"], source_type=SYNTH_SOURCE, source_id=sid,
        posted_by={"email": "t@example.com"},
    )
    legacy = await find_primary_journal_for_source(db, SYNTH_SOURCE, sid)
    assert legacy["status"] == STATUS_REVERSED
    assert await find_active_posted_primary_for_source(db, SYNTH_SOURCE, sid) is None


@pytest.mark.anyio
async def test_18_closed_period_blocks_new_and_revision(db):
    await close_period(db, "2026-03", user={"email": "admin@example.com"}, reason="test")
    with pytest.raises(PeriodLockedError):
        await _post(db, source_id=f"p-{uuid.uuid4().hex[:6]}", date="2026-03-10")

    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    # Open a journal in April then reverse; closed March still blocks March date repost
    sid = f"apr-{uuid.uuid4().hex[:6]}"
    original = await _post(db, source_id=sid, date="2026-04-01")
    await reverse_journal(
        db, original["id"], source_type=SYNTH_SOURCE, source_id=sid,
        posted_by={"email": "t@example.com"},
    )
    with pytest.raises(PeriodLockedError):
        await _post(db, source_id=sid, date="2026-03-10")


@pytest.mark.anyio
async def test_19_amount_correctness_and_history(db):
    await _cfg(db, enabled=True, allowlist=[SYNTH_SOURCE])
    sid = f"amt-{uuid.uuid4().hex[:8]}"
    original = await _post(db, source_id=sid, amount="250.50")
    await reverse_journal(
        db, original["id"], source_type=SYNTH_SOURCE, source_id=sid,
        posted_by={"email": "t@example.com"},
    )
    repost = await _post(db, source_id=sid, amount="250.50")
    lines = await db[JOURNAL_LINES].find({"journal_id": repost["id"]}, {"_id": 0}).to_list(10)
    debits = sum(float(ln.get("debit") or 0) for ln in lines)
    credits = sum(float(ln.get("credit") or 0) for ln in lines)
    assert round(debits, 2) == 250.50
    assert round(credits, 2) == 250.50
    all_docs = await db[JOURNAL_ENTRIES].find(
        {"source_type": SYNTH_SOURCE, "source_id": sid}, {"_id": 0}
    ).to_list(20)
    assert len(all_docs) == 3


@pytest.mark.anyio
async def test_20_index_definition_unchanged(db):
    info = await db[JOURNAL_ENTRIES].index_information()
    assert "uniq_primary_source_journal" in info
    spec = info["uniq_primary_source_journal"]
    assert spec.get("unique") is True
    pfe = spec.get("partialFilterExpression") or {}
    assert pfe.get("status") == STATUS_POSTED
    assert pfe.get("reversal_of") is None


@pytest.mark.anyio
async def test_21_no_production_test_helper_in_foundation_api():
    import services.financial_journal_foundation as foundation

    assert not hasattr(foundation, "set_financial_journal_revision_enabled_for_tests")
