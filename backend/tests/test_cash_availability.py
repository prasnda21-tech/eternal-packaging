import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from services.cash_availability import get_cash_availability
from services.finance_widget_layout import finance_widget_allowed
from services.widget_dashboard_http import register_widget_dashboard_routes
from test_dashboard_completion import Collection, DB

NOW = datetime(2026, 10, 31, 18, tzinfo=timezone.utc)  # 1 November WIB


def build(extra_headers=None, extra_lines=None, masters=None):
    return DB(
        ledger_accounts=Collection([
            {"id": "cash", "code": "100", "name": "Kas", "account_type": "BANK"},
            {"id": "bank", "code": "101", "name": "Bank nonaktif", "account_type": "BANK", "is_active": False},
            {"id": "zero", "code": "102", "name": "Nol", "account_type": "BANK"},
            {"id": "expense", "code": "600", "name": "Beban", "account_type": "EXPS"},
        ]),
        cash_bank_accounts=Collection(masters or []),
        journal_entries=Collection([
            {"id": "opening", "date": "2025-01-01", "status": "posted"},
            {"id": "transfer", "date": "2026-10-31", "status": "posted"},
            {"id": "out", "date": "2026-11-01", "status": "posted"},
            {"id": "draft", "date": "2026-10-31", "status": "draft"},
            {"id": "future", "date": "2026-11-02", "status": "posted"},
        ] + (extra_headers or [])),
        journal_lines=Collection([
            {"journal_id": "opening", "account_id": "cash", "debit": 100, "credit": 0},
            {"journal_id": "transfer", "account_id": "cash", "debit": 0, "credit": 30},
            {"journal_id": "transfer", "account_id": "bank", "debit": 30, "credit": 0},
            {"journal_id": "out", "account_id": "bank", "debit": 0, "credit": 50},
            {"journal_id": "draft", "account_id": "cash", "debit": 900, "credit": 0},
            {"journal_id": "future", "account_id": "cash", "debit": 900, "credit": 0},
            {"journal_id": "opening", "account_id": "expense", "debit": 999, "credit": 0},
        ] + (extra_lines or [])),
    )


def test_cumulative_all_accounts_transfer_negative_zero_and_wib_cutoff():
    body = asyncio.run(get_cash_availability(build(), now=NOW))
    assert body["as_of_date"] == "2026-11-01"
    assert body["total"] == 50
    assert [row["balance"] for row in body["rows"]] == [70, -20, 0]
    assert body["total"] == sum(row["balance"] for row in body["rows"])


def test_reversal_pair_and_legacy_journals_follow_ledger_rules():
    db = build([
        {"id": "old", "date": "2026-10-30", "status": "reversed"},
        {"id": "reverse", "date": "2026-10-31", "status": "posted"},
        {"id": "legacy", "date": "2026-10-31"},
    ], [
        {"journal_id": "old", "account_id": "cash", "debit": 10},
        {"journal_id": "reverse", "account_id": "cash", "credit": 10},
        {"journal_id": "legacy", "account_id": "cash", "debit": 0.25},
    ])
    assert asyncio.run(get_cash_availability(db, now=NOW))["total"] == 50.25


def test_duplicate_master_mapping_never_doubles_balance():
    db = build(masters=[{"id": "a", "account_id": "cash", "name": "Kas Jogja"}, {"id": "b", "account_id": "cash", "name": "Alias"}])
    body = asyncio.run(get_cash_availability(db, now=NOW))
    assert body["total"] == 50 and body["account_count"] == 3
    assert body["rows"][0]["name"] == "Kas Jogja / Alias"


def test_unmapped_master_marks_total_unknown_without_hiding_rekening():
    body = asyncio.run(get_cash_availability(build(masters=[{"id": "missing", "name": "Belum terhubung"}]), now=NOW))
    assert body["status"] == "incomplete" and body["total"] is None
    assert body["known_total"] == 50 and body["rows"][-1]["balance"] is None


def test_no_accounts_is_not_zero_and_no_transactions_is_zero():
    assert asyncio.run(get_cash_availability(DB(), now=NOW))["total"] is None
    db = build(); db["journal_entries"] = Collection([])
    body = asyncio.run(get_cash_availability(db, now=NOW))
    assert body["status"] == "complete" and body["total"] == 0


def test_all_journal_batches_are_included():
    headers = [{"id": str(i), "date": "2026-10-31", "status": "posted"} for i in range(1001)]
    lines = [{"journal_id": str(i), "account_id": "cash", "debit": 1} for i in range(1001)]
    assert asyncio.run(get_cash_availability(build(headers, lines), now=NOW))["total"] == 1051


def test_no_reconciliation_collection_read():
    class StrictDB(DB):
        def __getitem__(self, key):
            assert key in {"ledger_accounts", "cash_bank_accounts", "journal_entries", "journal_lines"}
            return super().__getitem__(key)
    assert asyncio.run(get_cash_availability(StrictDB(build()), now=NOW))["total"] == 50


def test_route_and_layout_require_cash_view_only():
    calls = {}
    def require(permission):
        async def allowed(): return {}
        calls[permission] = allowed
        return allowed
    router = APIRouter()
    register_widget_dashboard_routes(router, get_db=lambda: None, require_perm=require, Depends=Depends)
    route = next(r for r in router.routes if r.path == "/dashboard/widget/cash-available")
    assert route.dependant.dependencies[0].call is calls["cash_bank.view"]
    assert finance_widget_allowed("cash_available", {"cash_bank.view"})
    assert not finance_widget_allowed("cash_available", {"cash_bank.reconcile"})
