#!/usr/bin/env python3
"""Read-only investigation of accounting reconciliation exceptions.

Scope (Owner production run):
  - Receipts matching final-audit category
    anomaly_settlement_effective_missing_journal (settlement counts + no
    primary journals).
  - Finalized invoices with no primary journals (legacy_finalized_never_posted
    bucket and nearby incomplete/anomaly subclasses).

Never prints customer names, invoice/receipt numbers, account codes, or amounts.
Never writes. Exit: 0=classified, 2=REVIEW_REQUIRED, 3=BLOCKED, 1=error.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
RECON_PATH = SCRIPT_DIR / "accounting_final_reconciliation_audit.py"


def _load_recon():
    spec = importlib.util.spec_from_file_location("acct_recon", RECON_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


RECON = _load_recon()

SOURCE_INVOICE = "sales_invoice"
SOURCE_RECEIPT = "sales_receipt"
PAGE = 200
HARD_CAP = 20_000
JOURNAL_BATCH = 200
AUTO_INCOMPLETE = frozenset(
    {"pending", "posting", "failed", "retry_pending", "error", "gagal", "in_progress"}
)


# ---------------------------------------------------------------------------
# Pure classifiers (unit-testable)
# ---------------------------------------------------------------------------


def settlement_field_shape(doc: dict) -> str:
    if "settlement_effective" not in doc:
        return "absent"
    if doc.get("settlement_effective") is True:
        return "true"
    if doc.get("settlement_effective") is False:
        return "false"
    return "other"


def classify_receipt_missing_journal(doc: dict, journals: Sequence[dict]) -> str:
    """Subclass one receipt that the final audit flags as missing-journal anomaly.

    Preconditions (enforced): counts toward outstanding AND no primary journals.
    If caller passes journals, returns a different code (should not happen for
    the anomaly set).
    """
    if journals:
        chosen, note = RECON.select_primary(journals)
        if note == "ambiguous":
            return "other_ambiguous_primary"
        if chosen and str(chosen.get("status") or "").lower() == "reversed":
            return "other_only_reversed_primary"
        if chosen and str(chosen.get("status") or "").lower() == "posted":
            return "other_has_active_posted"  # not actually missing
        return "other_has_nonposted_primary"

    if not RECON.receipt_counts_toward_outstanding(doc):
        return "other_does_not_count"

    shape = settlement_field_shape(doc)
    auto = str(doc.get("auto_accounting_status") or "").strip().lower()
    eligible = doc.get("auto_lifecycle_eligible") is True
    has_settle_at = bool(doc.get("settlement_effective_at"))
    has_posted_at = bool(doc.get("accounting_posted_at"))

    if shape == "true":
        # Contract: apply_operational_settlement requires posted journal first.
        if auto in AUTO_INCOMPLETE:
            return "confirmed_true_settlement_auto_incomplete_no_journal"
        if eligible or auto == "posted" or has_settle_at or has_posted_at:
            return "confirmed_true_settlement_missing_journal"
        return "confirmed_true_settlement_missing_journal"

    if shape == "absent":
        if eligible or auto in AUTO_INCOMPLETE or auto == "posted":
            return "review_legacy_absent_but_auto_lifecycle_signals"
        if has_posted_at:
            return "review_legacy_absent_with_posted_at_no_journal"
        return "legacy_absent_missing_journal"

    return "other_unexpected_settlement_shape"


def classify_finalized_invoice_no_journal(doc: dict, journals: Sequence[dict]) -> str:
    """Finer buckets for finalized invoices with no primary journals."""
    status = str(doc.get("financial_status") or "").strip().lower()
    if status != "finalized":
        return "other_not_finalized"
    if journals:
        chosen, note = RECON.select_primary(journals)
        if note == "ambiguous":
            return "other_ambiguous_primary"
        if chosen and str(chosen.get("status") or "").lower() == "posted":
            return "other_has_active_posted"
        if chosen and str(chosen.get("status") or "").lower() == "reversed":
            return "review_finalized_accounting_reversed"
        return "other_has_nonposted_primary"

    auto = str(doc.get("auto_accounting_status") or "").strip().lower()
    eligible = doc.get("auto_lifecycle_eligible") is True
    has_posted_at = bool(doc.get("accounting_posted_at"))
    has_auto_posted_at = bool(doc.get("auto_accounting_posted_at"))

    if auto in AUTO_INCOMPLETE:
        return "review_auto_lifecycle_incomplete"
    if auto == "posted" or has_auto_posted_at:
        return "confirmed_auto_says_posted_but_no_journal"
    if has_posted_at:
        return "review_posted_at_without_journal"
    if eligible:
        return "review_auto_eligible_never_posted"
    if auto in ("", "none", "null"):
        # No automation markers — candidate pre-automation, not proven by date alone.
        return "candidate_pre_automation_unmarked"
    # Explicit unexpected auto status while finalized/unposted
    return "review_finalized_unposted_with_auto_status"


def is_confirmed_receipt_anomaly(code: str) -> bool:
    return code.startswith("confirmed_")


def is_confirmed_invoice_anomaly(code: str) -> bool:
    return code.startswith("confirmed_")


# ---------------------------------------------------------------------------
# IO
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


async def run_investigation(env_file: Path, timeout_sec: float) -> int:
    t0 = time.monotonic()
    blocks: List[str] = []
    reviews: List[str] = []

    def blocked(msg: str) -> None:
        blocks.append(msg)
        print(f"BLOCKED {msg}")

    def review(msg: str) -> None:
        reviews.append(msg)
        print(f"REVIEW {msg}")

    def ok(msg: str) -> None:
        print(f"OK {msg}")

    env = _load_env(env_file)
    mongo_url = env.get("MONGO_URL") or os.environ.get("MONGO_URL")
    db_name = env.get("DB_NAME") or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("ERROR missing MONGO_URL or DB_NAME", file=sys.stderr)
        return 1
    print(f"INVESTIGATION_START db_name_present=yes timeout_sec={timeout_sec}")

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

    async def page_docs(coll: str, filt: dict, projection: dict, label: str) -> List[dict]:
        out: List[dict] = []
        skip = 0
        while len(out) < HARD_CAP:
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
                blocked(f"page TIMEOUT {label}")
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
        if len(out) >= HARD_CAP:
            blocked(f"{label} HARD_CAP truncated fail-closed")
        return out

    async def journals_for(
        source_type: str, source_ids: Sequence[str]
    ) -> Tuple[Dict[str, List[dict]], bool]:
        grouped: Dict[str, List[dict]] = {sid: [] for sid in source_ids}
        complete = True
        for i in range(0, len(source_ids), JOURNAL_BATCH):
            chunk = list(source_ids[i : i + JOURNAL_BATCH])
            if not chunk:
                continue
            fetch_limit = RECON.journal_fetch_limit(len(chunk))
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
                        },
                        max_time_ms=mt_ms,
                    ).to_list(fetch_limit + 1),
                    per_op,
                    f"journals:{source_type}:{i}",
                )
            except TimeoutError:
                blocked(f"journals TIMEOUT {source_type}")
                return grouped, False
            except Exception as exc:
                blocked(f"journals failed ({type(exc).__name__})")
                return grouped, False
            fetched = list(docs or [])
            if RECON.journal_batch_is_truncated(len(fetched), fetch_limit):
                blocked(f"journals TRUNCATED {source_type} offset={i}")
                complete = False
                fetched = fetched[:fetch_limit]
            for d in fetched:
                sid = str(d.get("source_id") or "")
                if sid in grouped:
                    grouped[sid].append(d)
        return grouped, complete

    # --- Receipts: candidates that count + will be checked for missing journals ---
    receipts = await page_docs(
        "sales_receipts",
        {
            "financial_status": {"$nin": ["cancelled", "Canceled", "CANCELLED", "reversed"]},
            "cancelled": {"$ne": True},
            "$or": [
                {"settlement_effective": True},
                {"settlement_effective": {"$exists": False}},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "financial_status": 1,
            "cancelled": 1,
            "settlement_effective": 1,
            "settlement_effective_at": 1,
            "auto_accounting_status": 1,
            "auto_lifecycle_eligible": 1,
            "accounting_posted_at": 1,
            "created_at": 1,
            "updated_at": 1,
        },
        "receipts_counting",
    )
    rcpt_ids = [str(d["id"]) for d in receipts if d.get("id")]
    rcpt_journals, rcpt_j_ok = await journals_for(SOURCE_RECEIPT, rcpt_ids)

    missing: List[Tuple[dict, List[dict]]] = []
    for doc in receipts:
        if not RECON.receipt_counts_toward_outstanding(doc):
            continue
        sid = str(doc.get("id") or "")
        jrn = rcpt_journals.get(sid) or []
        if not jrn:
            missing.append((doc, jrn))

    rcpt_classes: Counter = Counter()
    shape_of_missing: Counter = Counter()
    for doc, jrn in missing:
        code = classify_receipt_missing_journal(doc, jrn)
        rcpt_classes[code] += 1
        shape_of_missing[settlement_field_shape(doc)] += 1

    print(
        "RECEIPT_MISSING_JOURNAL_SCOPE "
        f"counting_candidates={len(receipts)} "
        f"missing_primary_journal={len(missing)} "
        f"journal_scan_complete={rcpt_j_ok}"
    )
    print(f"RECEIPT_MISSING_JOURNAL_SETTLEMENT_SHAPE {dict(shape_of_missing)}")
    print(f"RECEIPT_MISSING_JOURNAL_CLASSIFICATION {dict(rcpt_classes)}")

    confirmed_rcpt = sum(
        n for k, n in rcpt_classes.items() if is_confirmed_receipt_anomaly(k)
    )
    review_rcpt = sum(n for k, n in rcpt_classes.items() if k.startswith("review_"))
    legacy_rcpt = int(rcpt_classes.get("legacy_absent_missing_journal", 0))

    # Contract note (code-level, not a production claim)
    print(
        "RECEIPT_CONTRACT "
        "settlement_effective_true_requires_posted_journal=yes "
        "settlement_effective_absent_counts_legacy=yes "
        "anomaly_category_includes_true_and_absent=yes"
    )

    # --- Invoices: finalized with no primary journals ---
    invoices = await page_docs(
        "sales_invoices",
        {"financial_status": "finalized"},
        {
            "_id": 0,
            "id": 1,
            "financial_status": 1,
            "auto_accounting_status": 1,
            "auto_lifecycle_eligible": 1,
            "auto_accounting_posted_at": 1,
            "accounting_posted_at": 1,
            "created_at": 1,
            "updated_at": 1,
        },
        "invoices_finalized",
    )
    inv_ids = [str(d["id"]) for d in invoices if d.get("id")]
    inv_journals, inv_j_ok = await journals_for(SOURCE_INVOICE, inv_ids)

    inv_no_j: List[Tuple[dict, List[dict]]] = []
    for doc in invoices:
        sid = str(doc.get("id") or "")
        jrn = inv_journals.get(sid) or []
        if not jrn:
            inv_no_j.append((doc, jrn))

    inv_classes: Counter = Counter()
    for doc, jrn in inv_no_j:
        inv_classes[classify_finalized_invoice_no_journal(doc, jrn)] += 1

    print(
        "INVOICE_NO_JOURNAL_SCOPE "
        f"finalized={len(invoices)} "
        f"no_primary_journal={len(inv_no_j)} "
        f"journal_scan_complete={inv_j_ok}"
    )
    print(f"INVOICE_LEGACY_CLASSIFICATION {dict(inv_classes)}")

    confirmed_inv = sum(
        n for k, n in inv_classes.items() if is_confirmed_invoice_anomaly(k)
    )
    review_inv = sum(n for k, n in inv_classes.items() if k.startswith("review_"))
    candidate_pre = int(inv_classes.get("candidate_pre_automation_unmarked", 0))

    print(
        "CONFIRMED_ACCOUNTING_ANOMALIES "
        f"receipt={confirmed_rcpt} invoice={confirmed_inv}"
    )
    print(
        "UNRESOLVED_OR_REVIEW "
        f"receipt_review={review_rcpt} invoice_review={review_inv} "
        f"receipt_legacy_absent={legacy_rcpt} "
        f"invoice_candidate_pre_automation={candidate_pre}"
    )

    if not rcpt_j_ok or not inv_j_ok:
        blocked("journal scan incomplete")

    if confirmed_rcpt or confirmed_inv:
        review(
            f"confirmed anomalies present receipt={confirmed_rcpt} invoice={confirmed_inv}"
        )
    if review_rcpt or review_inv:
        review(
            f"review subclasses present receipt={review_rcpt} invoice={review_inv}"
        )
    if legacy_rcpt:
        ok(
            f"legacy_absent_missing_journal={legacy_rcpt} "
            "(operational legacy; not automatic journal fabrication)"
        )
    if candidate_pre:
        ok(
            f"candidate_pre_automation_unmarked={candidate_pre} "
            "(unmarked finalized/unposted; not proven pre-automation by date)"
        )

    print(
        "MINIMUM_SAFE_NEXT_ACTION "
        "owner_review_confirmed_true_settlement_missing_journal_if_any; "
        "do_not_backfill_journals; "
        "do_not_mass_set_settlement_effective; "
        "for_legacy_absent_missing_journal_leave_or_owner_decide_ops_vs_accounting"
    )

    elapsed = round(time.monotonic() - t0, 2)
    print(
        f"INVESTIGATION_END elapsed_sec={elapsed} "
        f"missing_receipts={len(missing)} finalized_no_journal={len(inv_no_j)} "
        f"blocked={len(blocks)} review={len(reviews)}"
    )
    client.close()

    if blocks:
        print("VERDICT BLOCKED")
        return 3
    if confirmed_rcpt or confirmed_inv or review_rcpt or review_inv or legacy_rcpt or candidate_pre:
        # Presence of classified exceptions ⇒ Owner review
        print("VERDICT REVIEW_REQUIRED")
        return 2
    print("VERDICT PASS")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--timeout-sec", type=float, default=45.0)
    args = p.parse_args()
    if not args.env_file.is_file():
        print(f"ERROR env-file missing: {args.env_file}", file=sys.stderr)
        return 1
    return asyncio.run(run_investigation(args.env_file, args.timeout_sec))


if __name__ == "__main__":
    sys.exit(main())
