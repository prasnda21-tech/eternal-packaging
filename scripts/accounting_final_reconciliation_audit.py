#!/usr/bin/env python3
"""Final accounting reconciliation audit (READ-ONLY).

Owner runs on an approved host against backend .env. Never mutates data.

Source-of-truth contracts (code):
  - Posted primary journals: journal_entries where reversal_of is null,
    status=posted, source_type in {sales_invoice, sales_receipt}.
  - Reversal journals: journal_entries with reversal_of string (and/or
    source_type sales_*_reversal).
  - Edit revision: reverse then repost → prior primary becomes reversed;
    active posted primary is the current financial effect.
  - Business cancel: soft-cancel document; posted docs reverse first.
  - Settlement-effective receipts: cancelled never count; explicit
    settlement_effective=False never counts; field absent = legacy counts;
    explicit True counts when not cancelled
    (services.sales_receipt_posting.receipt_counts_toward_outstanding).
  - accounting_posted / accounting_status on API responses are projections
    from journals (attach_*); they are NOT Mongo source of truth.
  - AR/GL: accounts_receivable derives from journal_entries + journal_lines.

Safety:
  - No writes, DDL, flag changes, grants, deploy, or restore.
  - Never prints URI/passwords, customer names, invoice/receipt numbers,
    account codes, or individual monetary amounts.
  - Bounded queries, maxTimeMS, pagination, fail-closed timeouts.
  - Exit: 0=PASS, 2=REVIEW_REQUIRED, 3=BLOCKED, 1=script error.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SOURCE_INVOICE = "sales_invoice"
SOURCE_RECEIPT = "sales_receipt"
SOURCE_INV_REV = "sales_invoice_reversal"
SOURCE_RCPT_REV = "sales_receipt_reversal"
SOURCE_CREDIT = "sales_customer_credit_application"
SOURCE_CREDIT_REV = "sales_customer_credit_application_reversal"

PAGE = 200
HARD_DOC_CAP = 20_000
JOURNAL_BATCH = 200


class Verdict:
    PASS = "PASS"
    REVIEW = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


# ---------------------------------------------------------------------------
# Pure categorization (unit-testable; no Mongo)
# ---------------------------------------------------------------------------


def receipt_is_cancelled(doc: Optional[dict]) -> bool:
    if not doc:
        return False
    status = str(doc.get("financial_status") or "").strip().lower()
    if status in ("cancelled", "reversed", "canceled"):
        return True
    return doc.get("cancelled") is True


def receipt_counts_toward_outstanding(doc: Optional[dict]) -> bool:
    """Mirror services.sales_receipt_posting.receipt_counts_toward_outstanding."""
    if receipt_is_cancelled(doc) or not doc:
        return False
    if "settlement_effective" in doc and doc.get("settlement_effective") is False:
        return False
    return True


def select_primary(docs: Sequence[dict]) -> Tuple[Optional[dict], str]:
    """Return (chosen, note). note=ok|ambiguous|none."""
    primaries = [d for d in docs if d and not d.get("reversal_of")]
    posted = [
        d for d in primaries if str(d.get("status") or "").strip().lower() == "posted"
    ]
    if len(posted) > 1:
        return None, "ambiguous"
    if len(posted) == 1:
        return posted[0], "ok"
    reversed_docs = [
        d for d in primaries if str(d.get("status") or "").strip().lower() == "reversed"
    ]
    if reversed_docs and len(reversed_docs) == len(primaries):
        reversed_docs.sort(
            key=lambda d: str(d.get("posted_at") or d.get("created_at") or "")
        )
        return reversed_docs[-1], "ok"
    if len(primaries) == 1:
        return primaries[0], "ok"
    if len(primaries) > 1:
        return None, "ambiguous"
    return None, "none"


def categorize_invoice(doc: dict, journals: Sequence[dict]) -> str:
    """Category code for one invoice vs its primary journals (reversal_of null)."""
    status = str(doc.get("financial_status") or "").strip().lower()
    chosen, note = select_primary(journals)
    if note == "ambiguous":
        return "anomaly_duplicate_active_or_ambiguous_primary"
    active_posted = bool(
        chosen and str(chosen.get("status") or "").lower() == "posted"
    )
    only_reversed = bool(
        chosen and str(chosen.get("status") or "").lower() == "reversed"
    )
    has_any = bool(journals)

    if status == "finalized":
        if active_posted:
            return "ok_finalized_active_posted"
        if only_reversed:
            return "review_finalized_accounting_reversed"
        if not has_any:
            auto = str(doc.get("auto_accounting_status") or "").strip().lower()
            if auto in ("pending", "posting", "failed", "retry_pending"):
                return "review_finalized_auto_lifecycle_incomplete"
            return "legacy_finalized_never_posted"
        return "review_finalized_nonposted_primary"
    if status == "cancelled":
        if active_posted:
            return "anomaly_cancelled_still_active_posted"
        if only_reversed or has_any:
            return "ok_cancelled_with_journal_history"
        return "ok_cancelled_unposted"
    return "info_other_financial_status"


def categorize_receipt(doc: dict, journals: Sequence[dict]) -> str:
    chosen, note = select_primary(journals)
    if note == "ambiguous":
        return "anomaly_duplicate_active_or_ambiguous_primary"
    active_posted = bool(
        chosen and str(chosen.get("status") or "").lower() == "posted"
    )
    only_reversed = bool(
        chosen and str(chosen.get("status") or "").lower() == "reversed"
    )
    cancelled = receipt_is_cancelled(doc)
    se_present = "settlement_effective" in doc
    se_val = doc.get("settlement_effective") if se_present else None
    counts = receipt_counts_toward_outstanding(doc)

    if cancelled:
        if active_posted:
            return "anomaly_cancelled_still_active_posted"
        if only_reversed or journals:
            return "ok_cancelled_with_journal_history"
        return "ok_cancelled_unposted"

    if counts and active_posted:
        if se_present and se_val is True:
            return "ok_settlement_effective_active_posted"
        if not se_present:
            return "legacy_settlement_absent_active_posted"
        return "ok_settlement_counts_active_posted"

    if counts and not journals:
        return "anomaly_settlement_effective_missing_journal"

    if counts and only_reversed:
        return "review_settlement_counts_but_accounting_reversed"

    if se_present and se_val is False:
        if active_posted:
            return "review_settlement_false_but_active_posted"
        return "ok_pending_or_ineffective_settlement"

    if active_posted and not counts:
        return "review_active_posted_not_settlement_effective"

    if not journals:
        return "info_receipt_unposted"

    return "info_receipt_other"


def projection_flag_analysis(docs: Sequence[dict]) -> Dict[str, int]:
    """Prove whether accounting_posted is stored (projection) vs absent (expected)."""
    out = Counter()
    for d in docs:
        if "accounting_posted" in d:
            out["stored_accounting_posted_field"] += 1
            if d.get("accounting_posted") is True:
                out["stored_accounting_posted_true"] += 1
        else:
            out["absent_accounting_posted_field"] += 1
        if "accounting_status" in d:
            out["stored_accounting_status_field"] += 1
            if str(d.get("accounting_status") or "").lower() == "posted":
                out["stored_accounting_status_posted"] += 1
        else:
            out["absent_accounting_status_field"] += 1
        if d.get("accounting_posted_at"):
            out["has_accounting_posted_at"] += 1
    return dict(out)


def journal_fetch_limit(chunk_len: int) -> int:
    """Intended max journals returned per source_id batch (before +1 probe)."""
    return max(int(chunk_len) * 4, 20)


def journal_batch_is_truncated(fetched_count: int, fetch_limit: int) -> bool:
    """True when fetch used limit+1 and returned more than the intended limit."""
    return int(fetched_count) > int(fetch_limit)


def resolve_dup_primary_status(
    *,
    scan_complete: bool,
    dup_groups: int,
    error: Optional[str] = None,
) -> Tuple[str, str]:
    """Fail-closed duplicate primary verdict.

    Returns (level, message) where level is 'ok' or 'blocked'.
    Incomplete/error/timeout scans must NEVER report ok/PASS.
    """
    if not scan_complete:
        detail = f" error={error}" if error else ""
        return "blocked", f"duplicate_posted_primary_scan_incomplete{detail}"
    if int(dup_groups) > 0:
        return "blocked", f"duplicate_posted_primary_groups={int(dup_groups)}"
    return "ok", "duplicate_posted_primary_groups=0"


def legacy_settlement_explained_by_documents(
    legacy_absent_active_posted_count: int,
) -> Optional[str]:
    """Explain legacy settlement only from per-document category evidence.

    Aggregate count deltas (journals vs settlement_effective=true) are never
    sufficient to claim an explanation.
    """
    n = int(legacy_absent_active_posted_count)
    if n <= 0:
        return None
    return f"legacy_settlement_absent_active_posted_documents={n}"


def legacy_explained_by_count_delta(
    posted_primary_journals: int,
    settlement_effective_true: int,
    settlement_effective_absent: int,
) -> bool:
    """Count deltas alone never explain legacy reconciliation."""
    _ = (posted_primary_journals, settlement_effective_true, settlement_effective_absent)
    return False


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _load_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def _with_timeout(coro, seconds: float, label: str):
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(label) from exc


# ---------------------------------------------------------------------------
# Audit runner
# ---------------------------------------------------------------------------


async def run_audit(env_file: Path, timeout_sec: float) -> int:
    t0 = time.monotonic()
    findings: List[str] = []
    reviews: List[str] = []
    blocks: List[str] = []

    def ok(msg: str) -> None:
        findings.append(f"OK {msg}")
        print(f"OK {msg}")

    def review(msg: str) -> None:
        reviews.append(msg)
        print(f"REVIEW {msg}")

    def blocked(msg: str) -> None:
        blocks.append(msg)
        print(f"BLOCKED {msg}")

    env = _load_env(env_file)
    mongo_url = env.get("MONGO_URL") or os.environ.get("MONGO_URL")
    db_name = env.get("DB_NAME") or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("ERROR missing MONGO_URL or DB_NAME", file=sys.stderr)
        return 1
    # Never print URI
    print(f"AUDIT_START db_name_present=yes timeout_sec={timeout_sec}")

    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        print("ERROR motor not installed", file=sys.stderr)
        return 1

    client = AsyncIOMotorClient(
        mongo_url,
        serverSelectionTimeoutMS=int(min(timeout_sec, 15) * 1000),
    )
    db = client[db_name]
    mt_ms = int(min(timeout_sec, 45) * 1000)
    per_op = min(timeout_sec, 45.0)

    try:
        await _with_timeout(client.admin.command("ping"), per_op, "ping")
        ok("mongo_ping")
    except Exception as exc:
        blocked(f"mongo_ping failed ({type(exc).__name__})")
        client.close()
        return 3

    async def page_ids(
        coll: str, filt: dict, projection: dict, label: str
    ) -> List[dict]:
        out: List[dict] = []
        skip = 0
        while len(out) < HARD_DOC_CAP:
            try:
                batch = await _with_timeout(
                    db[coll]
                    .find(filt, projection, max_time_ms=mt_ms)
                    .skip(skip)
                    .limit(PAGE)
                    .to_list(PAGE),
                    per_op,
                    f"{label}:{skip}",
                )
            except TimeoutError:
                blocked(f"page TIMEOUT {label} skip={skip}")
                return out
            except Exception as exc:
                blocked(f"page failed {label} ({type(exc).__name__})")
                return out
            if not batch:
                break
            out.extend(batch)
            if len(batch) < PAGE:
                break
            skip += PAGE
        if len(out) >= HARD_DOC_CAP:
            blocked(
                f"{label} hit HARD_DOC_CAP={HARD_DOC_CAP} "
                "(truncated; fail-closed)"
            )
        return out

    async def journals_for(
        source_type: str, source_ids: Sequence[str]
    ) -> Dict[str, List[dict]]:
        grouped: Dict[str, List[dict]] = {sid: [] for sid in source_ids}
        for i in range(0, len(source_ids), JOURNAL_BATCH):
            chunk = list(source_ids[i : i + JOURNAL_BATCH])
            if not chunk:
                continue
            fetch_limit = journal_fetch_limit(len(chunk))
            try:
                docs = await _with_timeout(
                    db.journal_entries.find(
                        {
                            "source_type": source_type,
                            "source_id": {"$in": chunk},
                            "reversal_of": None,
                        },
                        {
                            "_id": 0,
                            "id": 1,
                            "source_id": 1,
                            "status": 1,
                            "posted_at": 1,
                            "created_at": 1,
                            "reversal_of": 1,
                            "supersedes_journal_id": 1,
                            "journal_revision": 1,
                        },
                        max_time_ms=mt_ms,
                    ).to_list(fetch_limit + 1),
                    per_op,
                    f"journals:{source_type}:{i}",
                )
            except TimeoutError:
                blocked(f"journals TIMEOUT source_type={source_type}")
                return grouped
            except Exception as exc:
                blocked(f"journals failed ({type(exc).__name__})")
                return grouped
            fetched = list(docs or [])
            if journal_batch_is_truncated(len(fetched), fetch_limit):
                blocked(
                    f"journals TRUNCATED source_type={source_type} "
                    f"batch_offset={i} fetch_limit={fetch_limit} "
                    f"fetched={len(fetched)} (fail-closed)"
                )
                # Keep only the intended window; do not silently treat as complete.
                fetched = fetched[:fetch_limit]
            for d in fetched:
                sid = str(d.get("source_id") or "")
                if sid in grouped:
                    grouped[sid].append(d)
        return grouped

    async def count(coll: str, filt: dict, label: str) -> int:
        try:
            n = await _with_timeout(
                db[coll].count_documents(filt, limit=50_000, maxTimeMS=mt_ms),
                per_op,
                label,
            )
            return int(n)
        except TimeoutError:
            blocked(f"count TIMEOUT {label}")
            return -1
        except Exception as exc:
            review(f"count failed {label} ({type(exc).__name__})")
            return -1

    # --- Aggregate journal inventory (no PII) ---
    je_inv_posted = await count(
        "journal_entries",
        {"source_type": SOURCE_INVOICE, "status": "posted", "reversal_of": None},
        "je_inv_posted",
    )
    je_inv_reversed_primary = await count(
        "journal_entries",
        {"source_type": SOURCE_INVOICE, "status": "reversed", "reversal_of": None},
        "je_inv_reversed_primary",
    )
    je_inv_rev_docs = await count(
        "journal_entries",
        {"source_type": SOURCE_INV_REV, "status": "posted"},
        "je_inv_rev_docs",
    )
    je_rcpt_posted = await count(
        "journal_entries",
        {"source_type": SOURCE_RECEIPT, "status": "posted", "reversal_of": None},
        "je_rcpt_posted",
    )
    je_rcpt_reversed_primary = await count(
        "journal_entries",
        {"source_type": SOURCE_RECEIPT, "status": "reversed", "reversal_of": None},
        "je_rcpt_reversed_primary",
    )
    je_rcpt_rev_docs = await count(
        "journal_entries",
        {"source_type": SOURCE_RCPT_REV, "status": "posted"},
        "je_rcpt_rev_docs",
    )
    je_credit = await count(
        "journal_entries",
        {"source_type": SOURCE_CREDIT, "status": "posted", "reversal_of": None},
        "je_credit",
    )
    je_credit_rev = await count(
        "journal_entries",
        {"source_type": SOURCE_CREDIT_REV, "status": "posted"},
        "je_credit_rev",
    )
    dup_groups = 0
    dup_scan_complete = False
    dup_error: Optional[str] = None
    try:
        dups = await _with_timeout(
            db.journal_entries.aggregate(
                [
                    {
                        "$match": {
                            "status": "posted",
                            "reversal_of": None,
                            "source_type": {"$in": [SOURCE_INVOICE, SOURCE_RECEIPT]},
                        }
                    },
                    {
                        "$group": {
                            "_id": {"st": "$source_type", "sid": "$source_id"},
                            "n": {"$sum": 1},
                        }
                    },
                    {"$match": {"n": {"$gt": 1}}},
                    {"$count": "groups"},
                ],
                maxTimeMS=mt_ms,
            ).to_list(1),
            per_op,
            "dup_primary",
        )
        dup_groups = int((dups[0] or {}).get("groups") or 0) if dups else 0
        dup_scan_complete = True
    except TimeoutError:
        dup_error = "TIMEOUT"
    except Exception as exc:
        dup_error = type(exc).__name__
    dup_level, dup_msg = resolve_dup_primary_status(
        scan_complete=dup_scan_complete,
        dup_groups=dup_groups,
        error=dup_error,
    )
    if dup_level == "ok":
        ok(dup_msg)
    else:
        blocked(dup_msg)

    print(
        "JOURNAL_INVENTORY "
        f"inv_posted_primary={je_inv_posted} "
        f"inv_reversed_primary={je_inv_reversed_primary} "
        f"inv_reversal_docs={je_inv_rev_docs} "
        f"rcpt_posted_primary={je_rcpt_posted} "
        f"rcpt_reversed_primary={je_rcpt_reversed_primary} "
        f"rcpt_reversal_docs={je_rcpt_rev_docs} "
        f"credit_posted_primary={je_credit} "
        f"credit_reversal_docs={je_credit_rev}"
    )

    # --- Invoices ---
    invoices = await page_ids(
        "sales_invoices",
        {"financial_status": {"$in": ["finalized", "cancelled"]}},
        {
            "_id": 0,
            "id": 1,
            "financial_status": 1,
            "auto_accounting_status": 1,
            "accounting_posted": 1,
            "accounting_status": 1,
            "accounting_posted_at": 1,
            "outstanding_amount": 1,
        },
        "invoices",
    )
    inv_ids = [str(d["id"]) for d in invoices if d.get("id")]
    inv_journals = await journals_for(SOURCE_INVOICE, inv_ids)
    inv_cats: Counter = Counter()
    for doc in invoices:
        sid = str(doc.get("id") or "")
        inv_cats[categorize_invoice(doc, inv_journals.get(sid) or [])] += 1
    inv_proj = projection_flag_analysis(invoices)
    print(
        "INVOICE_RECONCILED "
        f"documents={len(invoices)} categories={dict(inv_cats)}"
    )
    print(f"INVOICE_PROJECTION_FLAGS {inv_proj}")

    # --- Receipts ---
    receipts = await page_ids(
        "sales_receipts",
        {},
        {
            "_id": 0,
            "id": 1,
            "financial_status": 1,
            "cancelled": 1,
            "settlement_effective": 1,
            "auto_accounting_status": 1,
            "accounting_posted": 1,
            "accounting_status": 1,
            "accounting_posted_at": 1,
            "invoice_id": 1,
        },
        "receipts",
    )
    rcpt_ids = [str(d["id"]) for d in receipts if d.get("id")]
    rcpt_journals = await journals_for(SOURCE_RECEIPT, rcpt_ids)
    rcpt_cats: Counter = Counter()
    settlement_true = 0
    settlement_absent = 0
    settlement_false = 0
    settlement_counts = 0
    for doc in receipts:
        sid = str(doc.get("id") or "")
        rcpt_cats[categorize_receipt(doc, rcpt_journals.get(sid) or [])] += 1
        if "settlement_effective" not in doc:
            settlement_absent += 1
        elif doc.get("settlement_effective") is True:
            settlement_true += 1
        elif doc.get("settlement_effective") is False:
            settlement_false += 1
        if receipt_counts_toward_outstanding(doc):
            settlement_counts += 1
    rcpt_proj = projection_flag_analysis(receipts)
    print(
        "RECEIPT_RECONCILED "
        f"documents={len(receipts)} categories={dict(rcpt_cats)}"
    )
    print(
        "RECEIPT_SETTLEMENT_SHAPE "
        f"settlement_effective_true={settlement_true} "
        f"settlement_effective_false={settlement_false} "
        f"settlement_effective_absent={settlement_absent} "
        f"counts_toward_outstanding={settlement_counts} "
        f"posted_primary_journals={je_rcpt_posted}"
    )
    print(f"RECEIPT_PROJECTION_FLAGS {rcpt_proj}")

    # Legacy settlement: per-document category evidence only (never count deltas).
    legacy_rcpt_docs = int(
        rcpt_cats.get("legacy_settlement_absent_active_posted", 0)
    )
    legacy_msg = legacy_settlement_explained_by_documents(legacy_rcpt_docs)
    if legacy_msg:
        ok(legacy_msg)
    if legacy_explained_by_count_delta(
        je_rcpt_posted, settlement_true, settlement_absent
    ):
        # Unreachable by contract; guard against future regressions.
        blocked("legacy_explained_by_count_delta_must_remain_false")

    # Projection flag analysis verdict
    stored_true = int(inv_proj.get("stored_accounting_posted_true", 0)) + int(
        rcpt_proj.get("stored_accounting_posted_true", 0)
    )
    absent = int(inv_proj.get("absent_accounting_posted_field", 0)) + int(
        rcpt_proj.get("absent_accounting_posted_field", 0)
    )
    if stored_true == 0 and absent > 0:
        ok(
            "live_accounting_posted_flag=0 is EXPECTED: "
            "accounting_posted is API projection from journals, not Mongo SoT"
        )
    elif stored_true > 0:
        review(
            f"some documents store accounting_posted=true count={stored_true} "
            "(still not SoT; journals remain authoritative)"
        )

    # Hard anomalies
    anomaly_keys = (
        "anomaly_duplicate_active_or_ambiguous_primary",
        "anomaly_cancelled_still_active_posted",
        "anomaly_settlement_effective_missing_journal",
    )
    anomaly_total = sum(inv_cats.get(k, 0) for k in anomaly_keys) + sum(
        rcpt_cats.get(k, 0) for k in anomaly_keys
    )
    print(f"CONFIRMED_ANOMALY_COUNT {anomaly_total}")
    for k in anomaly_keys:
        n = inv_cats.get(k, 0) + rcpt_cats.get(k, 0)
        if n:
            review(f"anomaly {k}={n}")

    review_keys = [k for k in inv_cats if k.startswith("review_")] + [
        k for k in rcpt_cats if k.startswith("review_")
    ]
    for k in sorted(set(review_keys)):
        n = inv_cats.get(k, 0) + rcpt_cats.get(k, 0)
        if n:
            review(f"category {k}={n}")

    legacy_inv = inv_cats.get("legacy_finalized_never_posted", 0)
    legacy_rcpt = rcpt_cats.get("legacy_settlement_absent_active_posted", 0)
    print(
        "LEGACY_DATA_FINDINGS "
        f"finalized_never_posted={legacy_inv} "
        f"settlement_absent_active_posted={legacy_rcpt}"
    )
    if legacy_inv:
        review(
            f"legacy_finalized_never_posted={legacy_inv} "
            "(not automatic bug; pre-automation or intentional unposted)"
        )

    # Orphan journals (journal source_id with no document) — capped
    async def orphan_count(source_type: str, coll: str) -> int:
        try:
            rows = await _with_timeout(
                db.journal_entries.aggregate(
                    [
                        {
                            "$match": {
                                "source_type": source_type,
                                "status": "posted",
                                "reversal_of": None,
                            }
                        },
                        {"$limit": 2000},
                        {
                            "$lookup": {
                                "from": coll,
                                "localField": "source_id",
                                "foreignField": "id",
                                "as": "doc",
                            }
                        },
                        {"$match": {"doc.0": {"$exists": False}}},
                        {"$count": "n"},
                    ],
                    maxTimeMS=mt_ms,
                ).to_list(1),
                per_op,
                f"orphan:{source_type}",
            )
            return int((rows[0] or {}).get("n") or 0) if rows else 0
        except TimeoutError:
            blocked(f"orphan TIMEOUT {source_type}")
            return -1
        except Exception as exc:
            review(f"orphan scan failed {source_type} ({type(exc).__name__})")
            return -1

    orphan_inv = await orphan_count(SOURCE_INVOICE, "sales_invoices")
    orphan_rcpt = await orphan_count(SOURCE_RECEIPT, "sales_receipts")
    print(
        f"ORPHAN_PRIMARY_JOURNALS invoice_sample={orphan_inv} "
        f"receipt_sample={orphan_rcpt} (capped)"
    )
    if orphan_inv and orphan_inv > 0:
        review(f"orphan_invoice_primary_journals_sample={orphan_inv}")
    if orphan_rcpt and orphan_rcpt > 0:
        review(f"orphan_receipt_primary_journals_sample={orphan_rcpt}")

    # Negative operational outstanding (informational; AR SoT is journals)
    neg = await count(
        "sales_invoices",
        {"financial_status": "finalized", "outstanding_amount": {"$lt": 0}},
        "neg_outstanding",
    )
    if neg > 0:
        review(f"negative_operational_outstanding={neg}")
    elif neg == 0:
        ok("negative_operational_outstanding=0")

    print(
        "AR_GL_CONTRACT "
        "accounting_ar_source=journal_entries+journal_lines "
        "operational_outstanding=sales_invoices.outstanding_amount "
        "do_not_equate_document_counts_to_gl"
    )
    print(
        "CANCEL_REVERSAL_CONTRACT "
        "posted_docs_reverse_before_soft_cancel "
        "unposted_soft_cancel_without_fabricated_reversal "
        "settlement_effective_forced_false_on_receipt_cancel"
    )

    elapsed = round(time.monotonic() - t0, 2)
    print(
        f"AUDIT_END elapsed_sec={elapsed} "
        f"ok={len(findings)} review={len(reviews)} blocked={len(blocks)}"
    )
    client.close()

    if blocks:
        print("VERDICT BLOCKED")
        return 3
    if reviews:
        print("VERDICT REVIEW_REQUIRED")
        return 2
    print("VERDICT PASS")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="Backend .env path (MONGO_URL + DB_NAME).",
    )
    p.add_argument("--timeout-sec", type=float, default=45.0)
    args = p.parse_args()
    if not args.env_file.is_file():
        print(f"ERROR env-file missing: {args.env_file}", file=sys.stderr)
        return 1
    return asyncio.run(run_audit(args.env_file, args.timeout_sec))


if __name__ == "__main__":
    sys.exit(main())
