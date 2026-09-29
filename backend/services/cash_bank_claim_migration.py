"""Explicit claim-index migration. Not called from application startup.

Startup must not drop the legacy unique index on journal_line_id.
This procedure is resumable and refuses to change data when preflight
finds duplicate claim keys.

Order, proved when Mongo accepts both indexes at once:
1. Read indexes and claim documents. Report conflicts. Write nothing.
2. Create the partial unique indexes while the legacy unique index
   still protects journal_line_id strings.
3. Verify those indexes exist.
4. Drop the legacy non-partial unique index only after that proof.
5. Verify the feature-ready contract.

If step 2 fails, the legacy index stays and this function raises.
There is then no window in which string claims are unprotected, because
the old index was not removed.

Rollback of code to the baseline startup calls
create_index("journal_line_id", unique=True) with no partial filter.
That build fails when more than one match document has a null or missing
journal_line_id, which cancelled pairs produce by unsetting the field.
Baseline startup is then not a safe rollback. The partial indexes still
protect remaining string claims, but this procedure does not promise that
rolling the code back will boot. Schema rollback to the non-partial
unique index has the same limit. Do not promise either rollback after
more than one cancelled pair exists.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.cash_bank_reconciliation import (
    RECON_MATCHES,
    claim_feature_status,
    create_target_match_indexes,
    legacy_journal_unique_name,
)
from services.cash_bank_statement import LINES


class ClaimMigrationError(Exception):
    def __init__(self, message: str, report: Optional[dict] = None):
        super().__init__(message)
        self.report = report or {}


async def _string_duplicates(collection, field: str) -> List[dict]:
    rows = await collection.aggregate([
        {"$match": {field: {"$type": "string"}}},
        {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": 20},
    ]).to_list(20)
    return [{"value": row.get("_id"), "count": row.get("n")} for row in rows]


async def _compound_duplicates(collection, account_field: str, key_field: str) -> List[dict]:
    rows = await collection.aggregate([
        {"$match": {key_field: {"$type": "string"}}},
        {"$group": {
            "_id": {"account": f"${account_field}", "key": f"${key_field}"},
            "n": {"$sum": 1},
        }},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": 20},
    ]).to_list(20)
    return [{"key": row.get("_id"), "count": row.get("n")} for row in rows]


async def preflight_claim_indexes(db) -> Dict[str, Any]:
    """Read-only inventory. Does not delete or rewrite documents."""
    matches = db[RECON_MATCHES]
    lines = db[LINES]
    info = await matches.index_information()
    line_info = await lines.index_information()
    journal_dups = await _string_duplicates(matches, "journal_line_id")
    statement_dups = await _string_duplicates(matches, "statement_line_id")
    idem_dups = await _compound_duplicates(matches, "cash_bank_id", "idempotency_key")
    cancel_dups = await _compound_duplicates(matches, "cash_bank_id", "cancel_idempotency_key")
    txn_dups = await _compound_duplicates(lines, "cash_bank_id", "authoritative_txn_id")
    conflicts = {
        "journal_line_id": journal_dups,
        "statement_line_id": statement_dups,
        "idempotency_key": idem_dups,
        "cancel_idempotency_key": cancel_dups,
        "authoritative_txn_id": txn_dups,
    }
    blocked = any(conflicts.values())
    return {
        "indexes": sorted(info),
        "line_indexes": sorted(line_info),
        "legacy_unique_index": legacy_journal_unique_name(info),
        "counts": {
            "legacy": await matches.count_documents({"reconciliation_id": {"$type": "string"}}),
            "pair_active": await matches.count_documents({"match_kind": "pair", "status": "active"}),
            "pair_cancelled": await matches.count_documents({"match_kind": "pair", "status": "cancelled"}),
            "journal_line_string": await matches.count_documents({"journal_line_id": {"$type": "string"}}),
            "journal_line_null_or_missing": await matches.count_documents({"journal_line_id": None}),
            "statement_line_string": await matches.count_documents({"statement_line_id": {"$type": "string"}}),
            "statement_line_null_or_missing": await matches.count_documents({"statement_line_id": None}),
        },
        "conflicts": conflicts,
        "blocked": blocked,
        "changed_documents": 0,
    }


async def migrate_claim_indexes(db, *, fail_after: Optional[str] = None) -> Dict[str, Any]:
    """Run the index transition. Raises without dropping the legacy index on failure."""
    report = await preflight_claim_indexes(db)
    if report["blocked"]:
        raise ClaimMigrationError(
            "Preflight menemukan konflik klaim. Tidak ada data atau index yang diubah.",
            report,
        )
    matches = db[RECON_MATCHES]
    info = await matches.index_information()
    legacy_name = legacy_journal_unique_name(info)
    await create_target_match_indexes(db)
    from services.cash_bank_statement import ensure_indexes as ensure_statement_indexes
    await ensure_statement_indexes(db)
    info = await matches.index_information()
    if legacy_journal_unique_name(info) != legacy_name:
        raise ClaimMigrationError("Index lama berubah sebelum penggantian yang direncanakan.", report)
    created = await claim_feature_status(db)
    if created["missing_indexes"]:
        raise ClaimMigrationError("Index wajib belum tersedia setelah pembuatan.", {**report, "status": created})
    if fail_after == "partials_created":
        raise ClaimMigrationError(
            "Migrasi berhenti setelah index parsial dibuat. Index lama masih ada.",
            {**report, "resumable": True, "legacy_unique_index": legacy_name},
        )
    if legacy_name:
        # Partial string uniqueness is already in place, so dropping the
        # non-partial index does not open a gap for active string claims.
        await matches.drop_index(legacy_name)
    final = await claim_feature_status(db)
    if not final["ready"]:
        raise ClaimMigrationError("Constraint final belum terverifikasi.", {**report, "status": final})
    report["ready"] = True
    report["legacy_unique_index"] = None
    report["rollback"] = {
        "code": (
            "Rollback kode ke baseline menjalankan create_index unik non-parsial "
            "journal_line_id pada startup. Pembangunan itu gagal bila lebih dari "
            "satu dokumen tidak punya journal_line_id. Startup baseline tidak aman "
            "setelah pasangan cancelled melepaskan field itu. Index parsial yang "
            "sudah terpasang tetap melindungi nilai string."
        ),
        "schema_to_non_partial_unique": (
            "Tidak tersedia bila lebih dari satu dokumen punya journal_line_id "
            "null atau hilang. Pasangan cancelled melepaskan field itu."
        ),
        "null_or_missing_journal_line_id": report["counts"]["journal_line_null_or_missing"],
    }
    return report
