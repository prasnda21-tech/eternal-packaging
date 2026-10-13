#!/usr/bin/env python3
"""Synthetic tests for accounting final reconciliation categorization.

No Mongo. No production. Validates source-contract categories only.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "accounting_final_reconciliation_audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("acct_recon", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


M = _load()


class ReceiptSettlementContract(unittest.TestCase):
    def test_absent_field_counts_legacy(self):
        self.assertTrue(M.receipt_counts_toward_outstanding({"financial_status": "posted"}))

    def test_explicit_false_never_counts(self):
        self.assertFalse(
            M.receipt_counts_toward_outstanding(
                {"financial_status": "posted", "settlement_effective": False}
            )
        )

    def test_cancelled_never_counts(self):
        self.assertFalse(
            M.receipt_counts_toward_outstanding(
                {"financial_status": "cancelled", "settlement_effective": True}
            )
        )

    def test_explicit_true_counts(self):
        self.assertTrue(
            M.receipt_counts_toward_outstanding(
                {"financial_status": "received", "settlement_effective": True}
            )
        )


class InvoiceCategories(unittest.TestCase):
    def test_finalized_active_posted(self):
        cat = M.categorize_invoice(
            {"financial_status": "finalized"},
            [{"status": "posted", "reversal_of": None}],
        )
        self.assertEqual(cat, "ok_finalized_active_posted")

    def test_finalized_never_posted_legacy(self):
        cat = M.categorize_invoice({"financial_status": "finalized"}, [])
        self.assertEqual(cat, "legacy_finalized_never_posted")

    def test_finalized_reversed_is_review_not_auto_bug(self):
        cat = M.categorize_invoice(
            {"financial_status": "finalized"},
            [{"status": "reversed", "reversal_of": None}],
        )
        self.assertEqual(cat, "review_finalized_accounting_reversed")

    def test_cancelled_still_posted_is_anomaly(self):
        cat = M.categorize_invoice(
            {"financial_status": "cancelled"},
            [{"status": "posted", "reversal_of": None}],
        )
        self.assertEqual(cat, "anomaly_cancelled_still_active_posted")

    def test_duplicate_posted_is_anomaly(self):
        cat = M.categorize_invoice(
            {"financial_status": "finalized"},
            [
                {"status": "posted", "reversal_of": None, "id": "a"},
                {"status": "posted", "reversal_of": None, "id": "b"},
            ],
        )
        self.assertEqual(cat, "anomaly_duplicate_active_or_ambiguous_primary")

    def test_cancelled_unposted_ok(self):
        cat = M.categorize_invoice({"financial_status": "cancelled"}, [])
        self.assertEqual(cat, "ok_cancelled_unposted")


class ReceiptCategories(unittest.TestCase):
    def test_legacy_absent_with_posted(self):
        cat = M.categorize_receipt(
            {"financial_status": "received"},
            [{"status": "posted", "reversal_of": None}],
        )
        self.assertEqual(cat, "legacy_settlement_absent_active_posted")

    def test_settlement_true_with_posted(self):
        cat = M.categorize_receipt(
            {"financial_status": "received", "settlement_effective": True},
            [{"status": "posted", "reversal_of": None}],
        )
        self.assertEqual(cat, "ok_settlement_effective_active_posted")

    def test_settlement_true_missing_journal_anomaly(self):
        cat = M.categorize_receipt(
            {"financial_status": "received", "settlement_effective": True},
            [],
        )
        self.assertEqual(cat, "anomaly_settlement_effective_missing_journal")

    def test_settlement_false_pending_ok(self):
        cat = M.categorize_receipt(
            {"financial_status": "received", "settlement_effective": False},
            [],
        )
        self.assertEqual(cat, "ok_pending_or_ineffective_settlement")


class ProjectionFlags(unittest.TestCase):
    def test_absent_field_is_expected_zero_live_flag(self):
        stats = M.projection_flag_analysis(
            [
                {"financial_status": "finalized"},
                {"financial_status": "finalized", "accounting_posted_at": "x"},
            ]
        )
        self.assertEqual(stats["absent_accounting_posted_field"], 2)
        self.assertEqual(stats.get("stored_accounting_posted_true", 0), 0)
        self.assertEqual(stats["has_accounting_posted_at"], 1)


class JournalPaginationTruncation(unittest.TestCase):
    def test_fetch_limit_scales_with_chunk(self):
        self.assertEqual(M.journal_fetch_limit(1), 20)
        self.assertEqual(M.journal_fetch_limit(10), 40)

    def test_limit_plus_one_detects_truncation(self):
        limit = M.journal_fetch_limit(5)
        self.assertFalse(M.journal_batch_is_truncated(limit, limit))
        self.assertTrue(M.journal_batch_is_truncated(limit + 1, limit))

    def test_truncation_is_fail_closed_signal(self):
        """Fetched count at intended limit is complete; +1 proves overflow."""
        limit = 20
        self.assertFalse(M.journal_batch_is_truncated(20, limit))
        self.assertTrue(M.journal_batch_is_truncated(21, limit))


class DupPrimaryFailClosed(unittest.TestCase):
    def test_timeout_never_reports_ok_zero(self):
        level, msg = M.resolve_dup_primary_status(
            scan_complete=False, dup_groups=0, error="TIMEOUT"
        )
        self.assertEqual(level, "blocked")
        self.assertIn("scan_incomplete", msg)
        self.assertNotEqual(msg, "duplicate_posted_primary_groups=0")

    def test_error_never_reports_ok_zero(self):
        level, msg = M.resolve_dup_primary_status(
            scan_complete=False, dup_groups=0, error="OperationFailure"
        )
        self.assertEqual(level, "blocked")
        self.assertIn("scan_incomplete", msg)

    def test_complete_zero_is_ok(self):
        level, msg = M.resolve_dup_primary_status(scan_complete=True, dup_groups=0)
        self.assertEqual(level, "ok")
        self.assertEqual(msg, "duplicate_posted_primary_groups=0")

    def test_complete_positive_is_blocked(self):
        level, msg = M.resolve_dup_primary_status(scan_complete=True, dup_groups=2)
        self.assertEqual(level, "blocked")
        self.assertEqual(msg, "duplicate_posted_primary_groups=2")


class LegacyPerDocumentEvidence(unittest.TestCase):
    def test_count_delta_alone_never_explains(self):
        self.assertFalse(
            M.legacy_explained_by_count_delta(
                posted_primary_journals=135,
                settlement_effective_true=22,
                settlement_effective_absent=100,
            )
        )

    def test_zero_documents_no_explanation(self):
        self.assertIsNone(M.legacy_settlement_explained_by_documents(0))

    def test_per_document_category_explains(self):
        msg = M.legacy_settlement_explained_by_documents(41)
        self.assertEqual(msg, "legacy_settlement_absent_active_posted_documents=41")

    def test_category_path_requires_absent_field_and_posted(self):
        cat = M.categorize_receipt(
            {"financial_status": "received"},  # settlement_effective absent
            [{"status": "posted", "reversal_of": None}],
        )
        self.assertEqual(cat, "legacy_settlement_absent_active_posted")
        self.assertIsNotNone(M.legacy_settlement_explained_by_documents(1))


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if result.result.wasSuccessful() else 1)
