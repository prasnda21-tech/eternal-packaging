"""Current cash/bank book balances. No reconciliation or forecast dependency."""
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from services.financial_statement_common import load_statement_accounts, sum_journal_lines_by_account, money, money_float


async def get_cash_availability(db, *, now=None):
    now = now or datetime.now(timezone.utc)
    as_of = now.astimezone(ZoneInfo("Asia/Jakarta")).date().isoformat()
    accounts = {str(row["id"]): row for row in await load_statement_accounts(db) if row.get("account_type") == "BANK"}
    names = {}
    unmapped = []
    async for row in db.cash_bank_accounts.find({}, {"_id": 0, "id": 1, "name": 1, "account_id": 1}):
        aid = str(row.get("account_id") or "").strip()
        if aid in accounts:
            names.setdefault(aid, []).append(str(row.get("name") or "").strip())
        else:
            unmapped.append({"account_id": None, "cash_bank_id": row.get("id"), "code": "", "name": row.get("name") or "Rekening tanpa nama", "balance": None})
    balances = await sum_journal_lines_by_account(db, {"date": {"$lte": as_of}}) if accounts else {}
    rows = []
    total = Decimal("0.00")
    for aid, account in accounts.items():
        debit, credit = balances.get(aid, (Decimal("0.00"), Decimal("0.00")))
        amount = money(debit - credit)
        total += amount
        rows.append({"account_id": aid, "code": account.get("code"),
                     "name": " / ".join(dict.fromkeys(n for n in names.get(aid, []) if n)) or account.get("name"),
                     "balance": money_float(amount)})
    rows.sort(key=lambda row: (str(row.get("code") or ""), row["account_id"]))
    rows.extend(unmapped)
    status = "incomplete" if unmapped else ("unavailable" if not accounts else "complete")
    return {
        "status": status, "as_of_date": as_of, "generated_at": now.isoformat(),
        "source": "journal_lines.account_type_BANK", "basis": "current_book_balance",
        "total": money_float(total) if status == "complete" else None,
        "known_total": money_float(total) if accounts else None,
        "rows": rows, "account_count": len(rows),
        "detail": "Ada rekening tanpa tautan akun Kas/Bank yang valid; total belum lengkap." if unmapped else ("Belum ada akun Kas/Bank." if not accounts else "Saldo pembukuan seluruh kas dan bank sampai hari ini."),
    }
