"""Preview parser for BCA CSV statements.

Reads a CSV through the existing statement reader and returns normalized rows.
This module does not write, import, pair, post, or change a balance.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence

from services.cash_bank_statement import (
    MAX_BYTES,
    MONEY,
    StatementValidationError,
    parse_date,
    read_matrix,
)

ROLES = ("tanggal", "keterangan", "debit", "kredit", "saldo")

# Exact labels after lowercase and whitespace folding. Extend by adding aliases.
HEADER_ALIASES = {
    "tanggal": ("tanggal transaksi", "tanggal", "tgl transaksi"),
    "keterangan": ("keterangan", "berita", "deskripsi"),
    "debit": ("debit", "debet"),
    "kredit": ("kredit", "credit"),
    "saldo": ("saldo", "saldo akhir"),
}

_EMPTY_AMOUNT = {"", "-", "—", "–"}
_SLASH_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_DASH_DATE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FRACTION = re.compile(r"^\d{1,2}$")
PEND_STATUS = "pending_confirmation"
CONFIRMED_STATUS = "confirmed"


def parse_bca_confirmation_form(raw: Optional[str]) -> list:
    """Decode the confirmation form. Row matching still happens against the CSV."""
    if raw is None or raw == "":
        return []
    try:
        import json
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StatementValidationError("Konfirmasi tanggal tidak dapat dibaca") from exc
    if not isinstance(parsed, list):
        raise StatementValidationError("Konfirmasi tanggal tidak dapat dibaca")
    return parsed


def statement_rows_from_bca(
    filename: str,
    data: bytes,
    currency: Optional[str],
    confirmations: Optional[list] = None,
    actor: Optional[dict] = None,
    confirmed_at: Optional[str] = None,
) -> dict:
    """Normalize a BCA CSV into the generic statement-row shape. Does not write.

    PEND rows stay out of the accepted list until this call checks the original
    cell again and applies a server-validated confirmation.
    """
    parsed = parse_bca_statement(filename, data)
    confirmed = _confirmation_index(confirmations)
    accepted: List[dict] = []
    pending: List[dict] = []
    errors = list(parsed["errors"])
    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    closing = None
    closing_date = None
    offset = 0
    for row in parsed["rows"]:
        if row.get("date_status") == PEND_STATUS:
            match = confirmed.pop(row["source_row"], None)
            if match is None:
                pending.append(row)
                errors.append({"row": row["source_row"], "message": "Baris PEND belum dikonfirmasi"})
                continue
            stored = dict(row)
            stored["tanggal"] = match["tanggal"]
            stored["date_status"] = CONFIRMED_STATUS
            stored["date_confirmation"] = {
                "reason": match["alasan"],
                "confirmed_by": actor,
                "confirmed_at": confirmed_at,
            }
            row = stored
        offset += 1
        amount = row["debit"] if row["direction"] == "out" else row["kredit"]
        if row.get("saldo") is not None:
            closing = row["saldo"]
            closing_date = row["tanggal"]
        if row["direction"] == "in":
            total_in += Decimal(str(amount))
        else:
            total_out += Decimal(str(amount))
        accepted.append(_statement_row(row, offset, amount, currency))
    if confirmed:
        raise StatementValidationError(
            "Konfirmasi tidak sesuai baris PEND",
            errors=[{"row": source_row, "message": "Konfirmasi tidak sesuai baris PEND"} for source_row in sorted(confirmed)],
        )
    return {
        "errors": errors,
        "pending": pending,
        "accepted": accepted,
        "accepted_count": len(accepted),
        "total_in": float(total_in.quantize(MONEY)),
        "total_out": float(total_out.quantize(MONEY)),
        "closing_balance": closing,
        "closing_date": closing_date,
        "headers": [],
    }


def _statement_row(row: dict, offset: int, amount: float, currency: Optional[str]) -> dict:
    doc = {
        "source_row": row["source_row"],
        "row_number": offset,
        "date": row["tanggal"],
        "description": row["keterangan"],
        "reference": None,
        "bank_txn_id": None,
        "direction": row["direction"],
        "amount": amount,
        "running_balance": row.get("saldo"),
        "currency_code": currency,
    }
    if row.get("date_status") == CONFIRMED_STATUS and row.get("original_date") == "PEND":
        doc["original_date"] = "PEND"
        doc["date_status"] = CONFIRMED_STATUS
        doc["date_confirmation"] = row.get("date_confirmation")
    return doc


def parse_bca_statement(filename: str, data: bytes) -> dict:
    """Parse one BCA CSV. The return value is a preview and is not stored."""
    if not str(filename or "").lower().endswith(".csv"):
        raise StatementValidationError("Format tidak didukung. Gunakan CSV BCA.")
    if not data:
        raise StatementValidationError("Berkas kosong")
    if len(data) > MAX_BYTES:
        raise StatementValidationError("Berkas melebihi batas 2 MB")
    try:
        matrix = read_matrix(filename, data)
    except StatementValidationError:
        raise
    except UnicodeDecodeError as exc:
        raise StatementValidationError("Berkas CSV tidak dapat dibaca") from exc
    if not matrix:
        raise StatementValidationError("Berkas tidak berisi baris")
    header_index, columns = _locate_header(matrix)
    rows: List[dict] = []
    errors: List[dict] = []
    for index, raw in enumerate(matrix[header_index + 1 :], start=header_index + 1):
        if _blank(raw):
            continue
        source_row = index + 1
        try:
            if any(isinstance(cell, str) and cell.strip().startswith("=") for cell in raw):
                raise StatementValidationError("Teks formula tidak diimpor")
            parsed = _parse_row(raw, columns)
        except StatementValidationError as exc:
            errors.append({"row": source_row, "message": str(exc)})
            continue
        parsed["source_row"] = source_row
        rows.append(parsed)
    return {
        "bank": "BCA",
        "header_detected": True,
        "header_row": header_index + 1,
        "rows": rows,
        "errors": errors,
        "persisted_statement_lines": False,
        "stored": False,
    }


def _locate_header(matrix: Sequence[Sequence[Any]]) -> tuple:
    best = None
    best_score = -1
    for index, row in enumerate(matrix):
        found = _match_header(row)
        if all(len(found[role]) == 1 for role in ROLES):
            return index, {role: found[role][0] for role in ROLES}
        score = sum(1 for role in ROLES if len(found[role]) == 1)
        if score > best_score:
            best = found
            best_score = score
    missing = [role for role in ROLES if best is None or len(best[role]) == 0]
    duplicated = [role for role in ROLES if best is not None and len(best[role]) > 1]
    parts = [f"kolom {role} tidak ditemukan" for role in missing]
    parts.extend(f"kolom {role} muncul lebih dari sekali" for role in duplicated)
    raise StatementValidationError("Format CSV BCA tidak dikenali: " + ", ".join(parts))


def _match_header(row: Sequence[Any]) -> Dict[str, List[int]]:
    found = {role: [] for role in ROLES}
    for index, cell in enumerate(row):
        label = _normalize_header(cell)
        if not label:
            continue
        for role in ROLES:
            if label in HEADER_ALIASES[role]:
                found[role].append(index)
                break
    return found


def _parse_row(row: Sequence[Any], columns: Dict[str, int]) -> dict:
    raw_date = _text(_cell(row, columns["tanggal"]))
    keterangan = _text(_cell(row, columns["keterangan"]))
    if not keterangan:
        raise StatementValidationError("Keterangan kosong")
    debit = _parse_amount(_cell(row, columns["debit"]))
    kredit = _parse_amount(_cell(row, columns["kredit"]))
    saldo = _parse_amount(_cell(row, columns["saldo"]), allow_negative=True)
    debit_filled = debit is not None and debit > 0
    kredit_filled = kredit is not None and kredit > 0
    if debit_filled and kredit_filled:
        raise StatementValidationError("Baris tidak boleh berisi debit dan kredit sekaligus")
    if not debit_filled and not kredit_filled:
        raise StatementValidationError("Debit dan kredit kosong")
    if debit_filled:
        direction = "out"
        debit_value = _number(debit)
        kredit_value = None
    else:
        direction = "in"
        debit_value = None
        kredit_value = _number(kredit)
    if raw_date.upper() == "PEND":
        tanggal = None
        original_date = "PEND"
        date_status = PEND_STATUS
    else:
        tanggal = _parse_date(raw_date)
        original_date = raw_date
        date_status = "valid"
    return {
        "tanggal": tanggal,
        "original_date": original_date,
        "date_status": date_status,
        "keterangan": keterangan,
        "debit": debit_value,
        "kredit": kredit_value,
        "saldo": None if saldo is None else _number(saldo),
        "direction": direction,
    }


def _confirmation_index(items: Optional[list]) -> Dict[int, dict]:
    if items is None:
        items = []
    if not isinstance(items, list):
        raise StatementValidationError("Konfirmasi tanggal tidak dapat dibaca")
    found: Dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            raise StatementValidationError("Konfirmasi tanggal tidak dapat dibaca")
        source_row = item.get("source_row")
        if isinstance(source_row, bool) or not isinstance(source_row, int):
            raise StatementValidationError("Baris konfirmasi tidak valid")
        if source_row in found:
            raise StatementValidationError("Konfirmasi tanggal dobel untuk baris yang sama")
        alasan = _text(item.get("alasan"))
        if not alasan:
            raise StatementValidationError("Alasan konfirmasi wajib")
        found[source_row] = {"tanggal": _confirmed_iso_date(item.get("tanggal")), "alasan": alasan}
    return found


def _confirmed_iso_date(value: Any) -> str:
    text = _text(value)
    if not _ISO_DATE.fullmatch(text):
        raise StatementValidationError("Tanggal tidak valid")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise StatementValidationError("Tanggal tidak valid") from exc
    if parsed.strftime("%Y-%m-%d") != text:
        raise StatementValidationError("Tanggal tidak valid")
    return text


def _parse_date(value: Any) -> str:
    text = _text(value)
    if _SLASH_DATE.fullmatch(text):
        date_format = "DD/MM/YYYY"
    elif _DASH_DATE.fullmatch(text):
        date_format = "DD-MM-YYYY"
    else:
        raise StatementValidationError("Tanggal tidak valid")
    try:
        return parse_date(text, date_format)
    except StatementValidationError as exc:
        raise StatementValidationError("Tanggal tidak valid") from exc


def _parse_amount(value: Any, *, allow_negative: bool = False) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise StatementValidationError("Nominal tidak valid")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, (int, float)):
        amount = Decimal(str(value))
    else:
        text = _text(value).replace(" ", "").replace("Rp", "").replace("IDR", "")
        if text in _EMPTY_AMOUNT:
            return None
        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative = True
            text = text[1:-1]
        elif text.startswith("+"):
            text = text[1:]
        elif text.startswith("-"):
            negative = True
            text = text[1:]
        if not text:
            return None
        try:
            amount = Decimal(_normalize_number(text))
        except (InvalidOperation, ValueError) as exc:
            raise StatementValidationError("Nominal tidak valid") from exc
        if negative:
            amount = -amount
    amount = amount.quantize(MONEY)
    if amount < 0 and not allow_negative:
        raise StatementValidationError("Nominal tidak valid")
    return amount


def _normalize_number(text: str) -> str:
    if text.isdigit():
        return text
    has_comma = "," in text
    has_dot = "." in text
    if has_comma and has_dot:
        if text.rfind(",") > text.rfind("."):
            head, frac = text.rsplit(",", 1)
            sep = "."
        else:
            head, frac = text.rsplit(".", 1)
            sep = ","
        if not _FRACTION.fullmatch(frac) or not _thousands(head, sep):
            raise StatementValidationError("Nominal tidak valid")
        return head.replace(sep, "") + "." + frac
    sep = "," if has_comma else "."
    if sep in text and _thousands(text, sep):
        return text.replace(sep, "")
    if sep in text:
        head, frac = text.rsplit(sep, 1)
        if head.isdigit() and _FRACTION.fullmatch(frac) and sep not in head:
            return head + "." + frac
    raise StatementValidationError("Nominal tidak valid")


def _thousands(head: str, sep: str) -> bool:
    if sep not in head:
        return head.isdigit()
    parts = head.split(sep)
    if not parts[0].isdigit() or not 1 <= len(parts[0]) <= 3:
        return False
    return all(part.isdigit() and len(part) == 3 for part in parts[1:])


def _normalize_header(value: Any) -> str:
    return " ".join(_text(value).replace("\u00a0", " ").lower().split())


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _cell(row: Sequence[Any], index: int) -> Any:
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _blank(row: Sequence[Any]) -> bool:
    return not any(_text(cell) for cell in row)


def _number(amount: Decimal) -> float:
    return float(amount.quantize(MONEY))
