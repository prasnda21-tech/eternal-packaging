"""Generic CSV/XLSX bank-statement import. No bank-specific column layout.

Preview only reads and validates. It does not store the file, a preview id,
or any other document. Import receives the original bytes again, recomputes
the fingerprint, and re-parses on the server. Statement rows are not journals.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import os
import time
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bson.binary import Binary
from openpyxl import load_workbook
from pymongo.errors import DuplicateKeyError

from services.cash_bank_master import (
    COLLECTION as CASH_BANK_ACCOUNTS,
    CURRENCY_WRITER,
    CURRENCY_WRITER_UNTIL,
    CashBankValidationError,
    IMPORT_READERS,
    _writer_takeover_filter,
    coordination_deadline,
    get_cash_bank_raw,
    normalize_currency_code,
)
from services.purchase_common import actor_snapshot, now_iso

IMPORTS = "cash_bank_statement_imports"
LINES = "cash_bank_statement_lines"
PREVIEWS = "cash_bank_statement_previews"
MANUAL_BALANCES = "cash_bank_manual_statement_balances"
IMPORT_FENCES = "cash_bank_import_fences"

MAX_BYTES = 2_000_000
MAX_ROWS = 2000
PENDING_REVIEW_PAGE_DEFAULT = 100
PENDING_REVIEW_PAGE_MAX = 200
DUPLICATE_GROUP_PAGE_DEFAULT = 20
DUPLICATE_GROUP_PAGE_MAX = 50
STALE_REVIEW_MESSAGE = "Daftar berubah. Muat ulang dari awal."
_GROUP_KEY_SEPARATOR = "\x1f"
_WHITESPACE_CHARS = (
    "\t", "\n", "\r", "\f", "\v", "\u00a0", "\u1680",
    "\u2000", "\u2001", "\u2002", "\u2003", "\u2004", "\u2005", "\u2006",
    "\u2007", "\u2008", "\u2009", "\u200a", "\u2028", "\u2029", "\u202f",
    "\u205f", "\u3000",
)
MAX_COLS = 40
MONEY = Decimal("0.01")

DATE_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
}

STATUS_IMPORTING = "importing"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

DUP_CLEAR = "clear"
DUP_PENDING = "pending_review"
DUP_KEPT = "kept"
DUP_EXCLUDED = "excluded"
DUP_DEFINITE = "definite_duplicate"
AUTHORITATIVE_TXN_INDEX = "uniq_authoritative_bank_txn"


async def _before_currency_pin() -> None:
    """Test seam before this commit pins the account currency."""
    return None


async def _after_currency_pin() -> None:
    """Test seam after the currency pin is stored and before statement rows are written."""
    return None


async def _on_import_fence_busy(cash_bank_id: str) -> None:
    """Test seam when another import already holds this account's classification fence."""
    return None


async def _before_statement_lines_insert() -> None:
    """Test seam. Production leaves this empty. Placed after the duplicate scan."""
    return None


async def _after_import_fence_acquired() -> None:
    """Test seam after this attempt owns the fence and before classification."""
    return None


async def _before_duplicate_resolution() -> None:
    """Test seam immediately before the duplicate-resolution compare-and-swap."""
    return None


async def _before_fenced_write(_kind: str) -> None:
    """Test seam after this attempt's last ownership check and before the business write."""
    return None


DISPLACED_WRITER = "displaced"


class StatementValidationError(ValueError):
    def __init__(self, message: str, errors: Optional[List[dict]] = None):
        super().__init__(message)
        self.errors = errors or []


class StatementConflictError(Exception):
    pass


async def _after_statement_line_insert(_index: int) -> None:
    """Test seam after one statement line is durable."""
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapping_hash(mapping: dict) -> str:
    import json
    raw = json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _money(raw: Decimal) -> Decimal:
    return raw.quantize(MONEY)


def _money_float(raw: Decimal) -> float:
    return float(_money(raw))


async def ensure_indexes(db) -> None:
    await db[IMPORTS].create_index("id", unique=True)
    await db[IMPORTS].create_index(
        [("cash_bank_id", 1), ("file_sha256", 1)],
        unique=True,
        name="uniq_statement_file_account",
    )
    await db[IMPORTS].create_index([("cash_bank_id", 1), ("created_at", -1)])
    await db[LINES].create_index("id", unique=True)
    await db[LINES].create_index("import_id")
    await db[LINES].create_index([("cash_bank_id", 1), ("bank_txn_id", 1)])
    await db[LINES].create_index([("cash_bank_id", 1), ("date", 1)])
    await db[LINES].create_index(
        [("cash_bank_id", 1), ("authoritative_txn_id", 1)],
        unique=True,
        partialFilterExpression={"authoritative_txn_id": {"$type": "string"}},
        name=AUTHORITATIVE_TXN_INDEX,
    )
    await db[MANUAL_BALANCES].create_index(
        [("cash_bank_id", 1), ("as_of", 1)],
        unique=True,
        name="uniq_manual_statement_balance",
    )
    await db[IMPORT_FENCES].create_index("cash_bank_id", unique=True, name="uniq_import_fence_account")


def _matrix_from_csv(data: bytes) -> List[List[Any]]:
    text = data.decode("utf-8-sig")
    sample = text[:4096]
    delimiter = ","
    if sample:
        counts = {d: sample.count(d) for d in (",", ";", "\t")}
        delimiter = max(counts, key=counts.get)
        if counts[delimiter] == 0:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for row in reader:
        if len(rows) >= MAX_ROWS:
            raise StatementValidationError(f"Berkas melebihi {MAX_ROWS} baris")
        rows.append(list(row[:MAX_COLS]))
    return rows


def _matrix_from_xlsx(data: bytes) -> List[List[Any]]:
    try:
        wb = load_workbook(
            io.BytesIO(data),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except Exception as exc:
        raise StatementValidationError("Berkas XLSX tidak dapat dibaca") from exc
    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            raise StatementValidationError("Berkas XLSX tidak memiliki lembar")
        rows: List[List[Any]] = []
        # read_only yields stored rows only. data_only stays false so formulas
        # are rejected instead of evaluated. keep_links stays false.
        for cells in ws.iter_rows(max_col=MAX_COLS):
            if len(rows) >= MAX_ROWS:
                raise StatementValidationError(f"Berkas melebihi {MAX_ROWS} baris")
            values = []
            for cell in cells:
                if getattr(cell, "data_type", None) == "f":
                    raise StatementValidationError(
                        "Sel formula tidak dievaluasi dan tidak diimpor"
                    )
                values.append(cell.value)
            rows.append(values)
        return rows
    finally:
        wb.close()


def read_matrix(filename: str, data: bytes) -> List[List[Any]]:
    name = str(filename or "").lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        return _matrix_from_csv(data)
    if name.endswith(".xlsx"):
        return _matrix_from_xlsx(data)
    raise StatementValidationError("Format tidak didukung. Gunakan .csv atau .xlsx")


def _cell(row: Sequence[Any], index: Optional[int]) -> Any:
    if index is None:
        return None
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_date(value: Any, date_format: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()[:10]
        except Exception as exc:
            raise StatementValidationError("Tanggal tidak valid") from exc
    text = _as_text(value)
    if not text:
        raise StatementValidationError("Tanggal kosong")
    if isinstance(value, (int, float)) or text.replace(".", "", 1).isdigit():
        raise StatementValidationError("Tanggal numerik ambigu dan tidak ditebak")
    fmt = DATE_FORMATS.get(date_format)
    if not fmt:
        raise StatementValidationError("Format tanggal tidak didukung")
    try:
        parsed = datetime.strptime(text, fmt)
    except ValueError as exc:
        raise StatementValidationError(
            f"Tanggal '{text}' tidak sesuai format {date_format}"
        ) from exc
    return parsed.date().isoformat()


def parse_amount(value: Any, decimal_mode: str) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return _money(value)
    if isinstance(value, bool):
        raise StatementValidationError("Nominal tidak valid")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _money(Decimal(str(value)))
    text = _as_text(value).replace(" ", "").replace("Rp", "").replace("IDR", "")
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]
    if text.startswith("+"):
        text = text[1:]
    last_comma = text.rfind(",")
    last_dot = text.rfind(".")
    if decimal_mode == "comma_decimal":
        if last_dot != -1 and last_comma != -1 and last_dot > last_comma:
            raise StatementValidationError("Pemisah desimal tidak sesuai mode koma")
        if text.count(",") > 1:
            raise StatementValidationError("Koma desimal ganda")
        body = text.replace(".", "").replace(",", ".")
    elif decimal_mode == "dot_decimal":
        if last_comma != -1 and last_dot != -1 and last_comma > last_dot:
            raise StatementValidationError("Pemisah desimal tidak sesuai mode titik")
        if text.count(".") > 1:
            raise StatementValidationError("Titik desimal ganda atau ribuan ambigu")
        body = text.replace(",", "")
    else:
        raise StatementValidationError("Mode desimal tidak didukung")
    try:
        amount = _money(Decimal(body))
    except (InvalidOperation, ValueError) as exc:
        raise StatementValidationError("Nominal tidak valid") from exc
    if negative:
        amount = -amount
    return amount


def normalize_mapping(raw: dict) -> dict:
    data = dict(raw or {})
    allowed = {
        "header_row", "date_column", "date_format", "description_column",
        "reference_column", "bank_txn_id_column", "bank_txn_id_authoritative",
        "amount_mode", "amount_column",
        "sign_convention", "in_column", "out_column", "balance_column",
        "currency_column", "file_currency", "decimal_mode",
        "operator_coverage_confirmed",
    }
    unknown = set(data) - allowed
    if unknown:
        raise StatementValidationError("Kolom mapping tidak dikenal: " + ", ".join(sorted(unknown)))
    try:
        header_row = int(data.get("header_row", 0))
        date_column = int(data["date_column"])
        description_column = int(data["description_column"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StatementValidationError("Mapping tanggal dan uraian wajib") from exc
    date_format = str(data.get("date_format") or "")
    if date_format not in DATE_FORMATS:
        raise StatementValidationError("Format tanggal wajib dipilih")
    decimal_mode = str(data.get("decimal_mode") or "")
    if decimal_mode not in ("comma_decimal", "dot_decimal"):
        raise StatementValidationError("Mode desimal wajib dipilih")
    amount_mode = str(data.get("amount_mode") or "")
    if amount_mode not in ("signed", "in_out"):
        raise StatementValidationError("Mode nominal wajib: signed atau in_out")

    def opt_int(key: str) -> Optional[int]:
        if key not in data or data.get(key) in (None, ""):
            return None
        return int(data[key])

    mapping = {
        "header_row": header_row,
        "date_column": date_column,
        "date_format": date_format,
        "description_column": description_column,
        "reference_column": opt_int("reference_column"),
        "bank_txn_id_column": opt_int("bank_txn_id_column"),
        "bank_txn_id_authoritative": bool(data.get("bank_txn_id_authoritative")),
        "amount_mode": amount_mode,
        "amount_column": opt_int("amount_column"),
        "sign_convention": str(data.get("sign_convention") or ""),
        "in_column": opt_int("in_column"),
        "out_column": opt_int("out_column"),
        "balance_column": opt_int("balance_column"),
        "currency_column": opt_int("currency_column"),
        "file_currency": None,
        "decimal_mode": decimal_mode,
        "operator_coverage_confirmed": bool(data.get("operator_coverage_confirmed")),
    }
    if data.get("file_currency") not in (None, ""):
        mapping["file_currency"] = normalize_currency_code(data.get("file_currency"))
    if mapping["bank_txn_id_authoritative"] and mapping["bank_txn_id_column"] is None:
        raise StatementValidationError(
            "ID transaksi bank yang otoritatif membutuhkan kolom ID. Kolom referensi tidak dianggap unik."
        )
    if amount_mode == "signed":
        if mapping["amount_column"] is None:
            raise StatementValidationError("Kolom nominal wajib")
        if mapping["sign_convention"] not in ("negative_out", "negative_in"):
            raise StatementValidationError(
                "Konvensi tanda wajib dipilih. Label debit/kredit tidak menentukan arah."
            )
    else:
        if mapping["in_column"] is None or mapping["out_column"] is None:
            raise StatementValidationError("Kolom masuk dan keluar wajib")
        if mapping["in_column"] == mapping["out_column"]:
            raise StatementValidationError("Kolom masuk dan keluar harus berbeda")
    used = [
        mapping["date_column"], mapping["description_column"],
        mapping["reference_column"], mapping["bank_txn_id_column"],
        mapping["amount_column"], mapping["in_column"], mapping["out_column"],
        mapping["balance_column"], mapping["currency_column"],
    ]
    concrete = [n for n in used if n is not None]
    if len(concrete) != len(set(concrete)):
        raise StatementValidationError("Satu kolom tidak boleh dipakai dua kali")
    return mapping


def _direction_from_signed(amount: Decimal, convention: str) -> Tuple[str, Decimal]:
    if amount == 0:
        raise StatementValidationError("Nominal nol tidak punya arah")
    if convention == "negative_out":
        direction = "out" if amount < 0 else "in"
    elif convention == "negative_in":
        direction = "in" if amount < 0 else "out"
    else:
        raise StatementValidationError("Konvensi tanda tidak valid")
    return direction, abs(amount)


def interpret_rows(matrix: List[List[Any]], mapping: dict) -> dict:
    """Return accepted rows plus per-row errors. Does not write."""
    header_row = mapping["header_row"]
    if header_row < 0 or header_row >= len(matrix):
        raise StatementValidationError("Baris header di luar berkas")
    errors: List[dict] = []
    accepted: List[dict] = []
    data_rows = matrix[header_row + 1:]
    if not data_rows:
        raise StatementValidationError("Tidak ada baris data di bawah header")
    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    closing = None
    closing_date = None
    for offset, row in enumerate(data_rows, start=1):
        source_row = header_row + 1 + offset
        if not any(_as_text(c) for c in row):
            continue
        try:
            if any(isinstance(c, str) and c.strip().startswith("=") for c in row):
                raise StatementValidationError("Teks formula tidak diimpor")
            date = parse_date(_cell(row, mapping["date_column"]), mapping["date_format"])
            description = _as_text(_cell(row, mapping["description_column"]))
            if not description:
                raise StatementValidationError("Uraian kosong")
            reference = _as_text(_cell(row, mapping["reference_column"])) or None
            bank_txn_id = _as_text(_cell(row, mapping["bank_txn_id_column"])) or None
            if mapping["amount_mode"] == "signed":
                amount = parse_amount(_cell(row, mapping["amount_column"]), mapping["decimal_mode"])
                if amount is None:
                    raise StatementValidationError("Nominal kosong")
                direction, absolute = _direction_from_signed(amount, mapping["sign_convention"])
            else:
                money_in = parse_amount(_cell(row, mapping["in_column"]), mapping["decimal_mode"])
                money_out = parse_amount(_cell(row, mapping["out_column"]), mapping["decimal_mode"])
                money_in = money_in or Decimal("0.00")
                money_out = money_out or Decimal("0.00")
                if money_in < 0 or money_out < 0:
                    raise StatementValidationError("Kolom masuk/keluar tidak boleh negatif")
                if money_in > 0 and money_out > 0:
                    raise StatementValidationError("Baris tidak boleh berisi masuk dan keluar sekaligus")
                if money_in == 0 and money_out == 0:
                    raise StatementValidationError("Nominal masuk/keluar kosong")
                if money_in > 0:
                    direction, absolute = "in", money_in
                else:
                    direction, absolute = "out", money_out
            balance = None
            if mapping["balance_column"] is not None:
                balance = parse_amount(_cell(row, mapping["balance_column"]), mapping["decimal_mode"])
            currency = mapping["file_currency"]
            if mapping["currency_column"] is not None:
                raw_ccy = _as_text(_cell(row, mapping["currency_column"]))
                cell_ccy = normalize_currency_code(raw_ccy) if raw_ccy else None
                if currency and cell_ccy and currency != cell_ccy:
                    raise StatementValidationError("Mata uang sel berbeda dari mata uang berkas")
                currency = cell_ccy or currency
            if direction == "in":
                total_in += absolute
            else:
                total_out += absolute
            if balance is not None:
                closing = balance
                closing_date = date
            accepted.append({
                "source_row": source_row,
                "row_number": offset,
                "date": date,
                "description": description,
                "reference": reference,
                "bank_txn_id": bank_txn_id,
                "direction": direction,
                "amount": _money_float(absolute),
                "running_balance": None if balance is None else _money_float(balance),
                "currency_code": currency,
            })
        except (StatementValidationError, CashBankValidationError) as exc:
            errors.append({"row": source_row, "message": str(exc)})
    return {
        "errors": errors,
        "accepted": accepted,
        "accepted_count": len(accepted),
        "total_in": _money_float(total_in),
        "total_out": _money_float(total_out),
        "closing_balance": None if closing is None else _money_float(closing),
        "closing_date": closing_date,
        "headers": [_as_text(c) for c in matrix[header_row]],
    }


def _sample(matrix: List[List[Any]], limit: int = 8) -> List[List[str]]:
    out = []
    for row in matrix[:limit]:
        out.append([_as_text(c) for c in row])
    return out


def _currency_note() -> str:
    return "Konteks mata uang rekening saat pratinjau. Penetapan operator, bukan bukti independen bank."


def _preview_secret() -> bytes:
    secret = os.environ.get("JWT_SECRET") or ""
    if len(secret) < 16:
        raise StatementConflictError("Kunci pratinjau tidak tersedia")
    return secret.encode("utf-8")


def account_binding(cash_bank_id: str, currency: Optional[str], file_sha256: str) -> str:
    """Bind a preview to the account and currency the server just read.

    The value is an HMAC. It does not contain file bytes, and a client-supplied
    currency is not an input.
    """
    payload = "|".join([str(cash_bank_id), currency or "", str(file_sha256)]).encode("utf-8")
    return hmac.new(_preview_secret(), payload, hashlib.sha256).hexdigest()


def _binding_matches(expected: str, supplied: Optional[str]) -> bool:
    got = str(supplied or "").strip().lower()
    if len(got) != len(expected):
        return False
    return hmac.compare_digest(expected, got)


def _require_same_account(cash_bank_id: str, currency: Optional[str], file_sha256: str, supplied: Optional[str]) -> None:
    expected = account_binding(cash_bank_id, currency, file_sha256)
    if not _binding_matches(expected, supplied):
        raise StatementConflictError(
            "Rekening tidak boleh diganti setelah pratinjau. Mata uang rekening juga diperiksa ulang dari data server. "
            "Commit tidak dilanjutkan."
        )


def _bca_layout(mapping_raw: dict) -> bool:
    if not isinstance(mapping_raw, dict) or mapping_raw.get("layout") != "bca":
        return False
    if set(mapping_raw) != {"layout"}:
        raise StatementValidationError("Mapping BCA tidak valid")
    return True


async def create_bca_preview(db, *, cash_bank_id: str, filename: str, data: bytes, user: Optional[dict]) -> dict:
    """Parse a BCA CSV and bind it to the server account. This function does not write."""
    del user
    cb_id = str(cash_bank_id or "").strip()
    if not cb_id:
        raise StatementValidationError("Kas/Bank wajib dipilih")
    account = await get_cash_bank_raw(db, cb_id)
    if not account:
        raise StatementValidationError("Kas/Bank tidak ditemukan")
    from services.cash_bank_statement_bca import apply_pend_import_date, jakarta_import_date, parse_bca_statement
    parsed = parse_bca_statement(filename, data)
    when = jakarta_import_date()
    parsed["rows"] = [apply_pend_import_date(row, when) for row in parsed["rows"]]
    currency = account.get("currency_code") or None
    file_sha256 = _sha256(data)
    parsed.update({
        "fingerprint": file_sha256,
        "account_binding": account_binding(cb_id, currency, file_sha256),
        "cash_bank_id": cb_id,
        "filename": str(filename or "mutasi.csv"),
        "currency_code": currency,
        "account_currency_code": currency,
        "currency_context_note": _currency_note(),
        "persisted_statement_lines": False,
        "stored": False,
    })
    return parsed


async def create_preview(db, *, cash_bank_id: str, filename: str, data: bytes, user: Optional[dict]) -> dict:
    """Read the account and parse the upload. This function does not write."""
    del user
    cb_id = str(cash_bank_id or "").strip()
    if not cb_id:
        raise StatementValidationError("Kas/Bank wajib dipilih")
    cb = await get_cash_bank_raw(db, cb_id)
    if not cb:
        raise StatementValidationError("Kas/Bank tidak ditemukan")
    if not data:
        raise StatementValidationError("Berkas kosong")
    if len(data) > MAX_BYTES:
        raise StatementValidationError("Berkas melebihi batas 2 MB")
    matrix = read_matrix(filename, data)
    if not matrix:
        raise StatementValidationError("Berkas tidak berisi baris")
    currency = cb.get("currency_code") or None
    file_sha256 = _sha256(data)
    return {
        "fingerprint": file_sha256,
        "account_binding": account_binding(cb_id, currency, file_sha256),
        "cash_bank_id": cb_id,
        "filename": str(filename or "mutasi"),
        "row_count": len(matrix),
        "sample_rows": _sample(matrix),
        "currency_code": currency,
        "account_currency_code": currency,
        "currency_context_note": _currency_note(),
        "persisted_statement_lines": False,
        "stored": False,
    }


async def validate_preview(
    db,
    *,
    cash_bank_id: str,
    filename: str,
    data: bytes,
    mapping_raw: dict,
    account_binding_value: Optional[str] = None,
) -> dict:
    """Re-read the uploaded file and validate the mapping. This function does not write."""
    if not data:
        raise StatementValidationError("Berkas tidak dikirim ulang. Unggah berkas asli kembali.")
    if len(data) > MAX_BYTES:
        raise StatementValidationError("Berkas melebihi batas 2 MB")
    cb_id = str(cash_bank_id or "").strip()
    account = await get_cash_bank_raw(db, cb_id)
    if not account:
        raise StatementValidationError("Kas/Bank tidak ditemukan")
    file_sha256 = _sha256(data)
    _require_same_account(cb_id, account.get("currency_code") or None, file_sha256, account_binding_value)
    mapping = normalize_mapping(mapping_raw)
    matrix = read_matrix(filename or "", data)
    parsed = interpret_rows(matrix, mapping)
    return {
        "fingerprint": _sha256(data),
        "ok": not parsed["errors"],
        "errors": parsed["errors"],
        "accepted_count": parsed["accepted_count"],
        "total_in": parsed["total_in"],
        "total_out": parsed["total_out"],
        "closing_balance": parsed["closing_balance"],
        "closing_date": parsed["closing_date"],
        "headers": parsed["headers"],
        "sample_accepted": [
            {key: row.get(key) for key in ("row_number", "date", "description", "direction", "amount", "reference")}
            for row in parsed["accepted"][:8]
        ],
        "persisted_statement_lines": False,
        "mapping_hash": _mapping_hash(mapping),
    }


def _line_identity(row: dict) -> str:
    return "|".join([
        str(row.get("date") or ""),
        str(row.get("direction") or ""),
        str(row.get("amount") or ""),
        str(row.get("description") or ""),
        str(row.get("bank_txn_id") or ""),
        str(row.get("reference") or ""),
        str(row.get("currency_code") or ""),
        str(row.get("duplicate_status") or ""),
        str(row.get("authoritative_txn_id") or ""),
    ])


def _amount_key(value: Any) -> str:
    try:
        return format(_money(Decimal(str(value))), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def _normalized_description(value: Any) -> str:
    """Collapse whitespace and case only. Tokens inside the text stay in place."""
    return " ".join(str(value or "").lower().split())


def _normalized_reference(value: Any) -> str:
    """Blank reference stays blank. A present reference is part of the recognition tuple, not a unique bank id."""
    return " ".join(str(value or "").lower().split())


def _balance_key(value: Any):
    if value is None or value == "":
        return None
    return _amount_key(value)


def _supporting_same(row: dict, stored: dict) -> bool:
    """Date, direction, and amount. Used only for an authoritative bank transaction id."""
    return (
        str(row.get("direction") or "") == str(stored.get("direction") or "")
        and _amount_key(row.get("amount")) == _amount_key(stored.get("amount"))
        and _business_date(row) == _business_date(stored)
    )


def _display_same(row: dict, stored: dict) -> bool:
    return (
        (row.get("description") or "") == (stored.get("description") or "")
        and (row.get("reference") or None) == (stored.get("reference") or None)
        and _balance_key(row.get("running_balance")) == _balance_key(stored.get("running_balance"))
    )


def _business_date(row: dict) -> str:
    """PEND stays the literal token. A dated row uses its business date."""
    if str(row.get("original_date") or "") == "PEND":
        return "PEND"
    return str(row.get("date") or "")


def _level2_key(row: dict) -> Tuple[str, str, str, str, str]:
    """Reimport recognition inside one account. This is not a bank uniqueness guarantee.

    A repeated exact tuple is paired by occurrence. A different reference, or a
    different token left inside the full description, is a different tuple.
    """
    return (
        str(row.get("direction") or ""),
        _amount_key(row.get("amount")),
        _normalized_description(row.get("description")),
        _business_date(row),
        _normalized_reference(row.get("reference")),
    )


def _direction_totals(rows: Sequence[dict]) -> Tuple[Decimal, Decimal]:
    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    for row in rows:
        amount = Decimal(str(row.get("amount") or 0))
        if row.get("direction") == "in":
            total_in += amount
        elif row.get("direction") == "out":
            total_out += amount
    return _money(total_in), _money(total_out)


def _rows_to_insert(rows: Sequence[dict]) -> List[dict]:
    return [row for row in rows if row.get("import_disposition") == "insert"]


async def _account_import_epoch(db, cash_bank_id: str) -> int:
    account = await db[CASH_BANK_ACCOUNTS].find_one(
        {"id": str(cash_bank_id)}, {"_id": 0, "import_epoch": 1},
    )
    return int((account or {}).get("import_epoch") or 0)


def _epoch_clause(epoch: int) -> Dict[str, Any]:
    """Lines written before this field existed stay on generation 0."""
    if int(epoch) == 0:
        return {"$or": [{"import_epoch": 0}, {"import_epoch": {"$exists": False}}]}
    return {"import_epoch": int(epoch)}


async def _bump_import_epoch(db, cash_bank_id: str) -> None:
    """Advance the classification generation. Does not change amounts or decisions."""
    cash_bank_id = str(cash_bank_id)
    await db[CASH_BANK_ACCOUNTS].update_one({"id": cash_bank_id}, {"$inc": {"import_epoch": 1}})
    await db[LINES].update_many({"cash_bank_id": cash_bank_id}, {"$inc": {"import_epoch": 1}})


async def _reassign_stale_import_writers(
    db, cash_bank_id: str, new_attempt: Optional[str], keep_batch_id: Optional[str],
) -> None:
    """The fence holder adopts every importing writer except itself.

    A previous attempt can die after the fence compare-and-swap and before its
    own writer id was stored. Matching only that dead holder would leave the
    older writer in place.
    """
    cash_bank_id = str(cash_bank_id)
    owner = str(new_attempt or "")
    if keep_batch_id and owner:
        await db[IMPORTS].update_one(
            {
                "id": keep_batch_id,
                "status": STATUS_IMPORTING,
                "writer_attempt": {"$ne": owner},
            },
            {"$set": {"writer_attempt": owner}},
        )
        await db[LINES].update_many(
            {
                "import_id": keep_batch_id,
                "sealed": False,
                "writer_attempt": {"$ne": owner},
            },
            {"$set": {"writer_attempt": owner}},
        )
    batch_filter: Dict[str, Any] = {
        "cash_bank_id": cash_bank_id,
        "status": STATUS_IMPORTING,
        "writer_attempt": {"$ne": owner},
    }
    line_filter: Dict[str, Any] = {
        "cash_bank_id": cash_bank_id,
        "sealed": False,
        "writer_attempt": {"$ne": owner},
    }
    if keep_batch_id:
        batch_filter["id"] = {"$ne": keep_batch_id}
        line_filter["import_id"] = {"$ne": keep_batch_id}
    await db[IMPORTS].update_many(batch_filter, {"$set": {"writer_attempt": DISPLACED_WRITER}})
    await db[LINES].update_many(line_filter, {"$set": {"writer_attempt": DISPLACED_WRITER}})


async def _displace_attempt_writes(
    db, cash_bank_id: str, old_holder: Optional[str], new_attempt: Optional[str], keep_batch_id: Optional[str],
) -> None:
    """Used when a fence is cleared without a new holder. Generation moves first."""
    await _bump_import_epoch(db, cash_bank_id)
    if not old_holder and not new_attempt:
        await _reassign_stale_import_writers(db, cash_bank_id, None, keep_batch_id)
        return
    if new_attempt:
        await _reassign_stale_import_writers(db, cash_bank_id, new_attempt, keep_batch_id)
        return
    old_holder = str(old_holder)
    cash_bank_id = str(cash_bank_id)
    await db[IMPORTS].update_many(
        {
            "cash_bank_id": cash_bank_id,
            "status": STATUS_IMPORTING,
            "writer_attempt": old_holder,
        },
        {"$set": {"writer_attempt": DISPLACED_WRITER}},
    )
    await db[LINES].update_many(
        {
            "cash_bank_id": cash_bank_id,
            "sealed": False,
            "writer_attempt": old_holder,
        },
        {"$set": {"writer_attempt": DISPLACED_WRITER}},
    )


def _mark_conflict(row: dict, reason: str) -> None:
    row["import_disposition"] = "conflict"
    row["import_outcome"] = "conflict"
    row["conflict_reason"] = reason
    row["authoritative_txn_id"] = None
    row["authoritative_refresh"] = False


async def _flag_duplicates(
    db,
    cash_bank_id: str,
    rows: List[dict],
    *,
    authoritative: bool,
    ignore_import_id: Optional[str] = None,
    attempt: Optional[dict] = None,
) -> None:
    """Classify each incoming row. This pass does not write stored lines.

    An authoritative bank transaction id is the only code that can conflict.
    A mapped reference is only one field of the recognition tuple. Identical
    tuples stay as separate occurrences and are not marked pending_review.
    """
    del attempt
    seen_txn: Dict[str, int] = {}
    for idx, row in enumerate(rows):
        row["duplicate_status"] = DUP_CLEAR
        row["duplicate_reason"] = None
        row["duplicate_of_line_id"] = None
        row["authoritative_txn_id"] = None
        row["import_disposition"] = "insert"
        row["import_outcome"] = "insert"
        row["reused_line_id"] = None
        row["conflict_reason"] = None
        row["authoritative_refresh"] = False
        txn = str(row.get("bank_txn_id") or "").strip()
        if authoritative and txn:
            row["identity_guarantee"] = "authoritative"
            if txn in seen_txn:
                first = rows[seen_txn[txn]]
                same = _supporting_same(row, first)
                _mark_conflict(row, "id_otoritatif_berulang" if same else "id_otoritatif_bertentangan")
                if not same:
                    _mark_conflict(first, "id_otoritatif_bertentangan")
            else:
                seen_txn[txn] = idx
                row["authoritative_txn_id"] = txn
        else:
            row["identity_guarantee"] = "tidak_dijamin"
            row["duplicate_of_bank_txn_id"] = None

    if authoritative and seen_txn:
        query: Dict[str, Any] = {
            "cash_bank_id": cash_bank_id,
            "authoritative_txn_id": {"$in": list(seen_txn)},
        }
        if ignore_import_id:
            query["import_id"] = {"$ne": ignore_import_id}
        existing = await db[LINES].find(
            query,
            {
                "_id": 0, "authoritative_txn_id": 1, "id": 1, "date": 1,
                "original_date": 1, "direction": 1, "amount": 1, "duplicate_status": 1,
            },
        ).to_list(len(seen_txn) + 5)
        known = {doc.get("authoritative_txn_id"): doc for doc in existing}
        for row in rows:
            if row.get("import_outcome") == "conflict":
                continue
            txn = row.get("authoritative_txn_id")
            stored = known.get(txn) if txn else None
            if not stored:
                continue
            row["reused_line_id"] = stored.get("id")
            status = stored.get("duplicate_status")
            if status == DUP_EXCLUDED:
                _mark_conflict(row, "id_otoritatif_pada_baris_dikecualikan")
                continue
            if not _supporting_same(row, stored):
                _mark_conflict(row, "id_otoritatif_bertentangan")
                continue
            row["import_disposition"] = "reuse"
            row["authoritative_txn_id"] = None
            if status == DUP_PENDING:
                row["import_outcome"] = "unchanged"
            else:
                row["import_outcome"] = "refresh"
                row["authoritative_refresh"] = True
    await _plan_level2_reuse(db, cash_bank_id, rows, ignore_import_id)


async def _level2_candidates(
    db, cash_bank_id: str, sample: dict, ignore_import_id: Optional[str], *, excluded: bool,
) -> List[dict]:
    business_date = _business_date(sample)
    query: Dict[str, Any] = {
        "cash_bank_id": cash_bank_id,
        "direction": sample.get("direction"),
        "identity_guarantee": "tidak_dijamin",
        "sealed": {"$ne": False},
    }
    if excluded:
        query["duplicate_status"] = DUP_EXCLUDED
    else:
        query["duplicate_status"] = {"$ne": DUP_EXCLUDED}
    if business_date == "PEND":
        query["original_date"] = "PEND"
    else:
        query["date"] = sample.get("date")
    if ignore_import_id:
        query["import_id"] = {"$ne": ignore_import_id}
    found = await db[LINES].find(
        query,
        {
            "_id": 0, "id": 1, "description": 1, "amount": 1,
            "direction": 1, "date": 1, "original_date": 1, "reference": 1,
            "duplicate_status": 1, "active_claim": 1,
        },
    ).to_list(MAX_ROWS + 5)
    wanted_amount = _amount_key(sample.get("amount"))
    wanted_text = _normalized_description(sample.get("description"))
    wanted_reference = _normalized_reference(sample.get("reference"))
    matched = [
        doc for doc in found
        if _amount_key(doc.get("amount")) == wanted_amount
        and _normalized_description(doc.get("description")) == wanted_text
        and _business_date(doc) == business_date
        and _normalized_reference(doc.get("reference")) == wanted_reference
    ]
    matched.sort(key=lambda doc: str(doc.get("id") or ""))
    return matched


async def _plan_level2_reuse(
    db, cash_bank_id: str, rows: List[dict], ignore_import_id: Optional[str],
) -> None:
    """Pair exact recognition tuples by occurrence. Excluded lines are not candidates.

    Pending historical lines can occupy a slot so a reimport does not add
    another copy, but this function never rewrites them.
    """
    groups: Dict[Tuple[str, str, str, str, str], List[dict]] = {}
    for row in rows:
        if row.get("identity_guarantee") == "authoritative":
            continue
        if row.get("import_disposition") in ("reuse", "conflict"):
            continue
        groups.setdefault(_level2_key(row), []).append(row)
    for group in groups.values():
        existing = await _level2_candidates(
            db, cash_bank_id, group[0], ignore_import_id, excluded=False,
        )
        existing.sort(key=lambda doc: (
            1 if doc.get("duplicate_status") == DUP_PENDING else 0,
            str(doc.get("id") or ""),
        ))
        matched_ids = await _active_match_ids(db, [doc.get("id") for doc in existing])
        for index, row in enumerate(group):
            if index >= len(existing):
                row["import_disposition"] = "insert"
                row["import_outcome"] = "insert"
                row["duplicate_status"] = DUP_CLEAR
                continue
            target = existing[index]
            row["import_disposition"] = "reuse"
            row["reused_line_id"] = target.get("id")
            if target.get("duplicate_status") == DUP_PENDING:
                row["import_outcome"] = "unchanged"
                continue
            if target.get("active_claim") or target.get("id") in matched_ids:
                row["import_outcome"] = "protected"
                continue
            row["import_outcome"] = "refresh"


async def _active_match_ids(db, line_ids: Sequence[Any]) -> set:
    ids = [str(line_id) for line_id in line_ids if line_id]
    if not ids:
        return set()
    from services.cash_bank_reconciliation import RECON_MATCHES
    found = await db[RECON_MATCHES].find(
        {"statement_line_id": {"$in": ids}, "status": "active"},
        {"_id": 0, "statement_line_id": 1},
    ).to_list(len(ids) + 5)
    return {doc.get("statement_line_id") for doc in found if doc.get("statement_line_id")}


async def _before_reimport_write(line_id: str) -> None:
    """Test seam before the statement line claim is taken for an overwrite."""
    del line_id
    return None


async def _while_reimport_hold(line_id: str) -> None:
    """Test seam after this import holds the line and before fields are written."""
    del line_id
    return None


def _audit_id(batch_id: str, line_id: str, after: dict) -> str:
    raw = "|".join([
        str(batch_id),
        str(line_id),
        str(after.get("description") or ""),
        str(after.get("reference") or ""),
        str(_balance_key(after.get("running_balance"))),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


CONFLICT_TEXT = {
    "id_otoritatif_bertentangan": "ID transaksi bank sama, tetapi tanggal, arah, atau nominal berbeda",
    "id_otoritatif_berulang": "ID transaksi bank otoritatif muncul lebih dari sekali dalam berkas",
    "id_otoritatif_pada_baris_dikecualikan": "ID transaksi bank milik baris yang dikecualikan dan tidak diubah",
    "id_otoritatif_sudah_tersimpan": "ID transaksi bank otoritatif sudah tersimpan. Baris ini tidak ditambahkan dan tidak menimpa.",
}


def _public_conflict(row: dict) -> dict:
    reason = row.get("conflict_reason") or "konflik_identitas"
    return {
        "source_row": row.get("source_row"),
        "reason": reason,
        "message": CONFLICT_TEXT.get(reason, "Identitas impor bertentangan dan tidak ditimpa"),
        "bank_txn_id": row.get("bank_txn_id") or row.get("duplicate_of_bank_txn_id"),
        "statement_line_id": row.get("reused_line_id"),
    }


def _outcome_counts(rows: Sequence[dict]) -> dict:
    new_count = updated_count = protected_count = unchanged_count = conflict_count = 0
    conflicts = []
    for row in rows:
        outcome = row.get("import_outcome")
        if outcome == "conflict" or row.get("import_disposition") == "conflict":
            conflict_count += 1
            conflicts.append(_public_conflict(row))
        elif outcome == "updated":
            updated_count += 1
        elif outcome == "protected":
            protected_count += 1
        elif outcome == "insert" or row.get("import_disposition") == "insert":
            new_count += 1
        else:
            unchanged_count += 1
    fully = conflict_count == 0
    return {
        "new_count": new_count,
        "updated_count": updated_count,
        "protected_count": protected_count,
        "unchanged_count": unchanged_count,
        "conflict_count": conflict_count,
        "conflicts": conflicts,
        "fully_accepted": fully,
        "result_note": (
            "Semua baris impor diterima. Jurnal tidak dibuat."
            if fully else
            "Impor belum menerima semua transaksi. Jurnal tidak dibuat."
        ),
        "reused_count": updated_count + unchanged_count + protected_count,
    }


def _stamp_outcome(batch: dict, rows: Sequence[dict]) -> dict:
    summary = _outcome_counts(rows)
    batch.update(summary)
    batch["outcome_ready"] = True
    batch["reused_line_ids"] = [
        row.get("reused_line_id")
        for row in rows
        if row.get("import_disposition") == "reuse" and row.get("reused_line_id")
    ]
    return summary


def _idempotent_file_result(existing: dict, accepted_count: int) -> dict:
    body = public_import(existing) or {}
    body.update({
        "new_count": 0,
        "updated_count": 0,
        "protected_count": 0,
        "unchanged_count": accepted_count,
        "conflict_count": 0,
        "conflicts": [],
        "fully_accepted": True,
        "result_note": "Berkas yang sama sudah diimpor. Tidak ada transaksi baru.",
    })
    return body


async def _apply_reimport_updates(db, batch: dict, rows: Sequence[dict], actor: dict, attempt: dict) -> None:
    """Overwrite only while this import holds a claim the matcher also uses.

    A prior read of active_claim is not the guard. The compare-and-swap is.
    """
    from services.cash_bank_reconciliation import RECON_MATCHES
    for row in rows:
        if row.get("import_disposition") != "reuse" or row.get("import_outcome") != "refresh":
            continue
        line_id = str(row.get("reused_line_id") or "")
        if not line_id:
            row["import_disposition"] = "insert"
            row["import_outcome"] = "insert"
            continue
        hold = f"import-hold:{batch['id']}:{line_id}"
        await _assert_import_attempt(db, attempt)
        await _before_reimport_write(line_id)
        active = await db[RECON_MATCHES].find_one(
            {"statement_line_id": line_id, "status": "active"},
            {"_id": 0, "id": 1},
        )
        if active:
            row["import_outcome"] = "protected"
            row["protected_reason"] = "match_aktif"
            continue
        claimed = await db[LINES].update_one(
            {
                "id": line_id,
                "duplicate_status": {"$in": [DUP_CLEAR, DUP_KEPT]},
                "active_claim": None,
            },
            {"$set": {"active_claim": hold}},
        )
        if claimed.matched_count != 1:
            row["import_outcome"] = "protected"
            row["protected_reason"] = "klaim_aktif"
            continue
        try:
            await _assert_import_attempt(db, attempt)
            await _while_reimport_hold(line_id)
            active = await db[RECON_MATCHES].find_one(
                {"statement_line_id": line_id, "status": "active"},
                {"_id": 0, "id": 1},
            )
            if active:
                row["import_outcome"] = "protected"
                row["protected_reason"] = "match_aktif"
                continue
            current = await db[LINES].find_one(
                {"id": line_id, "active_claim": hold},
                {"_id": 0},
            )
            if not current or current.get("duplicate_status") not in (DUP_CLEAR, DUP_KEPT):
                row["import_outcome"] = "protected"
                row["protected_reason"] = "status_baris"
                continue
            after = {
                "description": row.get("description"),
                "reference": row.get("reference"),
                "running_balance": row.get("running_balance"),
            }
            if _display_same(row, current):
                row["import_outcome"] = "unchanged"
                continue
            if not row.get("authoritative_refresh"):
                after["description"] = current.get("description")
                after["reference"] = current.get("reference")
                if _balance_key(after["running_balance"]) == _balance_key(current.get("running_balance")):
                    row["import_outcome"] = "unchanged"
                    continue
            audit = {
                "id": _audit_id(batch["id"], line_id, after),
                "before": {
                    "description": current.get("description"),
                    "reference": current.get("reference"),
                    "running_balance": current.get("running_balance"),
                },
                "after": after,
                "actor": actor,
                "at": now_iso(),
                "source_batch_id": batch["id"],
                "source_row": row.get("source_row"),
            }
            refreshed = dict(current)
            refreshed["description"] = after["description"]
            refreshed["reference"] = after["reference"]
            written = await db[LINES].update_one(
                {
                    "id": line_id,
                    "active_claim": hold,
                    "duplicate_status": current.get("duplicate_status"),
                    "import_updates.id": {"$ne": audit["id"]},
                },
                {"$set": {
                    "description": after["description"],
                    "reference": after["reference"],
                    "running_balance": after["running_balance"],
                    "source_row": row.get("source_row"),
                    "last_import_id": batch["id"],
                    "identity": _line_identity(refreshed),
                }, "$push": {"import_updates": audit}},
            )
            if written.matched_count == 1:
                row["import_outcome"] = "updated"
                continue
            still = await db[LINES].find_one(
                {"id": line_id},
                {"_id": 0, "import_updates": 1, "active_claim": 1},
            )
            events = (still or {}).get("import_updates") or []
            if any(event.get("id") == audit["id"] for event in events):
                row["import_outcome"] = "unchanged"
            elif still and still.get("active_claim") != hold:
                row["import_outcome"] = "protected"
                row["protected_reason"] = "klaim_aktif"
            else:
                row["import_outcome"] = "unchanged"
        finally:
            await db[LINES].update_one(
                {"id": line_id, "active_claim": hold},
                {"$unset": {"active_claim": ""}},
            )


async def _acknowledge_reused_rows(db, rows: Sequence[dict], attempt: Optional[dict]) -> None:
    """Fence check for a repeat that will not insert. Does not rewrite lines."""
    if not any(row.get("import_disposition") == "reuse" for row in rows):
        return
    if attempt:
        await _assert_import_attempt(db, attempt)
    await _before_fenced_write("reuse")
    if attempt:
        await _assert_import_attempt(db, attempt)


def _outcome_recorded(doc: dict) -> bool:
    """Batches stored before outcome counts existed must not look fully accepted."""
    return any(key in doc for key in ("outcome_ready", "new_count", "fully_accepted"))


def _outcome_count(doc: dict, key: str):
    if not _outcome_recorded(doc):
        return None
    return int(doc.get(key) or 0)


def public_import(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    recorded = _outcome_recorded(doc)
    return {
        "id": doc.get("id"),
        "cash_bank_id": doc.get("cash_bank_id"),
        "filename": doc.get("filename"),
        "file_sha256": doc.get("file_sha256"),
        "mapping_hash": doc.get("mapping_hash"),
        "mapping": doc.get("mapping"),
        "status": doc.get("status"),
        "accepted_count": doc.get("accepted_count"),
        "stored_count": doc.get("stored_count"),
        "total_in": doc.get("total_in"),
        "total_out": doc.get("total_out"),
        "closing_balance": doc.get("closing_balance"),
        "closing_date": doc.get("closing_date"),
        "operator_coverage_confirmed": bool(doc.get("operator_coverage_confirmed")),
        "coverage_note": (
            "Cakupan dikonfirmasi operator, bukan verifikasi bank"
            if doc.get("operator_coverage_confirmed")
            else "Cakupan impor tidak diketahui"
        ),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "completed_at": doc.get("completed_at"),
        "reused_count": int(doc.get("reused_count") or 0),
        "new_count": _outcome_count(doc, "new_count"),
        "updated_count": _outcome_count(doc, "updated_count"),
        "protected_count": _outcome_count(doc, "protected_count"),
        "unchanged_count": _outcome_count(doc, "unchanged_count"),
        "conflict_count": _outcome_count(doc, "conflict_count"),
        "conflicts": (doc.get("conflicts") or []) if recorded else None,
        "fully_accepted": bool(doc.get("fully_accepted")) if recorded else None,
        "result_note": (doc.get("result_note") or "") if recorded else "",
    }


async def _stored_totals(db, import_id: str) -> Tuple[int, Decimal, Decimal]:
    rows = await db[LINES].find(
        {"import_id": import_id, "sealed": {"$ne": False}},
        {"_id": 0, "direction": 1, "amount": 1},
    ).to_list(MAX_ROWS + 5)
    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    for row in rows:
        amount = Decimal(str(row.get("amount") or 0))
        if row.get("direction") == "in":
            total_in += amount
        elif row.get("direction") == "out":
            total_out += amount
    return len(rows), _money(total_in), _money(total_out)


def _dup_fields(exc: DuplicateKeyError) -> set:
    details = getattr(exc, "details", None) or {}
    pattern = details.get("keyPattern") or {}
    if isinstance(pattern, dict) and pattern:
        return set(pattern)
    text = str(exc)
    return {part for part in ("authoritative_txn_id", "id") if part in text}


def _seal_filter(line_id: str, attempt_id: str, epoch: int) -> Dict[str, Any]:
    return {
        "id": line_id,
        "writer_attempt": str(attempt_id),
        "sealed": False,
        **_epoch_clause(epoch),
    }


async def _ensure_unsealed_slot(db, batch: dict, row: dict, attempt_id: str, epoch: int) -> str:
    """Reservation only. Business amount and classification are written by the seal."""
    line_id = f"{batch['id']}:{row['row_number']}"
    existing = await db[LINES].find_one({"id": line_id}, {"_id": 0, "id": 1})
    if existing:
        return line_id
    try:
        await db[LINES].insert_one({
            "id": line_id,
            "import_id": batch["id"],
            "cash_bank_id": batch["cash_bank_id"],
            "row_number": row["row_number"],
            "writer_attempt": str(attempt_id),
            "sealed": False,
            "duplicate_status": "unsealed",
            "import_epoch": int(epoch),
        })
    except DuplicateKeyError:
        pass
    return line_id


async def _insert_prepared_lines(db, batch: dict, rows: List[dict], actor: dict, attempt: dict) -> List[dict]:
    await _assert_import_attempt(db, attempt)
    await _before_statement_lines_insert()
    await _assert_import_attempt(db, attempt)
    now = now_iso()
    stored: List[dict] = []
    for index, row in enumerate(rows):
        await _assert_import_attempt(db, attempt)
        epoch = await _account_import_epoch(db, batch["cash_bank_id"])
        line_id = await _ensure_unsealed_slot(db, batch, row, attempt["attempt_id"], epoch)
        await _assert_import_attempt(db, attempt)
        await _before_fenced_write("line")
        doc = _line_doc(batch, row, line_id, actor, now)
        doc["sealed"] = True
        doc["writer_attempt"] = str(attempt["attempt_id"])
        doc["import_epoch"] = epoch
        try:
            result = await db[LINES].update_one(_seal_filter(line_id, attempt["attempt_id"], epoch), {"$set": doc})
        except DuplicateKeyError as exc:
            fields = _dup_fields(exc)
            if "authoritative_txn_id" in fields:
                await _assert_import_attempt(db, attempt)
                deleted = await db[LINES].delete_one({
                    "id": line_id,
                    "import_id": batch["id"],
                    "writer_attempt": str(attempt["attempt_id"]),
                    "sealed": False,
                })
                if deleted.deleted_count != 1:
                    raise StatementConflictError(
                        "Baris impor bentrok dengan data yang sudah tersimpan"
                    ) from exc
                _mark_conflict(row, "id_otoritatif_sudah_tersimpan")
                continue
            raise StatementConflictError(
                "Baris impor bentrok dengan data yang sudah tersimpan"
            ) from exc
        else:
            if result.matched_count != 1:
                current = await db[LINES].find_one(
                    {"id": line_id}, {"_id": 0, "identity": 1, "sealed": 1},
                )
                if current and current.get("sealed") is not False and current.get("identity") == doc["identity"]:
                    stored.append(row)
                elif current and current.get("sealed") is not False:
                    raise StatementConflictError(
                        "Baris impor bentrok dengan data yang sudah tersimpan"
                    )
                else:
                    raise StatementConflictError(
                        "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
                    )
            else:
                stored.append(row)
        await _after_statement_line_insert(index)
    return stored


def _line_doc(batch: dict, row: dict, line_id: str, actor: dict, now: str) -> dict:
    doc = {
        "id": line_id,
        "import_id": batch["id"],
        "cash_bank_id": batch["cash_bank_id"],
        "source_row": row["source_row"],
        "row_number": row["row_number"],
        "date": row["date"],
        "description": row["description"],
        "reference": row.get("reference"),
        "bank_txn_id": row.get("bank_txn_id"),
        "direction": row["direction"],
        "amount": row["amount"],
        "running_balance": row.get("running_balance"),
        "currency_code": row.get("currency_code"),
        "account_currency_code": batch.get("account_currency_code"),
        "identity_guarantee": row.get("identity_guarantee") or "tidak_dijamin",
        "duplicate_status": row.get("duplicate_status") or DUP_CLEAR,
        "duplicate_reason": row.get("duplicate_reason"),
        "duplicate_of_line_id": row.get("duplicate_of_line_id"),
        "duplicate_of_bank_txn_id": row.get("duplicate_of_bank_txn_id"),
        "identity": _line_identity(row),
        "resolutions": [],
        "created_at": now,
        "created_by": actor,
    }
    if row.get("authoritative_txn_id"):
        doc["authoritative_txn_id"] = row["authoritative_txn_id"]
    if row.get("original_date") == "PEND" and row.get("date_status") in ("temporary", "confirmed"):
        doc["original_date"] = "PEND"
        doc["date_status"] = row["date_status"]
        if row.get("date_source"):
            doc["date_source"] = row["date_source"]
        if row.get("date_confirmation"):
            doc["date_confirmation"] = row["date_confirmation"]
    return doc


async def _finish_if_complete(db, batch: dict, attempt: dict, inserted_rows: Sequence[dict]) -> dict:
    count, total_in, total_out = await _stored_totals(db, batch["id"])
    expected_in, expected_out = _direction_totals(inserted_rows)
    same = count == len(inserted_rows) and total_in == expected_in and total_out == expected_out
    status = STATUS_COMPLETE if same else STATUS_FAILED
    if batch.get("outcome_ready"):
        reused_count = int(batch.get("reused_count") or 0)
    else:
        reused_count = (
            max(int(batch.get("accepted_count") or 0) - count, 0)
            if same else int(batch.get("reused_count") or 0)
        )
    write: Dict[str, Any] = {"$set": {
        "status": status,
        "stored_count": count,
        "reused_count": reused_count,
        "completed_at": now_iso() if same else None,
    }}
    if batch.get("outcome_ready"):
        for key in (
            "new_count", "updated_count", "protected_count", "unchanged_count",
            "conflict_count", "conflicts", "fully_accepted", "result_note",
        ):
            write["$set"][key] = batch.get(key)
    if same:
        write["$unset"] = {"file_bytes": ""}
    await _assert_import_attempt(db, attempt)
    await _before_fenced_write("finish")
    result = await db[IMPORTS].update_one(
        {
            "id": batch["id"],
            "status": STATUS_IMPORTING,
            "writer_attempt": str(attempt["attempt_id"]),
        },
        write,
    )
    if result.matched_count != 1:
        raise StatementConflictError(
            "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
        )
    batch["status"] = status
    batch["stored_count"] = count
    batch["reused_count"] = reused_count
    return batch


async def _import_fence_reusable(db, current: dict) -> bool:
    batch_id = current.get("batch_id")
    if not batch_id:
        return False
    batch = await db[IMPORTS].find_one({"id": batch_id}, {"_id": 0, "status": 1})
    if not batch:
        return True
    return batch.get("status") in (STATUS_COMPLETE, STATUS_FAILED)


def _lease_expired(value: Optional[str], now: Optional[str] = None) -> bool:
    if not value:
        return True
    return str(value) <= (now or now_iso())


def _same_operation_live(current: dict, operation_id: str, now: str) -> bool:
    if str(current.get("operation_id") or "") != str(operation_id):
        return False
    return not _lease_expired(current.get("lease_until"), now)


async def _pull_dead_reader(db, cash_bank_id: str, attempt_id: Optional[str], operation_id: Optional[str] = None) -> None:
    """Drop the reader of an attempt whose fence was taken or cleared. Not an age sweep."""
    cash_bank_id = str(cash_bank_id)
    if attempt_id:
        await db[CASH_BANK_ACCOUNTS].update_one(
            {"id": cash_bank_id},
            {"$pull": {IMPORT_READERS: {"attempt_id": str(attempt_id)}}},
        )
        await db[CASH_BANK_ACCOUNTS].update_one(
            {"id": cash_bank_id},
            {"$pull": {IMPORT_READERS: str(attempt_id)}},
        )
    if operation_id and str(operation_id) != str(attempt_id or ""):
        await db[CASH_BANK_ACCOUNTS].update_one(
            {"id": cash_bank_id},
            {"$pull": {IMPORT_READERS: str(operation_id)}},
        )


async def _refresh_own_fence(db, cash_bank_id: str, attempt_id: str) -> bool:
    """Extend the lease only while this attempt is still the holder."""
    now = now_iso()
    refreshed = await db[IMPORT_FENCES].update_one(
        {"cash_bank_id": str(cash_bank_id), "holder": str(attempt_id)},
        {"$set": {"lease_until": coordination_deadline(), "at": now}},
    )
    return refreshed.matched_count == 1


async def _takeover_expired_fence(
    db, current: dict, attempt_id: str, operation_id: str, file_hash: str,
) -> bool:
    """Lease expiry only makes this compare-and-swap eligible. Age does not delete the fence."""
    now = now_iso()
    if not _lease_expired(current.get("lease_until"), now):
        return False
    keep_batch = None
    batch_id = current.get("batch_id")
    if batch_id:
        batch = await db[IMPORTS].find_one(
            {"id": batch_id}, {"_id": 0, "status": 1, "file_sha256": 1},
        )
        if (
            batch
            and batch.get("status") == STATUS_IMPORTING
            and batch.get("file_sha256") == file_hash
        ):
            keep_batch = batch_id
    await _bump_import_epoch(db, current.get("cash_bank_id"))
    replaced = await db[IMPORT_FENCES].update_one(
        {
            "cash_bank_id": current.get("cash_bank_id"),
            "holder": current.get("holder"),
            "$or": [
                {"lease_until": {"$lte": now}},
                {"lease_until": {"$exists": False}},
            ],
        },
        {"$set": {
            "holder": str(attempt_id),
            "operation_id": str(operation_id),
            "batch_id": keep_batch,
            "file_hash": file_hash,
            "lease_until": coordination_deadline(),
            "at": now,
        }},
    )
    if replaced.matched_count != 1:
        return False
    await _pull_dead_reader(
        db, current.get("cash_bank_id"), current.get("holder"), current.get("operation_id"),
    )
    await _reassign_stale_import_writers(
        db, current.get("cash_bank_id"), attempt_id, keep_batch,
    )
    return True


async def _replace_finished_fence(
    db, current: dict, attempt_id: str, operation_id: str, file_hash: str,
) -> bool:
    if not await _import_fence_reusable(db, current):
        return False
    await _bump_import_epoch(db, current.get("cash_bank_id"))
    now = now_iso()
    replaced = await db[IMPORT_FENCES].update_one(
        {"cash_bank_id": current.get("cash_bank_id"), "holder": current.get("holder")},
        {"$set": {
            "holder": str(attempt_id),
            "operation_id": str(operation_id),
            "batch_id": None,
            "file_hash": file_hash,
            "lease_until": coordination_deadline(),
            "at": now,
        }},
    )
    if replaced.matched_count != 1:
        return False
    await _pull_dead_reader(
        db, current.get("cash_bank_id"), current.get("holder"), current.get("operation_id"),
    )
    await _reassign_stale_import_writers(db, current.get("cash_bank_id"), attempt_id, None)
    return True


async def _acquire_import_fence(
    db, cash_bank_id: str, attempt_id: str, *, operation_id: str, file_hash: str,
    wait: bool = True, timeout: float = 20.0,
) -> None:
    """Per-account fence. The holder is one commit attempt, not the preview id."""
    deadline = time.monotonic() + timeout
    cash_bank_id = str(cash_bank_id)
    while True:
        current = await db[IMPORT_FENCES].find_one({"cash_bank_id": cash_bank_id})
        if current is None:
            try:
                await db[IMPORT_FENCES].insert_one({
                    "cash_bank_id": cash_bank_id,
                    "holder": str(attempt_id),
                    "operation_id": str(operation_id),
                    "batch_id": None,
                    "file_hash": file_hash,
                    "lease_until": coordination_deadline(),
                    "at": now_iso(),
                })
                return
            except DuplicateKeyError:
                continue
        if current.get("holder") == str(attempt_id):
            if await _refresh_own_fence(db, cash_bank_id, attempt_id):
                return
            continue
        if await _takeover_expired_fence(db, current, attempt_id, operation_id, file_hash):
            return
        if await _replace_finished_fence(db, current, attempt_id, operation_id, file_hash):
            return
        now = now_iso()
        if _same_operation_live(current, operation_id, now):
            raise StatementConflictError(
                "Impor pratinjau ini sedang berjalan pada permintaan lain. "
                "Ulangi setelah permintaan itu selesai. Permintaan ini tidak mengambil alih pagar."
            )
        await _on_import_fence_busy(cash_bank_id)
        if (not wait) or time.monotonic() >= deadline:
            raise StatementConflictError(
                "Impor lain sedang berjalan pada rekening ini. "
                "Ulangi setelah impor itu selesai atau gagal."
            )
        await asyncio.sleep(0.01)


async def _release_import_fence(db, cash_bank_id: str, attempt_id: str) -> None:
    """Drop only this attempt's fence. An importing batch it still owns becomes claimable."""
    released = await db[IMPORT_FENCES].delete_one({
        "cash_bank_id": str(cash_bank_id),
        "holder": str(attempt_id),
    })
    if released.deleted_count != 1:
        return
    await db[IMPORTS].update_many(
        {
            "cash_bank_id": str(cash_bank_id),
            "status": STATUS_IMPORTING,
            "writer_attempt": str(attempt_id),
        },
        {"$set": {"writer_attempt": DISPLACED_WRITER}},
    )
    await db[LINES].update_many(
        {
            "cash_bank_id": str(cash_bank_id),
            "sealed": False,
            "writer_attempt": str(attempt_id),
        },
        {"$set": {"writer_attempt": DISPLACED_WRITER}},
    )


async def _bind_import_fence(db, cash_bank_id: str, attempt_id: str, batch_id: str) -> None:
    bound = await db[IMPORT_FENCES].update_one(
        {"cash_bank_id": str(cash_bank_id), "holder": str(attempt_id)},
        {"$set": {
            "batch_id": batch_id,
            "lease_until": coordination_deadline(),
            "at": now_iso(),
        }},
    )
    if bound.matched_count != 1:
        raise StatementConflictError("Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan.")


def _reader_doc(attempt_id: str, operation_id: str) -> dict:
    return {
        "attempt_id": str(attempt_id),
        "operation_id": str(operation_id),
        "until": coordination_deadline(),
    }


async def _pin_import_currency(
    db, cash_bank_id: str, attempt_id: str, operation_id: str, expected: Optional[str],
) -> None:
    """Pin this attempt. Another attempt of the same preview is a different reader."""
    now = now_iso()
    result = await db[CASH_BANK_ACCOUNTS].update_one(
        {
            "id": str(cash_bank_id),
            "currency_code": expected,
            **{"$and": [_writer_takeover_filter(now)]},
        },
        {
            "$addToSet": {IMPORT_READERS: _reader_doc(attempt_id, operation_id)},
            "$unset": {CURRENCY_WRITER: "", CURRENCY_WRITER_UNTIL: ""},
        },
    )
    if result.matched_count == 1:
        return
    account = await get_cash_bank_raw(db, cash_bank_id)
    if _attempt_reader_current(account, attempt_id, expected):
        return
    raise StatementConflictError(
        "Mata uang rekening berubah setelah pratinjau. Validasi ulang setelah pratinjau baru. "
        "Commit tidak dilanjutkan."
    )


def _attempt_reader_current(account: Optional[dict], attempt_id: str, expected: Optional[str]) -> bool:
    if not account or (account.get("currency_code") or None) != expected:
        return False
    until = account.get(CURRENCY_WRITER_UNTIL)
    writer = account.get(CURRENCY_WRITER)
    if writer and until and str(until) > now_iso():
        return False
    if writer and not until:
        return False
    return any(
        isinstance(item, dict) and item.get("attempt_id") == str(attempt_id)
        for item in (account.get(IMPORT_READERS) or [])
    )


async def _unpin_import_currency(db, cash_bank_id: str, attempt_id: str) -> None:
    await db[CASH_BANK_ACCOUNTS].update_one(
        {"id": str(cash_bank_id)},
        {"$pull": {IMPORT_READERS: {"attempt_id": str(attempt_id)}}},
    )


async def _refresh_import_reader(db, attempt: dict) -> bool:
    now = now_iso()
    refreshed = await db[CASH_BANK_ACCOUNTS].update_one(
        {
            "id": str(attempt["cash_bank_id"]),
            "currency_code": attempt.get("expected_currency"),
            f"{IMPORT_READERS}.attempt_id": str(attempt["attempt_id"]),
            "$and": [_writer_takeover_filter(now)],
        },
        {
            "$set": {f"{IMPORT_READERS}.$[r].until": coordination_deadline()},
            "$unset": {CURRENCY_WRITER: "", CURRENCY_WRITER_UNTIL: ""},
        },
        array_filters=[{"r.attempt_id": str(attempt["attempt_id"])}],
    )
    return refreshed.matched_count == 1


async def _assert_import_attempt(db, attempt: dict) -> None:
    """Fencing check at each write. A lost holder cannot finish a conflicting import."""
    if attempt.get("fenced"):
        if not await _refresh_own_fence(db, attempt["cash_bank_id"], attempt["attempt_id"]):
            raise StatementConflictError(
                "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
            )
    if not await _refresh_import_reader(db, attempt):
        raise StatementConflictError(
            "Mata uang rekening berubah atau pembaca impor tidak lagi dimiliki permintaan ini."
        )


async def commit_import(
    db,
    *,
    cash_bank_id: str,
    filename: str,
    data: bytes,
    fingerprint: str,
    mapping_raw: dict,
    user: Optional[dict],
    account_binding_value: Optional[str] = None,
    client_rows: Any = None,
    date_confirmations: Optional[list] = None,
) -> dict:
    """Re-read the uploaded file. Client-parsed rows are ignored."""
    del client_rows
    from services.cash_bank_reconciliation import ClaimFeatureNotReady, require_claim_feature
    try:
        await require_claim_feature(db)
    except ClaimFeatureNotReady as exc:
        raise StatementConflictError(str(exc)) from exc
    if not data:
        raise StatementValidationError("Berkas tidak dikirim ulang. Unggah berkas asli kembali.")
    if len(data) > MAX_BYTES:
        raise StatementValidationError("Berkas melebihi batas 2 MB")
    actual = _sha256(data)
    supplied = str(fingerprint or "").strip().lower()
    if not supplied or supplied != actual:
        raise StatementConflictError(
            "Sidik jari berkas tidak sesuai. Impor ditolak dan berkas dihitung ulang di server."
        )
    cb_id = str(cash_bank_id or "").strip()
    account = await get_cash_bank_raw(db, cb_id)
    if not account:
        raise StatementValidationError("Kas/Bank tidak ditemukan")
    expected_currency = account.get("currency_code") or None
    _require_same_account(cb_id, expected_currency, actual, account_binding_value)
    stored = await db[IMPORTS].find_one({"cash_bank_id": cb_id, "file_sha256": actual}, {"_id": 0})
    if stored and stored.get("file_bytes") and _sha256(bytes(stored.get("file_bytes") or b"")) != actual:
        raise StatementConflictError("Hash berkas impor tersimpan tidak sesuai")
    source = {
        "id": actual,
        "cash_bank_id": cb_id,
        "filename": str(filename or "mutasi"),
        "file_bytes": data,
        "file_sha256": actual,
        "account_currency_code": expected_currency,
        "currency_context_note": _currency_note(),
    }
    await _before_currency_pin()
    attempt_id = str(uuid.uuid4())
    pinned = False
    try:
        await _pin_import_currency(db, account["id"], attempt_id, actual, expected_currency)
        pinned = True
        await _after_currency_pin()
        account = await get_cash_bank_raw(db, account["id"]) or account
        return await _commit_import_pinned(
            db, preview=source, account=account, mapping_raw=mapping_raw, user=user,
            attempt_id=attempt_id, date_confirmations=date_confirmations,
        )
    finally:
        if pinned:
            await _unpin_import_currency(db, account["id"], attempt_id)


async def _commit_import_pinned(
    db, *, preview: dict, account: dict, mapping_raw: dict, user: Optional[dict], attempt_id: str,
    date_confirmations: Optional[list] = None,
) -> dict:
    data = bytes(preview.get("file_bytes") or b"")
    file_hash = _sha256(data)
    if file_hash != preview.get("file_sha256"):
        raise StatementConflictError("Hash berkas pratinjau tidak sesuai")
    if _bca_layout(mapping_raw):
        from services.cash_bank_statement_bca import statement_rows_from_bca
        mapping = {"layout": "bca"}
        parsed = statement_rows_from_bca(
            preview.get("filename") or "",
            data,
            preview.get("account_currency_code") or None,
            confirmations=date_confirmations,
            actor=actor_snapshot(user),
            confirmed_at=now_iso(),
        )
    else:
        mapping = normalize_mapping(mapping_raw)
        matrix = read_matrix(preview.get("filename") or "", data)
        parsed = interpret_rows(matrix, mapping)
    if parsed["errors"]:
        raise StatementValidationError(
            "Impor ditolak karena ada baris tidak valid",
            errors=parsed["errors"],
        )
    if not parsed["accepted"]:
        raise StatementValidationError("Tidak ada baris yang dapat diimpor")
    mapping_hash = _mapping_hash(mapping)
    cash_bank_id = preview["cash_bank_id"]
    authoritative = bool(mapping.get("bank_txn_id_authoritative"))
    attempt = {
        "attempt_id": str(attempt_id),
        "operation_id": str(preview["id"]),
        "cash_bank_id": cash_bank_id,
        "expected_currency": preview.get("account_currency_code") or None,
        "fenced": not authoritative,
        "source_bytes": data,
    }
    if attempt["fenced"]:
        await _acquire_import_fence(
            db, cash_bank_id, attempt_id, operation_id=attempt["operation_id"], file_hash=file_hash,
        )
    try:
        return await _store_pinned_import(
            db, preview=preview, account=account, mapping=mapping, parsed=parsed,
            user=user, file_hash=file_hash, mapping_hash=mapping_hash,
            cash_bank_id=cash_bank_id, authoritative=authoritative, attempt=attempt,
        )
    finally:
        if attempt["fenced"]:
            await _release_import_fence(db, cash_bank_id, attempt_id)


async def _claim_batch_writer(db, batch_id: str, attempt_id: str) -> None:
    claimed = await db[IMPORTS].update_one(
        {
            "id": batch_id,
            "status": STATUS_IMPORTING,
            "$or": [
                {"writer_attempt": str(attempt_id)},
                {"writer_attempt": DISPLACED_WRITER},
                {"writer_attempt": {"$exists": False}},
            ],
        },
        {"$set": {"writer_attempt": str(attempt_id)}},
    )
    if claimed.matched_count != 1:
        raise StatementConflictError(
            "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
        )
    await db[LINES].update_many(
        {
            "import_id": batch_id,
            "sealed": False,
            "writer_attempt": DISPLACED_WRITER,
        },
        {"$set": {"writer_attempt": str(attempt_id)}},
    )


async def _continue_importing_batch(db, existing: dict, parsed: dict, actor: dict, attempt: dict, cash_bank_id: str) -> dict:
    await _assert_import_attempt(db, attempt)
    await _claim_batch_writer(db, existing["id"], attempt["attempt_id"])
    if attempt.get("fenced"):
        await _bind_import_fence(db, cash_bank_id, attempt["attempt_id"], existing["id"])
    if attempt.get("source_bytes") and not existing.get("file_bytes"):
        await db[IMPORTS].update_one(
            {"id": existing["id"], "status": STATUS_IMPORTING, "file_bytes": {"$exists": False}},
            {"$set": {"file_bytes": Binary(attempt["source_bytes"])}},
        )
    await _apply_reimport_updates(db, existing, parsed["accepted"], actor, attempt)
    to_insert = _rows_to_insert(parsed["accepted"])
    stored_rows = await _insert_prepared_lines(db, existing, to_insert, actor, attempt) if to_insert else []
    await _assert_import_attempt(db, attempt)
    _stamp_outcome(existing, parsed["accepted"])
    finished = await _finish_if_complete(db, existing, attempt, stored_rows)
    if finished.get("status") != STATUS_COMPLETE:
        raise StatementConflictError("Impor belum lengkap dan total tersimpan tidak sesuai")
    return public_import(finished)


async def _store_pinned_import(
    db, *, preview, account, mapping, parsed, user, file_hash, mapping_hash,
    cash_bank_id, authoritative, attempt,
) -> dict:
    await _assert_import_attempt(db, attempt)
    await _after_import_fence_acquired()
    await _assert_import_attempt(db, attempt)
    existing_for_flag = await db[IMPORTS].find_one(
        {"cash_bank_id": cash_bank_id, "file_sha256": file_hash},
        {"_id": 0, "id": 1, "mapping_hash": 1},
    )
    ignore_import_id = None
    if existing_for_flag and existing_for_flag.get("mapping_hash") == mapping_hash:
        ignore_import_id = existing_for_flag.get("id")
    await _flag_duplicates(
        db,
        cash_bank_id,
        parsed["accepted"],
        authoritative=authoritative,
        ignore_import_id=ignore_import_id,
        attempt=attempt,
    )
    await _acknowledge_reused_rows(db, parsed["accepted"], attempt)
    await _assert_import_attempt(db, attempt)
    actor = actor_snapshot(user)
    existing = await db[IMPORTS].find_one(
        {"cash_bank_id": cash_bank_id, "file_sha256": file_hash},
    )
    if existing:
        existing.pop("_id", None)
        if existing.get("mapping_hash") != mapping_hash:
            raise StatementConflictError(
                "Berkas yang sama sudah diimpor dengan mapping berbeda. "
                "Batch lama tidak diubah."
            )
        if existing.get("status") == STATUS_COMPLETE:
            return _idempotent_file_result(existing, len(parsed["accepted"]))
        if existing.get("status") == STATUS_IMPORTING:
            return await _continue_importing_batch(db, existing, parsed, actor, attempt, cash_bank_id)
        raise StatementConflictError("Batch impor sebelumnya gagal dan tidak diubah diam-diam")

    await _assert_import_attempt(db, attempt)
    inserting = _rows_to_insert(parsed["accepted"])
    reused = [row for row in parsed["accepted"] if row.get("import_disposition") == "reuse"]
    batch = {
        "id": str(uuid.uuid4()),
        "cash_bank_id": cash_bank_id,
        "filename": preview.get("filename"),
        "file_sha256": file_hash,
        "file_bytes": Binary(attempt.get("source_bytes") or b""),
        "mapping_hash": mapping_hash,
        "mapping": mapping,
        "status": STATUS_IMPORTING,
        "accepted_count": parsed["accepted_count"],
        "stored_count": 0,
        "reused_count": len(reused),
        "reused_line_ids": [row.get("reused_line_id") for row in reused if row.get("reused_line_id")],
        "total_in": parsed["total_in"],
        "total_out": parsed["total_out"],
        "closing_balance": parsed["closing_balance"] if inserting else None,
        "closing_date": parsed["closing_date"] if inserting else None,
        "file_closing_balance": parsed["closing_balance"],
        "file_closing_date": parsed["closing_date"],
        "operator_coverage_confirmed": bool(mapping.get("operator_coverage_confirmed")),
        "account_currency_code": account.get("currency_code") or None,
        "currency_context_note": preview.get("currency_context_note"),
        "created_at": now_iso(),
        "created_by": actor,
        "completed_at": None,
        "writer_attempt": str(attempt["attempt_id"]),
    }
    try:
        await db[IMPORTS].insert_one(dict(batch))
    except DuplicateKeyError:
        existing = await db[IMPORTS].find_one(
            {"cash_bank_id": cash_bank_id, "file_sha256": file_hash},
        )
        if existing and existing.get("mapping_hash") == mapping_hash:
            existing.pop("_id", None)
            if existing.get("status") == STATUS_COMPLETE:
                return _idempotent_file_result(existing, len(parsed["accepted"]))
            if existing.get("status") == STATUS_IMPORTING:
                return await _continue_importing_batch(db, existing, parsed, actor, attempt, cash_bank_id)
        raise StatementConflictError(
            "Berkas yang sama sudah diimpor dengan mapping berbeda. Batch lama tidak diubah."
        )
    batch.pop("_id", None)
    if attempt.get("fenced"):
        await _bind_import_fence(db, cash_bank_id, attempt["attempt_id"], batch["id"])
    await _apply_reimport_updates(db, batch, parsed["accepted"], actor, attempt)
    inserting = _rows_to_insert(parsed["accepted"])
    stored_rows = await _insert_prepared_lines(db, batch, inserting, actor, attempt) if inserting else []
    await _assert_import_attempt(db, attempt)
    _stamp_outcome(batch, parsed["accepted"])
    finished = await _finish_if_complete(db, batch, attempt, stored_rows)
    if finished.get("status") != STATUS_COMPLETE:
        raise StatementConflictError("Impor belum lengkap dan total tersimpan tidak sesuai")
    return public_import(finished)


async def list_imports(db, *, cash_bank_id: str) -> List[dict]:
    docs = await db[IMPORTS].find(
        {"cash_bank_id": str(cash_bank_id)},
        {"_id": 0, "file_bytes": 0},
    ).sort("created_at", -1).to_list(200)
    return [public_import(d) for d in docs]


async def list_ready_lines(db, *, cash_bank_id: str, start: str, end: str) -> List[dict]:
    imports = await db[IMPORTS].find(
        {"cash_bank_id": str(cash_bank_id), "status": STATUS_COMPLETE},
        {"_id": 0, "id": 1},
    ).to_list(500)
    ids = [d["id"] for d in imports if d.get("id")]
    if not ids:
        return []
    rows = await db[LINES].find(
        {
            "import_id": {"$in": ids},
            "date": {"$gte": start, "$lte": end},
            "duplicate_status": {"$ne": DUP_EXCLUDED},
            "sealed": {"$ne": False},
        },
        {"_id": 0},
    ).to_list(MAX_ROWS * 5)
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("id") or "")))
    return rows


def _duplicate_period(start: str, end: str) -> Tuple[str, str]:
    begin = str(start or "").strip()
    finish = str(end or "").strip()
    if len(begin) != 10 or len(finish) != 10 or begin > finish:
        raise StatementValidationError("Periode tidak valid")
    return begin, finish


def _public_resolution(row: dict) -> Optional[dict]:
    events = [event for event in (row.get("resolutions") or []) if isinstance(event, dict)]
    if not events:
        return None
    latest = events[-1]
    actor = latest.get("actor") or {}
    return {
        "action": latest.get("action"),
        "reason": latest.get("reason"),
        "at": latest.get("at"),
        "actor": {"id": actor.get("id"), "name": actor.get("name")},
    }


def _duplicate_line_public(row: dict, batch: Optional[dict], match: Optional[dict]) -> dict:
    batch = batch or {}
    return {
        "statement_line_id": row.get("id"),
        "import_id": row.get("import_id"),
        "filename": batch.get("filename"),
        "imported_at": batch.get("created_at"),
        "date": row.get("date"),
        "business_date": _business_date(row),
        "amount": row.get("amount"),
        "direction": row.get("direction"),
        "description": row.get("description"),
        "duplicate_status": row.get("duplicate_status"),
        "resolution": _public_resolution(row),
        "active_claim": row.get("active_claim"),
        "reconciliation_status": "active" if match else "unmatched",
        "reconciliation_match_id": (match or {}).get("id"),
    }


async def _duplicate_context(db, rows: Sequence[dict]) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    import_ids = list({row.get("import_id") for row in rows if row.get("import_id")})
    batches = []
    if import_ids:
        batches = await db[IMPORTS].find(
            {"id": {"$in": import_ids}},
            {"_id": 0, "id": 1, "filename": 1, "created_at": 1},
        ).to_list(len(import_ids))
    by_import = {doc.get("id"): doc for doc in batches}
    line_ids = [row.get("id") for row in rows if row.get("id")]
    matches = []
    if line_ids:
        from services.cash_bank_reconciliation import RECON_MATCHES
        matches = await db[RECON_MATCHES].find(
            {"statement_line_id": {"$in": line_ids}, "status": "active"},
            {"_id": 0, "id": 1, "statement_line_id": 1, "status": 1},
        ).to_list(len(line_ids))
    match_by_line = {doc.get("statement_line_id"): doc for doc in matches if doc.get("statement_line_id")}
    return by_import, match_by_line


def _collapsed_text_expr(field: str) -> dict:
    """Match _normalized_description inside an aggregation. This does not load the rows."""
    expr: Dict[str, Any] = {
        "$toLower": {"$convert": {"input": f"${field}", "to": "string", "onError": "", "onNull": ""}},
    }
    for character in _WHITESPACE_CHARS:
        expr = {"$replaceAll": {"input": expr, "find": character, "replacement": " "}}
    expr = {
        "$reduce": {
            "input": {"$range": [0, 12]},
            "initialValue": expr,
            "in": {"$replaceAll": {"input": "$$value", "find": "  ", "replacement": " "}},
        }
    }
    return {"$trim": {"input": expr}}


def _amount_key_expr() -> dict:
    return {
        "$cond": [
            {"$in": [{"$type": "$amount"}, ["int", "long", "double", "decimal"]]},
            {"$toString": {"$round": [{"$toDecimal": "$amount"}, 2]}},
            {"$convert": {"input": "$amount", "to": "string", "onError": "", "onNull": ""}},
        ]
    }


def _business_date_expr() -> dict:
    return {
        "$cond": [
            {"$eq": ["$original_date", "PEND"]},
            "PEND",
            {"$convert": {"input": "$date", "to": "string", "onError": "", "onNull": ""}},
        ]
    }


def _duplicate_group_key_expr() -> dict:
    """Same tuple as _level2_key, joined so a page can request whole groups."""
    return {
        "$concat": [
            {"$convert": {"input": "$direction", "to": "string", "onError": "", "onNull": ""}},
            _GROUP_KEY_SEPARATOR,
            _amount_key_expr(),
            _GROUP_KEY_SEPARATOR,
            _collapsed_text_expr("description"),
            _GROUP_KEY_SEPARATOR,
            _business_date_expr(),
            _GROUP_KEY_SEPARATOR,
            _collapsed_text_expr("reference"),
        ]
    }


def _duplicate_group_sort_expr() -> dict:
    return {
        "$concat": [
            _business_date_expr(),
            _GROUP_KEY_SEPARATOR,
            {"$convert": {"input": "$direction", "to": "string", "onError": "", "onNull": ""}},
            _GROUP_KEY_SEPARATOR,
            _amount_key_expr(),
            _GROUP_KEY_SEPARATOR,
            _collapsed_text_expr("description"),
            _GROUP_KEY_SEPARATOR,
            _collapsed_text_expr("reference"),
        ]
    }


def _duplicate_group_key(row: dict) -> str:
    direction, amount, description, business_date, reference = _level2_key(row)
    return _GROUP_KEY_SEPARATOR.join((direction, amount, description, business_date, reference))


def _duplicate_screen_filter(account: str, begin: str, finish: str) -> dict:
    return {
        "cash_bank_id": account,
        "date": {"$gte": begin, "$lte": finish},
        "duplicate_status": {"$in": [DUP_PENDING, DUP_KEPT, DUP_EXCLUDED]},
        "sealed": {"$ne": False},
    }


def _page_bounds(page: int, page_size: int, default: int, maximum: int) -> Tuple[int, int, int]:
    current_page = max(1, int(page or 1))
    current_size = min(maximum, max(1, int(page_size or default)))
    return current_page, current_size, (current_page - 1) * current_size


def _review_changed(page: int, supplied: str, current: str) -> bool:
    return int(page or 1) > 1 and bool(str(supplied or "").strip()) and str(supplied).strip() != current


async def _collect(cursor) -> List[dict]:
    rows: List[dict] = []
    async for doc in cursor:
        rows.append(doc)
    return rows


async def _account_pending_total(db, account: str) -> int:
    return int(await db[LINES].count_documents({
        "cash_bank_id": account,
        "duplicate_status": DUP_PENDING,
    }))


async def _duplicate_review_revision(db, account: str, pending_total: int) -> str:
    """A read of counts and the latest stamp. Opening the next page does not change it."""
    rows = await _collect(db[LINES].aggregate([
        {"$match": {
            "cash_bank_id": account,
            "duplicate_status": {"$in": [DUP_PENDING, DUP_KEPT, DUP_EXCLUDED]},
        }},
        {"$group": {
            "_id": None,
            "n": {"$sum": 1},
            "stamp": {"$max": {"$ifNull": ["$updated_at", {"$ifNull": ["$created_at", ""]}]}},
        }},
    ], allowDiskUse=True))
    count = int(rows[0].get("n") or 0) if rows else 0
    stamp = rows[0].get("stamp") if rows else ""
    return f"{int(pending_total)}:{count}:{stamp or ''}"


async def _duplicate_group_index(db, account: str, begin: str, finish: str) -> List[dict]:
    """One summary per duplicate group. Line documents stay in the database."""
    return await _collect(db[LINES].aggregate([
        {"$match": _duplicate_screen_filter(account, begin, finish)},
        {"$addFields": {
            "group_key": _duplicate_group_key_expr(),
            "sort_key": _duplicate_group_sort_expr(),
        }},
        {"$group": {
            "_id": "$group_key",
            "sort_key": {"$first": "$sort_key"},
            "member_count": {"$sum": 1},
            "pending_count": {"$sum": {"$cond": [{"$eq": ["$duplicate_status", DUP_PENDING]}, 1, 0]}},
        }},
        {"$match": {"member_count": {"$gte": 2}, "pending_count": {"$gte": 1}}},
        {"$sort": {"sort_key": 1, "_id": 1}},
    ], allowDiskUse=True))


def _group_membership_expr(begin: str, finish: str, keys: Sequence[str]) -> dict:
    return {"$and": [
        {"$ne": ["$sealed", False]},
        {"$gte": [{"$ifNull": ["$date", ""]}, begin]},
        {"$lte": [{"$ifNull": ["$date", ""]}, finish]},
        {"$in": [_duplicate_group_key_expr(), list(keys)]},
    ]}


def _row_is_group_member(row: dict, begin: str, finish: str, keys: set) -> bool:
    if row.get("sealed") is False or not keys:
        return False
    day = str(row.get("date") or "")
    if day < begin or day > finish:
        return False
    return _duplicate_group_key(row) in keys


def _public_duplicate_group(account: str, members: Sequence[dict], by_import: dict, match_by_line: dict) -> dict:
    ordered = sorted(members, key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")))
    direction, _amount, description, business_date, reference = _level2_key(ordered[0])
    return {
        "cash_bank_id": account,
        "group_key": _duplicate_group_key(ordered[0]),
        "business_date": business_date,
        "direction": direction,
        "amount": ordered[0].get("amount"),
        "normalized_description": description,
        "normalized_reference": reference,
        "description": ordered[0].get("description"),
        "candidate_count": len(ordered),
        "lines": [
            _duplicate_line_public(row, by_import.get(row.get("import_id")), match_by_line.get(row.get("id")))
            for row in ordered
        ],
    }


def _stale_review(account: str, begin: str, finish: str, *, page: int, page_size: int, pending_total: int, revision: str, groups: bool) -> dict:
    body = {
        "cash_bank_id": account,
        "start_date": begin,
        "end_date": finish,
        "page": page,
        "page_size": page_size,
        "pending_total": pending_total,
        "review_revision": revision,
        "stale": True,
        "truncated": False,
        "message": STALE_REVIEW_MESSAGE,
    }
    if groups:
        body.update({"group_total": 0, "groups": []})
    else:
        body.update({
            "total": 0,
            "solo_lines": [],
            "outside_period": [],
            "unreachable": [],
            "other_accounts": [],
        })
    return body


async def list_duplicate_groups(
    db, *, cash_bank_id: str, start: str, end: str,
    page: int = 1, page_size: int = DUPLICATE_GROUP_PAGE_DEFAULT, review_revision: str = "",
) -> dict:
    """Read duplicate clusters that still need a decision. This function does not write.

    Each page contains whole groups. A group is never split across pages, and the
    grouping key is computed for every sealed line in the period rather than for
    the first 10,000 documents.
    """
    account = str(cash_bank_id or "").strip()
    if not account:
        raise StatementValidationError("Rekening wajib")
    begin, finish = _duplicate_period(start, end)
    current_page, current_size, skip = _page_bounds(
        page, page_size, DUPLICATE_GROUP_PAGE_DEFAULT, DUPLICATE_GROUP_PAGE_MAX,
    )
    pending_total = await _account_pending_total(db, account)
    revision = await _duplicate_review_revision(db, account, pending_total)
    if _review_changed(current_page, review_revision, revision):
        return _stale_review(
            account, begin, finish, page=current_page, page_size=current_size,
            pending_total=pending_total, revision=revision, groups=True,
        )
    index = await _duplicate_group_index(db, account, begin, finish)
    page_rows = index[skip:skip + current_size]
    expected = {row.get("_id"): int(row.get("member_count") or 0) for row in page_rows if row.get("_id")}
    members: List[dict] = []
    if expected:
        members = await _collect(db[LINES].aggregate([
            {"$match": _duplicate_screen_filter(account, begin, finish)},
            {"$addFields": {"group_key": _duplicate_group_key_expr()}},
            {"$match": {"group_key": {"$in": list(expected)}}},
            {"$project": {"_id": 0}},
        ], allowDiskUse=True))
    buckets: Dict[str, List[dict]] = {}
    for row in members:
        buckets.setdefault(str(row.get("group_key") or ""), []).append(row)
    if expected and (
        set(buckets) != set(expected)
        or any(len(buckets.get(key) or []) != count for key, count in expected.items())
    ):
        pending_total = await _account_pending_total(db, account)
        revision = await _duplicate_review_revision(db, account, pending_total)
        return _stale_review(
            account, begin, finish, page=current_page, page_size=current_size,
            pending_total=pending_total, revision=revision, groups=True,
        )
    by_import, match_by_line = await _duplicate_context(db, members)
    groups = [
        _public_duplicate_group(account, buckets[key], by_import, match_by_line)
        for key in expected
        if buckets.get(key)
    ]
    return {
        "cash_bank_id": account,
        "start_date": begin,
        "end_date": finish,
        "page": current_page,
        "page_size": current_size,
        "group_total": len(index),
        "pending_total": pending_total,
        "truncated": skip + len(groups) < len(index),
        "review_revision": revision,
        "stale": False,
        "groups": groups,
    }


def _valid_statement_day(value: Any) -> str:
    text = str(value or "").strip()
    return text if len(text) == 10 and text[4] == "-" and text[7] == "-" else ""


async def list_pending_review_access(
    db, *, cash_bank_id: str, start: str, end: str, page: int = 1, page_size: int = PENDING_REVIEW_PAGE_DEFAULT,
    review_revision: str = "",
) -> dict:
    """Read pending_review rows the group screen does not list. This function does not write.

    A row is a single line only when the same identity used for groups has no second
    candidate. pending_total counts every pending_review row on this account.
    """
    account = str(cash_bank_id or "").strip()
    if not account:
        raise StatementValidationError("Rekening wajib")
    begin, finish = _duplicate_period(start, end)
    current_page, current_size, skip = _page_bounds(
        page, page_size, PENDING_REVIEW_PAGE_DEFAULT, PENDING_REVIEW_PAGE_MAX,
    )
    pending_total = await _account_pending_total(db, account)
    revision = await _duplicate_review_revision(db, account, pending_total)
    if _review_changed(current_page, review_revision, revision):
        return _stale_review(
            account, begin, finish, page=current_page, page_size=current_size,
            pending_total=pending_total, revision=revision, groups=False,
        )
    index = await _duplicate_group_index(db, account, begin, finish)
    group_keys = [row.get("_id") for row in index if row.get("_id")]
    key_set = set(group_keys)
    hidden_match: Dict[str, Any] = {"cash_bank_id": account, "duplicate_status": DUP_PENDING}
    if group_keys:
        hidden_match["$expr"] = {"$not": [_group_membership_expr(begin, finish, group_keys)]}
    total = int((await _collect(db[LINES].aggregate([
        {"$match": hidden_match},
        {"$count": "n"},
    ], allowDiskUse=True)) or [{"n": 0}])[0].get("n") or 0)
    rows = await _collect(db[LINES].aggregate([
        {"$match": hidden_match},
        {"$sort": {"date": 1, "id": 1}},
        {"$skip": skip},
        {"$limit": current_size},
        {"$project": {"_id": 0}},
    ], allowDiskUse=True))
    truncated = skip + len(rows) < total
    by_import, match_by_line = await _duplicate_context(db, rows)
    solo_lines = []
    outside_period = []
    unreachable = []
    for row in rows:
        if _row_is_group_member(row, begin, finish, key_set):
            continue
        public = _duplicate_line_public(row, by_import.get(row.get("import_id")), match_by_line.get(row.get("id")))
        day = _valid_statement_day(row.get("date"))
        if row.get("sealed") is False:
            public["screen_reason"] = "Baris belum tersegel, sehingga kelompok periode tidak menampilkannya."
            unreachable.append(public)
            continue
        if not day:
            public["screen_reason"] = "Tanggal baris tidak ada, sehingga kelompok periode tidak menampilkannya."
            unreachable.append(public)
            continue
        if day < begin or day > finish:
            public["screen_reason"] = "Di luar periode yang sedang dibuka."
            outside_period.append(public)
            continue
        public["screen_reason"] = "Baris tunggal. Kelompok duplikat hanya menampilkan dua kandidat atau lebih."
        solo_lines.append(public)
    sort_key = lambda item: (str(item.get("date") or ""), str(item.get("statement_line_id") or ""))
    solo_lines.sort(key=sort_key)
    outside_period.sort(key=sort_key)
    unreachable.sort(key=sort_key)
    counts = await db[LINES].aggregate([
        {"$match": {"duplicate_status": DUP_PENDING, "cash_bank_id": {"$ne": account}}},
        {"$group": {"_id": "$cash_bank_id", "pending_count": {"$sum": 1}}},
    ]).to_list(500)
    account_ids = [doc.get("_id") for doc in counts if doc.get("_id")]
    names = {}
    if account_ids:
        accounts = await db[CASH_BANK_ACCOUNTS].find(
            {"id": {"$in": account_ids}},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(len(account_ids))
        names = {doc.get("id"): doc.get("name") for doc in accounts}
    other_accounts = sorted(
        (
            {
                "cash_bank_id": doc.get("_id"),
                "name": names.get(doc.get("_id")) or "Rekening tidak diketahui",
                "pending_count": int(doc.get("pending_count") or 0),
            }
            for doc in counts
            if doc.get("_id") and int(doc.get("pending_count") or 0) > 0
        ),
        key=lambda item: (str(item.get("name") or ""), str(item.get("cash_bank_id") or "")),
    )
    return {
        "cash_bank_id": account,
        "start_date": begin,
        "end_date": finish,
        "page": current_page,
        "page_size": current_size,
        "total": total,
        "pending_total": pending_total,
        "truncated": truncated,
        "review_revision": revision,
        "stale": False,
        "solo_lines": solo_lines,
        "outside_period": outside_period,
        "unreachable": unreachable,
        "other_accounts": other_accounts,
    }


def line_matchable(row: dict, account_currency: Optional[str]) -> Tuple[bool, str]:
    if row.get("duplicate_status") == DUP_DEFINITE:
        return False, "Duplikat pasti transaksi bank yang sama tidak dapat dicocokkan"
    if row.get("duplicate_status") == DUP_PENDING:
        return False, "Dugaan duplikat belum diputuskan"
    if row.get("duplicate_status") == DUP_EXCLUDED:
        return False, "Baris dikecualikan"
    if row.get("duplicate_status") not in (DUP_CLEAR, DUP_KEPT):
        return False, "Status baris tidak siap"
    if row.get("active_claim"):
        return False, "Baris sedang diklaim pencocokan"
    currency = row.get("currency_code") or None
    if not account_currency:
        return False, "Mata uang rekening belum ditetapkan"
    if not currency:
        return False, "Mata uang baris bank tidak diketahui"
    if currency != account_currency:
        return False, "Mata uang baris bank berbeda dari rekening"
    return True, ""


async def resolve_duplicate(
    db, line_id: str, *, action: str, reason: str, idempotency_key: str, user: Optional[dict]
) -> dict:
    from services.cash_bank_reconciliation import ClaimFeatureNotReady, require_claim_feature
    try:
        await require_claim_feature(db)
    except ClaimFeatureNotReady as exc:
        raise StatementConflictError(str(exc)) from exc
    act = str(action or "").strip()
    if act not in ("keep", "exclude"):
        raise StatementValidationError("Keputusan duplikat harus keep atau exclude")
    why = str(reason or "").strip()
    if len(why) < 3:
        raise StatementValidationError("Alasan keputusan wajib diisi")
    key = str(idempotency_key or "").strip()
    if not key:
        raise StatementValidationError("Idempotency key wajib")
    line = await db[LINES].find_one({"id": str(line_id)}, {"_id": 0})
    if not line:
        raise KeyError("missing")
    prior = await db[LINES].find_one({"resolutions.idempotency_key": key}, {"_id": 0})
    if prior:
        if prior.get("id") != line.get("id"):
            raise StatementConflictError("Kunci keputusan sudah dipakai pada baris lain")
        same = next((r for r in prior.get("resolutions") or [] if r.get("idempotency_key") == key), None)
        if same and (same.get("action") != act or same.get("reason") != why):
            raise StatementConflictError("Kunci yang sama tidak dapat mengubah keputusan")
        return prior
    if line.get("duplicate_status") == DUP_DEFINITE and act == "keep":
        raise StatementConflictError(
            "Duplikat pasti tidak dapat dipertahankan sebagai transaksi aktif kedua."
        )
    if line.get("duplicate_status") not in (DUP_PENDING, DUP_KEPT, DUP_CLEAR, DUP_DEFINITE):
        raise StatementConflictError("Baris yang dikecualikan tidak diubah lewat kunci baru tanpa alur terpisah")
    if line.get("active_claim") and act == "exclude":
        raise StatementConflictError(
            "Baris sudah dicocokkan. Batalkan pencocokan melalui alur berizin sebelum mengecualikan."
        )
    from services.cash_bank_reconciliation import RECON_MATCHES
    claimed = await db[RECON_MATCHES].find_one(
        {"statement_line_id": line["id"], "status": "active"},
        {"_id": 0, "id": 1},
    )
    if claimed and act == "exclude":
        raise StatementConflictError(
            "Baris sudah dicocokkan. Batalkan pencocokan melalui alur berizin sebelum mengecualikan."
        )
    await _before_duplicate_resolution()
    event = {
        "action": act,
        "reason": why,
        "idempotency_key": key,
        "at": now_iso(),
        "actor": actor_snapshot(user),
        "duplicate_of_line_id": line.get("duplicate_of_line_id"),
        "identity_guarantee": line.get("identity_guarantee") or "tidak_dijamin",
    }
    new_status = DUP_KEPT if act == "keep" else DUP_EXCLUDED
    result = await db[LINES].update_one(
        {
            "id": line["id"],
            "duplicate_status": line.get("duplicate_status"),
            "active_claim": None,
        },
        {
            "$set": {"duplicate_status": new_status, "updated_at": event["at"]},
            "$push": {"resolutions": event},
        },
    )
    if result.matched_count != 1:
        current = await db[LINES].find_one({"id": line["id"]}, {"_id": 0})
        if current and current.get("active_claim"):
            raise StatementConflictError(
                "Baris sudah dicocokkan. Batalkan pencocokan melalui alur berizin sebelum mengecualikan."
            )
        raise StatementConflictError("Keputusan duplikat bentrok. Muat ulang baris.")
    return await db[LINES].find_one({"id": line["id"]}, {"_id": 0})


async def set_manual_balance(
    db, *, cash_bank_id: str, as_of: str, amount: Any, user: Optional[dict]
) -> dict:
    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise StatementValidationError("Kas/Bank tidak ditemukan")
    date = str(as_of or "").strip()
    if len(date) != 10:
        raise StatementValidationError("Tanggal saldo manual tidak valid")
    parsed = parse_amount(amount, "dot_decimal")
    if parsed is None:
        raise StatementValidationError("Nominal saldo manual wajib")
    doc = {
        "cash_bank_id": str(cash_bank_id),
        "as_of": date,
        "amount": _money_float(parsed),
        "source": "manual",
        "label": "Input manual",
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }
    await db[MANUAL_BALANCES].update_one(
        {"cash_bank_id": doc["cash_bank_id"], "as_of": date},
        {"$set": doc, "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": doc["updated_at"]}},
        upsert=True,
    )
    stored = await db[MANUAL_BALANCES].find_one(
        {"cash_bank_id": doc["cash_bank_id"], "as_of": date},
        {"_id": 0},
    )
    return {
        "cash_bank_id": stored.get("cash_bank_id"),
        "as_of": stored.get("as_of"),
        "amount": stored.get("amount"),
        "source": "manual",
        "label": "Input manual",
    }


async def statement_closing(db, *, cash_bank_id: str, end: str) -> dict:
    """File closing only when one complete import states a balance on end."""
    batches = await db[IMPORTS].find(
        {
            "cash_bank_id": str(cash_bank_id),
            "status": STATUS_COMPLETE,
            "closing_date": end,
            "closing_balance": {"$ne": None},
        },
        {"_id": 0, "id": 1, "closing_balance": 1, "closing_date": 1, "filename": 1},
    ).to_list(50)
    manual = await db[MANUAL_BALANCES].find_one(
        {"cash_bank_id": str(cash_bank_id), "as_of": end},
        {"_id": 0, "amount": 1, "as_of": 1, "source": 1},
    )
    manual_view = None
    if manual:
        manual_view = {
            "amount": manual.get("amount"),
            "as_of": manual.get("as_of"),
            "source": "manual",
            "label": "Input manual",
        }
    if len(batches) == 1:
        batch = batches[0]
        return {
            "status": "dari_kolom_saldo_impor",
            "amount": batch.get("closing_balance"),
            "as_of": end,
            "source": "kolom_saldo_impor",
            "import_id": batch.get("id"),
            "filename": batch.get("filename"),
            "label": "Dari kolom saldo impor",
            "manual": manual_view,
        }
    if len(batches) > 1:
        return {
            "status": "belum_tersedia",
            "amount": None,
            "as_of": end,
            "source": None,
            "label": "Belum tersedia",
            "reason": "Lebih dari satu saldo impor pada tanggal akhir",
            "manual": manual_view,
        }
    return {
        "status": "belum_tersedia",
        "amount": None,
        "as_of": end,
        "source": None,
        "label": "Belum tersedia",
        "manual": manual_view,
    }
