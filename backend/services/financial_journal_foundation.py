"""Financial journal revision foundation (Tahap 2.1).

Isolated capability for controlled reverse/repost of primary journals.

Activation requires BOTH:
  1. Global flag `financial_journal_revision_enabled` = true
  2. Explicit non-empty source_type allowlist containing the source

Defaults: flag OFF, allowlist EMPTY → every caller keeps legacy outcomes.

Does NOT activate automatic Sales Invoice / Sales Receipt posting.
Does NOT mutate historical journals.
Does NOT create production index migrations on journal_entries.

INDEX ACTIVATION DEPENDENCY (fail-closed):
  Revision insert assumes deployed `uniq_primary_source_journal` matches
  journal_core.ensure_indexes partialFilterExpression
  {status: posted, reversal_of: null}.
  Production index is NOT verified by this module. Keep allowlist empty
  until a separate Owner-approved production index preflight passes.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Optional, Sequence

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

FLAG_KEY = "financial_journal_revision_enabled"
ALLOWLIST_KEY = "financial_journal_revision_source_allowlist"
CLAIM_COLL = "journal_source_post_claims"
CLAIM_TTL_SECONDS = 120
CLAIM_HELD_CODE = "journal_source_post_claim_held"
CLAIM_HELD_MESSAGE = "Posting sumber sedang diklaim"

# Documented assumption — never auto-verified against production here.
REQUIRED_PRIMARY_INDEX_NAME = "uniq_primary_source_journal"
REQUIRED_PRIMARY_INDEX_PARTIAL = {
    "status": "posted",
    "reversal_of": None,
}
REVISION_INDEX_PREFLIGHT_REQUIRED = True

# Additive claim indexes only — never touches journal_entries indexes.
_CLAIM_INDEXES_READY = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _normalize_allowlist(raw: Any) -> FrozenSet[str]:
    """Fail closed: empty / malformed / wildcards never grant revision."""
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    out = set()
    for item in raw:
        s = str(item or "").strip()
        if not s:
            continue
        upper = s.upper()
        if s == "*" or upper in {"*", "ALL", "ANY", "EVERY"}:
            # Wildcards are ignored — no silent permission expansion.
            continue
        out.add(s)
    return frozenset(out)


async def is_financial_journal_revision_enabled(db) -> bool:
    """Global flag only. Default OFF. Missing/malformed never enables."""
    try:
        doc = await db.app_settings.find_one(
            {"key": FLAG_KEY},
            {"_id": 0, "enabled": 1, "value": 1},
        )
    except Exception:
        return False
    if not doc:
        return False
    if "enabled" in doc:
        return doc.get("enabled") is True
    return doc.get("value") is True


async def revision_source_allowlist(db) -> FrozenSet[str]:
    """Explicit allowlist. Default EMPTY. No wildcards."""
    try:
        doc = await db.app_settings.find_one(
            {"key": ALLOWLIST_KEY},
            {"_id": 0, "source_types": 1, "allowlist": 1},
        )
    except Exception:
        return frozenset()
    if not doc:
        return frozenset()
    if "source_types" in doc:
        return _normalize_allowlist(doc.get("source_types"))
    return _normalize_allowlist(doc.get("allowlist"))


async def revision_enabled_for_source(db, source_type: str) -> bool:
    """True only when global flag ON and source_type explicitly allowlisted.

    Global ON + empty allowlist → False for every source (legacy path).
    """
    if not await is_financial_journal_revision_enabled(db):
        return False
    allow = await revision_source_allowlist(db)
    if not allow:
        return False
    st = str(source_type or "").strip()
    if not st:
        return False
    return st in allow


def revision_activation_blockers(
    *,
    global_enabled: bool,
    allowlist: Sequence[str],
) -> list:
    """Human-readable blockers. Production index preflight is always listed."""
    blockers = []
    if not global_enabled:
        blockers.append("global_flag_off")
    normalized = _normalize_allowlist(list(allowlist))
    if not normalized:
        blockers.append("allowlist_empty")
    if REVISION_INDEX_PREFLIGHT_REQUIRED:
        blockers.append("production_index_preflight_required")
    return blockers


async def ensure_claim_indexes(db) -> None:
    """Additive indexes on claim collection only. Safe on empty/new collection."""
    global _CLAIM_INDEXES_READY
    if _CLAIM_INDEXES_READY:
        return
    coll = db[CLAIM_COLL]
    await coll.create_index(
        [("source_type", 1), ("source_id", 1)],
        unique=True,
        name="uniq_journal_source_post_claim",
    )
    await coll.create_index("expires_at", name="journal_source_post_claim_expires")
    _CLAIM_INDEXES_READY = True


async def acquire_source_post_claim(
    db,
    source_type: str,
    source_id: str,
    *,
    ttl_seconds: int = CLAIM_TTL_SECONDS,
) -> Dict[str, str]:
    """Durable CAS claim for allowlisted revision posting. Standalone-Mongo compatible."""
    await ensure_claim_indexes(db)
    st = str(source_type or "").strip()
    sid = str(source_id or "").strip()
    if not st or not sid:
        raise ValueError("source_type and source_id required for claim")
    token = secrets.token_hex(16)
    now = _now()
    expires = now + timedelta(seconds=max(1, int(ttl_seconds)))
    doc = {
        "source_type": st,
        "source_id": sid,
        "token": token,
        "claimed_at": _iso(now),
        "expires_at": _iso(expires),
    }
    try:
        await db[CLAIM_COLL].insert_one(dict(doc))
        return {"token": token, "source_type": st, "source_id": sid, "expires_at": doc["expires_at"]}
    except DuplicateKeyError:
        existing = await db[CLAIM_COLL].find_one(
            {"source_type": st, "source_id": sid},
            {"_id": 0},
        )
        exp = _parse_iso((existing or {}).get("expires_at"))
        if existing and exp and exp <= now:
            # Stale claim recovery — token-scoped replace; never steal live claims.
            result = await db[CLAIM_COLL].find_one_and_update(
                {
                    "source_type": st,
                    "source_id": sid,
                    "token": existing.get("token"),
                    "expires_at": existing.get("expires_at"),
                },
                {"$set": {
                    "token": token,
                    "claimed_at": _iso(now),
                    "expires_at": _iso(expires),
                }},
                return_document=ReturnDocument.AFTER,
            )
            if result and str(result.get("token") or "") == token:
                return {
                    "token": token,
                    "source_type": st,
                    "source_id": sid,
                    "expires_at": _iso(expires),
                }
        raise RuntimeError(CLAIM_HELD_CODE) from None


async def release_source_post_claim(
    db,
    source_type: str,
    source_id: str,
    token: str,
) -> bool:
    """Release only when token matches. Wrong token is a no-op (False)."""
    st = str(source_type or "").strip()
    sid = str(source_id or "").strip()
    tok = str(token or "").strip()
    if not (st and sid and tok):
        return False
    result = await db[CLAIM_COLL].delete_one(
        {"source_type": st, "source_id": sid, "token": tok}
    )
    return int(getattr(result, "deleted_count", 0) or 0) == 1


async def next_journal_revision(db, source_type: str, source_id: str) -> int:
    """Revision identity for a new primary. Counts all non-reversal journals for source."""
    from services.journal_core import JOURNAL_ENTRIES

    count = await db[JOURNAL_ENTRIES].count_documents(
        {
            "source_type": str(source_type),
            "source_id": str(source_id),
            "reversal_of": None,
        }
    )
    return int(count) + 1


async def latest_reversed_primary_id(db, source_type: str, source_id: str) -> Optional[str]:
    from services.journal_core import JOURNAL_ENTRIES, STATUS_REVERSED

    doc = await db[JOURNAL_ENTRIES].find_one(
        {
            "source_type": str(source_type),
            "source_id": str(source_id),
            "reversal_of": None,
            "status": STATUS_REVERSED,
        },
        {"_id": 0, "id": 1, "posted_at": 1, "created_at": 1},
        sort=[("posted_at", -1), ("created_at", -1)],
    )
    if not doc:
        return None
    return str(doc.get("id") or "") or None
