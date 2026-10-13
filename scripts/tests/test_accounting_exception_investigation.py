#!/usr/bin/env python3
"""Synthetic tests for accounting exception investigation classifiers."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "accounting_exception_investigation.py"


def _load():
    spec = importlib.util.spec_from_file_location("acct_exc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


M = _load()


class ReceiptMissingJournal(unittest.TestCase):
    def test_true_settlement_no_journal_is_confirmed(self):
        code = M.classify_receipt_missing_journal(
            {"financial_status": "received", "settlement_effective": True},
            [],
        )
        self.assertEqual(code, "confirmed_true_settlement_missing_journal")
        self.assertTrue(M.is_confirmed_receipt_anomaly(code))

    def test_absent_legacy_no_journal(self):
        code = M.classify_receipt_missing_journal(
            {"financial_status": "received"},
            [],
        )
        self.assertEqual(code, "legacy_absent_missing_journal")
        self.assertFalse(M.is_confirmed_receipt_anomaly(code))

    def test_absent_with_auto_signals_is_review(self):
        code = M.classify_receipt_missing_journal(
            {
                "financial_status": "received",
                "auto_lifecycle_eligible": True,
            },
            [],
        )
        self.assertEqual(code, "review_legacy_absent_but_auto_lifecycle_signals")

    def test_explicit_false_does_not_count(self):
        code = M.classify_receipt_missing_journal(
            {"financial_status": "received", "settlement_effective": False},
            [],
        )
        self.assertEqual(code, "other_does_not_count")

    def test_final_audit_anomaly_includes_true_and_absent(self):
        """Mirror final-audit category gate: counts + no journals."""
        for doc in (
            {"financial_status": "x", "settlement_effective": True},
            {"financial_status": "x"},  # absent
        ):
            self.assertTrue(M.RECON.receipt_counts_toward_outstanding(doc))
            self.assertEqual(
                M.RECON.categorize_receipt(doc, []),
                "anomaly_settlement_effective_missing_journal",
            )


class InvoiceNoJournal(unittest.TestCase):
    def test_unmarked_is_candidate_not_proven_legacy(self):
        code = M.classify_finalized_invoice_no_journal(
            {"financial_status": "finalized"},
            [],
        )
        self.assertEqual(code, "candidate_pre_automation_unmarked")
        self.assertFalse(M.is_confirmed_invoice_anomaly(code))

    def test_auto_posted_status_without_journal_confirmed(self):
        code = M.classify_finalized_invoice_no_journal(
            {"financial_status": "finalized", "auto_accounting_status": "posted"},
            [],
        )
        self.assertEqual(code, "confirmed_auto_says_posted_but_no_journal")
        self.assertTrue(M.is_confirmed_invoice_anomaly(code))

    def test_auto_incomplete_is_review(self):
        code = M.classify_finalized_invoice_no_journal(
            {"financial_status": "finalized", "auto_accounting_status": "failed"},
            [],
        )
        self.assertEqual(code, "review_auto_lifecycle_incomplete")

    def test_eligible_never_posted_is_review(self):
        code = M.classify_finalized_invoice_no_journal(
            {"financial_status": "finalized", "auto_lifecycle_eligible": True},
            [],
        )
        self.assertEqual(code, "review_auto_eligible_never_posted")


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if result.result.wasSuccessful() else 1)
