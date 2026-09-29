"""Statement layout recognized from header structure, not from a file name.

The recognized header is exactly:

    Date, Description, Branch, Amount, <empty>, Balance

Metadata before that header is ``label, =, value``. Summary rows after the
transactions are ``Starting Balance``, ``Credit``, ``Debet``, and
``Ending Balance``. Debit and credit are not given a universal meaning.
Direction is taken from the running-balance change in the same file.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.cash_bank_master import CashBankValidationError, normalize_currency_code

HEADER = ["Date", "Description", "Branch", "Amount", "", "Balance"]
FOOTER_LABELS = ("Starting Balance", "Credit", "Debet", "Ending Balance")
AMOUNT_TEXT = re.compile(r"^\d+\.\d{2}$")
EXPLICIT_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
PEND_PARTS = re.compile(r"(?<!\d)(\d{2})(\d{2})/")
DAY_WINDOW = 31
INDICATORS = ("DB", "CR")


class LayoutRejected(ValueError):
    def __init__(self, message: str, errors: Optional[List[dict]] = None):
        super().__init__(message)
        self.errors = errors or []


def line_number(matrix: Sequence[Any], index: int) -> int:
    numbers = getattr(matrix, "line_numbers", None)
    if numbers and 0 <= index < len(numbers):
        return int(numbers[index])
    return index + 1


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _cells(row: Sequence[Any]) -> List[str]:
    return [_text(cell) for cell in row]


def _is_header(row: Sequence[Any]) -> bool:
    cells = _cells(row)
    if len(cells) < 6 or cells[:6] != HEADER:
        return False
    return all(cell == "" for cell in cells[6:])


def _is_metadata(row: Sequence[Any]) -> bool:
    cells = _cells(row)
    if len(cells) < 3 or cells[1] != "=" or not cells[0] or not cells[2]:
        return False
    return all(cell == "" for cell in cells[3:])


def _ambiguous_message(errors: List[dict]) -> str:
    detail = "; ".join(f"Baris {item['row']}: {item['message']}" for item in errors)
    return f"Struktur berkas ambigu dan tidak ditebak. {detail}"


def classify_matrix(matrix: Sequence[Sequence[Any]]) -> dict:
    hits = [index for index, row in enumerate(matrix) if _is_header(row)]
    if not hits:
        return {"status": "generic", "errors": [], "message": ""}
    if len(hits) > 1:
        errors = [
            {
                "row": line_number(matrix, index),
                "message": "Header mutasi muncul lebih dari sekali",
            }
            for index in hits
        ]
        return {"status": "ambiguous", "errors": errors, "message": _ambiguous_message(errors)}

    header_row = hits[0]
    errors: List[dict] = []
    currency_rows: List[Tuple[int, str]] = []
    for index, row in enumerate(matrix[:header_row]):
        if not any(_text(cell) for cell in row):
            continue
        if not _is_metadata(row):
            errors.append({
                "row": line_number(matrix, index),
                "message": "Baris sebelum header bukan metadata label, =, nilai",
            })
            continue
        if _text(row[0]) == "Currency":
            currency_rows.append((index, _text(row[2])))
    currency = None
    distinct = {value for _index, value in currency_rows}
    if len(distinct) > 1:
        for index, _value in currency_rows:
            errors.append({
                "row": line_number(matrix, index),
                "message": "Mata uang metadata tidak tunggal",
            })
    elif currency_rows:
        raw_currency = currency_rows[0][1]
        try:
            currency = normalize_currency_code(raw_currency)
        except CashBankValidationError:
            errors.append({
                "row": line_number(matrix, currency_rows[0][0]),
                "message": "Mata uang pada berkas tidak didukung dan tidak diganti",
            })
    if errors:
        return {
            "status": "ambiguous",
            "header_row": header_row,
            "errors": errors,
            "message": _ambiguous_message(errors),
        }
    return {
        "status": "recognized",
        "header_row": header_row,
        "currency": currency,
        "errors": [],
        "message": "",
    }


def suggested_mapping(classified: dict) -> Optional[dict]:
    if classified.get("status") != "recognized":
        return None
    return {
        "header_row": classified["header_row"],
        "date_column": 0,
        "date_format": "DD/MM/YYYY",
        "description_column": 1,
        "reference_column": None,
        "bank_txn_id_column": None,
        "bank_txn_id_authoritative": False,
        "amount_mode": "indicator",
        "amount_column": 3,
        "indicator_column": 4,
        "sign_convention": "",
        "in_column": None,
        "out_column": None,
        "balance_column": 5,
        "currency_column": None,
        "file_currency": classified.get("currency"),
        "decimal_mode": "dot_decimal",
        "operator_coverage_confirmed": False,
    }


def _money_float(raw: Decimal) -> float:
    return float(raw.quantize(Decimal("0.01")))


def _money_text(raw: Decimal) -> str:
    return f"{raw.quantize(Decimal('0.01')):.2f}"


def _strict_amount(value: Any) -> Tuple[Optional[Decimal], Optional[str]]:
    if isinstance(value, bool):
        return None, "Nominal tidak valid"
    if isinstance(value, str) or value is None:
        text = _text(value)
        if not AMOUNT_TEXT.fullmatch(text):
            return None, (
                "Nominal ambigu atau tidak berbentuk angka dengan titik dan dua digit desimal. "
                "Pemisah ribuan tidak diubah."
            )
        return Decimal(text), None
    if isinstance(value, int):
        if value < 0:
            return None, "Nominal negatif tidak dipakai pada kolom nominal ini"
        return Decimal(value).quantize(Decimal("0.01")), None
    if isinstance(value, Decimal):
        quantized = value.quantize(Decimal("0.01"))
        if quantized != value or value < 0:
            return None, "Nominal tidak tepat dua digit desimal"
        return quantized, None
    return None, "Nominal numerik pecahan tidak dibaca agar pembulatan tidak mengubah nilai"


def _date_kind(value: Any) -> Tuple[str, Optional[str]]:
    if isinstance(value, datetime):
        return "explicit", value.date().isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return "explicit", value.isoformat()[:10]
        except Exception:
            return "invalid", "Tanggal tidak valid"
    text = _text(value)
    if text.startswith("'"):
        text = text[1:]
    if text == "PEND":
        return "pend", None
    if not EXPLICIT_DATE.fullmatch(text):
        return "invalid", f"Tanggal '{_text(value)}' tidak sesuai DD/MM/YYYY"
    try:
        parsed = datetime.strptime(text, "%d/%m/%Y")
    except ValueError:
        return "invalid", f"Tanggal '{text}' tidak valid"
    return "explicit", parsed.date().isoformat()


def _pend_date(description: str, explicit_dates: List[datetime.date]) -> Tuple[Optional[str], Optional[str]]:
    found: List[Tuple[int, int]] = []
    for day_text, month_text in PEND_PARTS.findall(description):
        day = int(day_text)
        month = int(month_text)
        try:
            datetime(2000, month, day)
        except ValueError:
            continue
        found.append((day, month))
    unique = set(found)
    if len(unique) != 1:
        return None, "Tanggal PEND tidak memuat hari dan bulan yang tunggal. Tanggal tidak ditebak."
    years = {item.year for item in explicit_dates}
    if len(years) != 1:
        return None, "Tahun tanggal PEND tidak tertulis dan tidak tunggal pada berkas. Tahun tidak ditebak."
    year = next(iter(years))
    day, month = next(iter(unique))
    try:
        built = datetime(year, month, day).date()
    except ValueError:
        return None, "Tanggal PEND tidak valid untuk tahun yang tertulis pada berkas."
    earliest = min(explicit_dates)
    latest = max(explicit_dates)
    if not (earliest - timedelta(days=DAY_WINDOW) <= built <= latest + timedelta(days=DAY_WINDOW)):
        return None, (
            "Tanggal PEND berada di luar rentang tanggal yang tertulis pada berkas. "
            "Tahun tidak ditebak."
        )
    return built.isoformat(), None


def _cell(row: Sequence[Any], index: Optional[int]) -> Any:
    if index is None or index < 0 or index >= len(row):
        return None
    return row[index]


def _footer(matrix: Sequence[Sequence[Any]], header_row: int) -> Tuple[Optional[int], Dict[str, dict], List[dict]]:
    errors: List[dict] = []
    footer_at = None
    for index in range(header_row + 1, len(matrix)):
        if _text(_cell(matrix[index], 0)) in FOOTER_LABELS:
            footer_at = index
            break
    if footer_at is None:
        return None, {}, errors
    found: Dict[str, dict] = {}
    expected = list(FOOTER_LABELS)
    index = footer_at
    while index < len(matrix):
        row = matrix[index]
        if not any(_text(cell) for cell in row):
            index += 1
            continue
        label = _text(_cell(row, 0))
        line = line_number(matrix, index)
        if not expected:
            errors.append({"row": line, "message": "Ada baris setelah ringkasan saldo. Baris itu tidak dijadikan transaksi."})
            index += 1
            continue
        if label != expected[0] or not _is_metadata(row):
            errors.append({
                "row": line,
                "message": f"Ringkasan saldo tidak berurutan. Diharapkan {expected[0]}.",
            })
            return footer_at, found, errors
        amount, amount_error = _strict_amount(_cell(row, 2))
        if amount_error or amount is None:
            errors.append({"row": line, "message": amount_error or "Nominal ringkasan tidak valid"})
            return footer_at, found, errors
        found[label] = {"amount": amount, "row": line}
        expected.pop(0)
        index += 1
    if expected:
        errors.append({
            "row": line_number(matrix, footer_at),
            "message": "Ringkasan saldo tidak lengkap: " + ", ".join(expected),
        })
    return footer_at, found, errors


def interpret_recognized(matrix: Sequence[Sequence[Any]], mapping: dict) -> dict:
    classified = classify_matrix(matrix)
    if classified.get("status") != "recognized":
        message = classified.get("message") or (
            "Mode penanda arah hanya berlaku bila header Date, Description, Branch, Amount, dan Balance dikenali."
        )
        raise LayoutRejected(message, errors=classified.get("errors") or [])
    expected = suggested_mapping(classified) or {}
    compared = (
        "header_row", "date_column", "description_column", "amount_column",
        "indicator_column", "balance_column", "amount_mode", "decimal_mode", "date_format",
    )
    if any(mapping.get(key) != expected.get(key) for key in compared):
        line = line_number(matrix, classified["header_row"])
        raise LayoutRejected(
            f"Mapping tidak sesuai header yang dikenali pada baris {line}.",
            errors=[{"row": line, "message": "Mapping tidak sesuai header yang dikenali"}],
        )
    file_currency = classified.get("currency")
    mapped_currency = mapping.get("file_currency")
    if file_currency and mapped_currency and mapped_currency != file_currency:
        raise LayoutRejected(
            "Mata uang mapping berbeda dari mata uang yang tertulis pada berkas.",
            errors=[{"row": line_number(matrix, classified["header_row"]), "message": "Mata uang berkas dan mapping berbeda"}],
        )
    currency = file_currency or mapped_currency

    footer_at, footer, errors = _footer(matrix, classified["header_row"])
    end = footer_at if footer_at is not None else len(matrix)
    drafts: List[dict] = []
    explicit_dates: List[datetime.date] = []
    for index in range(classified["header_row"] + 1, end):
        row = matrix[index]
        if not any(_text(cell) for cell in row):
            continue
        line = line_number(matrix, index)
        extra = list(row)[6:]
        if len(row) < 6 or any(_text(cell) for cell in extra):
            errors.append({
                "row": line,
                "message": "Jumlah kolom tidak sesuai header. Isi tidak digabung dan tidak ditebak.",
            })
            continue
        kind, date_value = _date_kind(_cell(row, mapping["date_column"]))
        description = _text(_cell(row, mapping["description_column"]))
        amount, amount_error = _strict_amount(_cell(row, mapping["amount_column"]))
        indicator = _text(_cell(row, mapping["indicator_column"]))
        balance, balance_error = _strict_amount(_cell(row, mapping["balance_column"]))
        reference = None
        if mapping.get("reference_column") is not None:
            reference = _text(_cell(row, mapping["reference_column"])) or None
        bank_txn_id = None
        if mapping.get("bank_txn_id_column") is not None:
            bank_txn_id = _text(_cell(row, mapping["bank_txn_id_column"])) or None
        row_errors = []
        if kind == "invalid":
            row_errors.append(date_value or "Tanggal tidak valid")
        if not description:
            row_errors.append("Uraian kosong")
        if amount_error or amount is None:
            row_errors.append(amount_error or "Nominal kosong")
        if indicator not in INDICATORS:
            row_errors.append("Penanda arah bukan DB atau CR. Arti penanda tidak ditebak.")
        if balance_error or balance is None:
            row_errors.append(balance_error or "Saldo kosong dan tidak diisi")
        draft = {
            "line": line,
            "kind": kind,
            "date": date_value if kind == "explicit" else None,
            "description": description,
            "amount": amount,
            "indicator": indicator,
            "balance": balance,
            "reference": reference,
            "bank_txn_id": bank_txn_id,
            "row_errors": row_errors,
        }
        drafts.append(draft)
        if kind == "explicit" and date_value:
            explicit_dates.append(datetime.strptime(date_value, "%Y-%m-%d").date())
    if not drafts and not errors:
        errors.append({
            "row": line_number(matrix, classified["header_row"]),
            "message": "Tidak ada baris transaksi di bawah header",
        })

    for draft in drafts:
        if draft["kind"] != "pend" or draft["row_errors"]:
            continue
        iso_date, pend_error = _pend_date(draft["description"], explicit_dates)
        if pend_error:
            draft["row_errors"].append(pend_error)
        else:
            draft["date"] = iso_date

    opening = (footer.get("Starting Balance") or {}).get("amount")
    previous = opening
    for draft in drafts:
        balance = draft["balance"]
        amount = draft["amount"]
        if draft["row_errors"] or amount is None or balance is None:
            if balance is not None:
                previous = balance
            continue
        if previous is None:
            previous = balance
            continue
        delta = balance - previous
        draft["previous"] = previous
        draft["delta"] = delta
        if delta == amount:
            draft["observed"] = "in"
        elif delta == -amount:
            draft["observed"] = "out"
        else:
            draft["inconsistent"] = True
        previous = balance

    meanings: Dict[str, str] = {}
    conflicted = set()
    observed: Dict[str, set] = {}
    for draft in drafts:
        direction = draft.get("observed")
        if direction and draft["indicator"] in INDICATORS:
            observed.setdefault(draft["indicator"], set()).add(direction)
    for indicator, directions in observed.items():
        if len(directions) == 1:
            meanings[indicator] = next(iter(directions))
        else:
            conflicted.add(indicator)

    accepted: List[dict] = []
    checks: List[dict] = []
    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    closing = None
    closing_date = None
    for offset, draft in enumerate(drafts, start=1):
        messages = list(draft["row_errors"])
        if draft.get("inconsistent"):
            messages.append(
                "Saldo berjalan tidak sesuai nominal. "
                f"Saldo sebelumnya {_money_text(draft['previous'])}, "
                f"nominal {_money_text(draft['amount'])}, penanda {draft['indicator']}, "
                f"saldo tercatat {_money_text(draft['balance'])}, "
                f"selisih {_money_text(draft['delta'])}. "
                "Nominal dan saldo tidak diubah."
            )
        elif draft.get("observed") and draft["indicator"] in conflicted:
            messages.append("Bukti arah pada penanda yang sama saling bertentangan. Arah tidak dipilih.")
        elif draft["indicator"] in meanings and not draft.get("inconsistent"):
            draft["direction"] = meanings[draft["indicator"]]
        elif not messages and draft["indicator"] in INDICATORS:
            messages.append("Arah penanda tidak terbukti dari saldo pada berkas. Arah tidak ditebak.")
        if messages:
            for message in messages:
                errors.append({"row": draft["line"], "message": message})
        else:
            direction = draft["direction"]
            amount = draft["amount"]
            if direction == "in":
                total_in += amount
            else:
                total_out += amount
            if draft["balance"] is not None:
                closing = draft["balance"]
                closing_date = draft["date"]
            accepted.append({
                "source_row": draft["line"],
                "row_number": offset,
                "date": draft["date"],
                "description": draft["description"],
                "reference": draft["reference"],
                "bank_txn_id": draft["bank_txn_id"],
                "direction": direction,
                "amount": _money_float(amount),
                "running_balance": None if draft["balance"] is None else _money_float(draft["balance"]),
                "currency_code": currency,
            })
        if draft.get("previous") is not None and draft.get("balance") is not None and draft.get("amount") is not None:
            checks.append({
                "row": draft["line"],
                "previous": _money_text(draft["previous"]),
                "amount": _money_text(draft["amount"]),
                "indicator": draft["indicator"],
                "recorded_balance": _money_text(draft["balance"]),
                "delta": _money_text(draft["delta"]),
                "consistent": not draft.get("inconsistent") and draft["indicator"] not in conflicted,
            })

    ending = (footer.get("Ending Balance") or {}).get("amount")
    transactions_complete = bool(drafts) and len(accepted) == len(drafts) and not errors
    equation_ok = None
    if transactions_complete and "Credit" in footer:
        if total_in != footer["Credit"]["amount"]:
            errors.append({
                "row": footer["Credit"]["row"],
                "message": (
                    f"Jumlah masuk {_money_text(total_in)} tidak sama dengan Credit "
                    f"{_money_text(footer['Credit']['amount'])}. Angka tidak disesuaikan."
                ),
            })
    if transactions_complete and "Debet" in footer:
        if total_out != footer["Debet"]["amount"]:
            errors.append({
                "row": footer["Debet"]["row"],
                "message": (
                    f"Jumlah keluar {_money_text(total_out)} tidak sama dengan Debet "
                    f"{_money_text(footer['Debet']['amount'])}. Angka tidak disesuaikan."
                ),
            })
    if transactions_complete and opening is not None and ending is not None:
        computed = opening + total_in - total_out
        equation_ok = computed == ending and closing == ending
        if computed != ending:
            errors.append({
                "row": footer["Ending Balance"]["row"],
                "message": (
                    f"Saldo awal {_money_text(opening)} + masuk {_money_text(total_in)} - keluar "
                    f"{_money_text(total_out)} = {_money_text(computed)}, sedangkan saldo akhir pada berkas "
                    f"{_money_text(ending)}. Angka tidak disesuaikan."
                ),
            })
            equation_ok = False
        elif closing is not None and closing != ending:
            errors.append({
                "row": footer["Ending Balance"]["row"],
                "message": (
                    f"Saldo berjalan terakhir {_money_text(closing)} tidak sama dengan Ending Balance "
                    f"{_money_text(ending)}. Angka tidak disesuaikan."
                ),
            })
            equation_ok = False

    header = matrix[classified["header_row"]]
    return {
        "errors": errors,
        "accepted": accepted,
        "accepted_count": len(accepted),
        "total_in": _money_float(total_in),
        "total_out": _money_float(total_out),
        "closing_balance": None if closing is None else _money_float(closing),
        "closing_date": closing_date,
        "headers": [_text(cell) for cell in header],
        "opening_balance": None if opening is None else _money_float(opening),
        "footer_ending_balance": None if ending is None else _money_float(ending),
        "balance_equation_ok": equation_ok,
        "balance_checks": checks,
        "inconsistencies": [item for item in errors if "Saldo berjalan tidak sesuai" in item["message"] or "tidak sama" in item["message"]],
    }
