"""Preview parser for BCA CSV statements.

Reads a CSV through the existing statement reader and returns normalized rows.
This module does not write, import, pair, post, or change a balance.
"""
from __future__ import annotations

import re
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
_FRACTION = re.compile(r"^\d{1,2}$")


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
    tanggal = _parse_date(_cell(row, columns["tanggal"]))
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
    return {
        "tanggal": tanggal,
        "keterangan": keterangan,
        "debit": debit_value,
        "kredit": kredit_value,
        "saldo": None if saldo is None else _number(saldo),
        "direction": direction,
    }


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
