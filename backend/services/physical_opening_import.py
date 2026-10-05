"""Bulk R2 physical opening balance import.

Preview never writes stock. Confirm revalidates every row and posts
OPENING_BALANCE through the existing physical-stock transaction. One
request is one Mongo transaction. A later file for an identity that
already has an opening is rejected and does not overwrite it.
"""
from __future__ import annotations

import csv
import io
import uuid
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from pymongo.errors import DuplicateKeyError, PyMongoError

from services.physical_stock import (
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    PhysicalStockAbortError,
    PhysicalStockConflictError,
    PhysicalStockUnavailableError,
    PhysicalStockValidationError,
    StockOpnameInProgressError,
    WAREHOUSES_COLLECTION,
    _apply_movement_in_session,
    _as_qty,
    _check_sign,
    _client,
    now_iso,
    opening_reference_id,
    require_transaction_support,
    resolve_operational_branch,
)

IMPORTS = "physical_opening_imports"
HEADERS = ("Kode Barang", "Nama Barang", "Satuan Dasar", "Gudang", "Stock Fisik")

STATUS_READY = "Siap"
STATUS_UNKNOWN = "Kode Tidak Ditemukan"
STATUS_QTY = "Qty Tidak Valid"
STATUS_DUPLICATE = "Duplikat"
STATUS_OPENING = "Saldo Awal Sudah Ada"
STATUS_BLOCKED = "Tidak Bisa Diinisialisasi"

_SERVICE_TYPES = {"service", "jasa", "non_inventory", "non-inventory", "noninventory"}


class OpeningImportError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, rows: Optional[List[dict]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.rows = rows or []


def build_template_xlsx() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Saldo Awal"
    sheet.append(list(HEADERS))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_qty(value: Any) -> Tuple[Optional[int], Optional[str]]:
    if value is None or value == "":
        return None, STATUS_QTY
    if isinstance(value, bool):
        return None, STATUS_QTY
    if isinstance(value, int):
        qty = value
    elif isinstance(value, float):
        if not value.is_integer() or abs(value) == float("inf"):
            return None, STATUS_QTY
        qty = int(value)
    else:
        text = str(value).strip().replace(" ", "")
        if not text or text.startswith("+"):
            return None, STATUS_QTY
        if not text.isdigit():
            return None, STATUS_QTY
        qty = int(text)
    if qty < 0:
        return None, STATUS_QTY
    return qty, None


def parse_opening_table(content: bytes, filename: str) -> List[dict]:
    name = str(filename or "").lower()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
    else:
        try:
            book = load_workbook(io.BytesIO(content), data_only=True)
        except Exception as exc:
            raise OpeningImportError("File tidak dapat dibaca.") from exc
        sheet = book.active
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    if not rows:
        raise OpeningImportError("File kosong.")
    header = [_cell(cell) for cell in rows[0]]
    missing = [name for name in ("Kode Barang", "Gudang", "Stock Fisik") if name not in header]
    if missing:
        raise OpeningImportError("Kolom wajib tidak lengkap: " + ", ".join(missing))
    index = {name: header.index(name) for name in HEADERS if name in header}
    parsed = []
    for offset, raw in enumerate(rows[1:], start=2):
        if raw is None or all(_cell(cell) == "" for cell in raw):
            continue

        def take(column: str) -> Any:
            position = index.get(column)
            if position is None or position >= len(raw):
                return ""
            return raw[position]

        qty, qty_status = _parse_qty(take("Stock Fisik"))
        parsed.append({
            "row_number": offset,
            "item_code": _cell(take("Kode Barang")),
            "item_name": _cell(take("Nama Barang")),
            "unit": _cell(take("Satuan Dasar")),
            "warehouse_text": _cell(take("Gudang")),
            "physical_qty": qty,
            "qty_status": qty_status,
        })
    return parsed


def _inventory_item(master: dict) -> bool:
    kind = str(master.get("item_type") or "").strip().lower()
    return kind not in _SERVICE_TYPES


def _unit_ok(master: dict, supplied: str) -> bool:
    if not supplied:
        return True
    expected = str(master.get("base_unit") or master.get("unit") or "").strip().lower()
    return not expected or supplied.strip().lower() == expected


def _warehouse_match(text: str, warehouses: List[dict]) -> List[dict]:
    needle = text.strip().lower()
    active = [row for row in warehouses if row.get("active") is not False and row.get("id")]
    return [
        row for row in active
        if str(row.get("code") or "").strip().lower() == needle
        or str(row.get("name") or "").strip().lower() == needle
    ]


def _submitted_fingerprint(lines: List[dict]) -> Optional[List[list]]:
    rows = []
    for line in lines:
        if not isinstance(line, dict):
            return None
        qty, qty_status = _parse_qty(line.get("physical_qty"))
        master_id = str(line.get("master_item_id") or "").strip()
        branch_id = str(line.get("branch_id") or "").strip()
        warehouse_id = str(line.get("warehouse_id") or "").strip()
        if qty_status or qty is None or not master_id or not branch_id or not warehouse_id:
            return None
        rows.append([master_id, branch_id, warehouse_id, qty])
    return sorted(rows)


def _fingerprint(lines: List[dict]) -> List[list]:
    rows = [
        [
            str(line["master_item_id"]),
            str(line["branch_id"]),
            str(line["warehouse_id"]),
            int(line["physical_qty"]),
        ]
        for line in lines
    ]
    return sorted(rows)


async def _context(db) -> dict:
    masters = await db[MASTER_ITEMS_COLLECTION].find({}, {"_id": 0}).to_list(20000)
    by_code: Dict[str, List[dict]] = {}
    for master in masters:
        code = str(master.get("item_code") or "").strip()
        if code:
            by_code.setdefault(code, []).append(master)
    branches = await db[BRANCHES_COLLECTION].find({}, {"_id": 0}).to_list(500)
    warehouses = await db[WAREHOUSES_COLLECTION].find({}, {"_id": 0}).to_list(500)
    return {
        "by_code": by_code,
        "by_id": {master.get("id"): master for master in masters if master.get("id")},
        "branches": {row.get("id"): row for row in branches if row.get("id")},
        "warehouses": warehouses,
        "operational": resolve_operational_branch(branches),
    }


def _default_warehouse(context: dict) -> Optional[dict]:
    operational = context.get("operational") or {}
    branch_id = operational.get("id")
    if not branch_id:
        return None
    active = [
        row for row in context["warehouses"]
        if row.get("branch_id") == branch_id and row.get("active") is not False and row.get("id")
    ]
    if len(active) == 1:
        return active[0]
    return None


async def _eligibility(db, master_id: str, branch_id: str, warehouse_id: str) -> Optional[str]:
    identity = {
        "master_item_id": master_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
    }
    opening = await db[LEDGER_COLLECTION].find_one(
        {**identity, "movement_type": MOVEMENT_OPENING_BALANCE},
        {"_id": 1},
    )
    if opening:
        return STATUS_OPENING
    history = await db[LEDGER_COLLECTION].find_one(identity, {"_id": 1})
    balance = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 1})
    if history or balance:
        return STATUS_BLOCKED
    return None


async def evaluate_opening_rows(db, parsed: List[dict]) -> List[dict]:
    context = await _context(db)
    seen: Dict[Tuple[str, str], int] = {}
    for row in parsed:
        key = (row["item_code"].lower(), row["warehouse_text"].lower())
        if row["item_code"]:
            seen[key] = seen.get(key, 0) + 1
    evaluated = []
    for row in parsed:
        item = {
            "row_number": row["row_number"],
            "item_code": row["item_code"],
            "item_name": row["item_name"],
            "unit": row["unit"],
            "warehouse_name": row["warehouse_text"],
            "physical_qty": row["physical_qty"],
            "status": STATUS_BLOCKED,
            "reason": "",
            "master_item_id": None,
            "branch_id": None,
            "warehouse_id": None,
        }
        code = row["item_code"]
        if not code:
            item["status"] = STATUS_UNKNOWN
            evaluated.append(item)
            continue
        if seen[(code.lower(), row["warehouse_text"].lower())] > 1:
            item["status"] = STATUS_DUPLICATE
            evaluated.append(item)
            continue
        matches = context["by_code"].get(code) or []
        if len(matches) != 1:
            folded = [
                master for masters in context["by_code"].values() for master in masters
                if str(master.get("item_code") or "").strip().lower() == code.lower()
            ]
            matches = folded if len(folded) == 1 else []
        if len(matches) != 1:
            item["status"] = STATUS_UNKNOWN
            evaluated.append(item)
            continue
        master = matches[0]
        item["master_item_id"] = master.get("id")
        item["item_name"] = master.get("item_name") or master.get("name") or item["item_name"] or code
        item["unit"] = str(master.get("base_unit") or master.get("unit") or item["unit"] or "")
        if not _inventory_item(master):
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Item jasa tidak dapat diinisialisasi sebagai stok fisik."
            evaluated.append(item)
            continue
        if row["qty_status"] or row["physical_qty"] is None:
            item["status"] = STATUS_QTY
            evaluated.append(item)
            continue
        if not _unit_ok(master, row["unit"]):
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Satuan dasar tidak sesuai."
            evaluated.append(item)
            continue
        warehouse = None
        if row["warehouse_text"]:
            hits = _warehouse_match(row["warehouse_text"], context["warehouses"])
            if len(hits) != 1:
                item["status"] = STATUS_BLOCKED
                item["reason"] = "Gudang tidak dapat diidentifikasi."
                evaluated.append(item)
                continue
            warehouse = hits[0]
        else:
            warehouse = _default_warehouse(context)
            if warehouse is None:
                item["status"] = STATUS_BLOCKED
                item["reason"] = "Gudang wajib dipilih."
                evaluated.append(item)
                continue
        branch = context["branches"].get(warehouse.get("branch_id"))
        if not branch or branch.get("active") is not True or warehouse.get("active") is not True:
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Cabang atau gudang tidak aktif."
            evaluated.append(item)
            continue
        if str(warehouse.get("branch_id") or "") != str(branch.get("id") or ""):
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Gudang tidak berada pada cabang ini."
            evaluated.append(item)
            continue
        item["branch_id"] = branch["id"]
        item["warehouse_id"] = warehouse["id"]
        item["warehouse_name"] = warehouse.get("name") or warehouse.get("code") or row["warehouse_text"]
        blocked = await _eligibility(db, master["id"], branch["id"], warehouse["id"])
        if blocked == STATUS_OPENING:
            item["status"] = STATUS_OPENING
            item["reason"] = "SUDAH MEMILIKI SALDO AWAL"
        elif blocked == STATUS_BLOCKED:
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Identitas ini sudah memiliki mutasi stok fisik."
        else:
            item["status"] = STATUS_READY
        evaluated.append(item)
    return evaluated


def _ready_lines(rows: List[dict]) -> List[dict]:
    return [row for row in rows if row.get("status") == STATUS_READY]


async def preview_opening_import(db, content: bytes, filename: str) -> dict:
    parsed = parse_opening_table(content, filename)
    rows = await evaluate_opening_rows(db, parsed)
    return {
        "rows": rows,
        "ready_count": len(_ready_lines(rows)),
        "blocked_count": len(rows) - len(_ready_lines(rows)),
        "can_confirm": bool(rows) and all(row.get("status") == STATUS_READY for row in rows),
    }


def _line_request(line: dict, *, actor_id: str, batch_id: str, idempotency_key: str) -> dict:
    qty = int(line["physical_qty"])
    _check_sign(MOVEMENT_OPENING_BALANCE, _as_qty(qty, allow_zero=True))
    master_id = str(line["master_item_id"])
    branch_id = str(line["branch_id"])
    warehouse_id = str(line["warehouse_id"])
    return {
        "master_item_id": master_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": qty,
        "reference_type": MOVEMENT_OPENING_BALANCE,
        "reference_id": opening_reference_id(master_id, branch_id, warehouse_id),
        "reason": "Impor saldo awal",
        "actor_id": actor_id,
        "occurred_at": now_iso(),
        "created_at": now_iso(),
        "idempotency_key": f"opening-import:{idempotency_key}:{master_id}:{warehouse_id}",
        "metadata": {
            "import_batch_id": batch_id,
            "import_idempotency_key": idempotency_key,
        },
    }


async def revalidate_submitted_lines(db, lines: List[dict]) -> List[dict]:
    """Check confirm payload by master, branch, and warehouse ids. Does not write."""
    context = await _context(db)
    warehouses = {row.get("id"): row for row in context["warehouses"] if row.get("id")}
    seen: Dict[Tuple[str, str], int] = {}
    prepared = []
    for index, line in enumerate(lines, start=2):
        if not isinstance(line, dict):
            raise OpeningImportError("Baris impor tidak valid.")
        qty, qty_status = _parse_qty(line.get("physical_qty"))
        master_id = str(line.get("master_item_id") or "").strip()
        warehouse_id = str(line.get("warehouse_id") or "").strip()
        prepared.append({
            "row_number": index,
            "item_code": str(line.get("item_code") or "").strip(),
            "master_item_id": master_id,
            "branch_id": str(line.get("branch_id") or "").strip(),
            "warehouse_id": warehouse_id,
            "physical_qty": qty,
            "qty_status": qty_status,
        })
        if master_id and warehouse_id:
            seen[(master_id, warehouse_id)] = seen.get((master_id, warehouse_id), 0) + 1
    rows = []
    for source in prepared:
        master = context["by_id"].get(source["master_item_id"])
        item = {
            "row_number": source["row_number"],
            "item_code": source["item_code"] or (str(master.get("item_code") or "") if master else ""),
            "item_name": "",
            "unit": "",
            "warehouse_name": "",
            "physical_qty": source["physical_qty"],
            "status": STATUS_BLOCKED,
            "reason": "",
            "master_item_id": source["master_item_id"] or None,
            "branch_id": source["branch_id"] or None,
            "warehouse_id": source["warehouse_id"] or None,
        }
        if source["master_item_id"] and source["warehouse_id"] and seen[(source["master_item_id"], source["warehouse_id"])] > 1:
            item["status"] = STATUS_DUPLICATE
            rows.append(item)
            continue
        if not master:
            item["status"] = STATUS_UNKNOWN
            rows.append(item)
            continue
        code = str(master.get("item_code") or "").strip()
        if source["item_code"] and source["item_code"].lower() != code.lower():
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Barang tidak sesuai."
            rows.append(item)
            continue
        item["item_code"] = code or source["item_code"]
        item["item_name"] = master.get("item_name") or master.get("name") or code
        item["unit"] = str(master.get("base_unit") or master.get("unit") or "")
        if not _inventory_item(master):
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Item jasa tidak dapat diinisialisasi sebagai stok fisik."
            rows.append(item)
            continue
        if source["qty_status"] or source["physical_qty"] is None:
            item["status"] = STATUS_QTY
            rows.append(item)
            continue
        warehouse = warehouses.get(source["warehouse_id"])
        if not warehouse:
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Gudang tidak dapat diidentifikasi."
            rows.append(item)
            continue
        branch = context["branches"].get(warehouse.get("branch_id"))
        if source["branch_id"] and source["branch_id"] != str(warehouse.get("branch_id") or ""):
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Cabang tidak sesuai."
            item["warehouse_name"] = warehouse.get("name") or warehouse.get("code") or ""
            rows.append(item)
            continue
        if not branch or branch.get("active") is not True or warehouse.get("active") is not True:
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Cabang atau gudang tidak aktif."
            rows.append(item)
            continue
        item["branch_id"] = branch["id"]
        item["warehouse_id"] = warehouse["id"]
        item["warehouse_name"] = warehouse.get("name") or warehouse.get("code") or ""
        blocked = await _eligibility(db, master["id"], branch["id"], warehouse["id"])
        if blocked == STATUS_OPENING:
            item["status"] = STATUS_OPENING
            item["reason"] = "SUDAH MEMILIKI SALDO AWAL"
        elif blocked == STATUS_BLOCKED:
            item["status"] = STATUS_BLOCKED
            item["reason"] = "Identitas ini sudah memiliki mutasi stok fisik."
        else:
            item["status"] = STATUS_READY
        rows.append(item)
    return rows


async def confirm_opening_import(db, payload: dict, *, actor_id: str) -> dict:
    await require_transaction_support(_client(db))
    key = str((payload or {}).get("idempotency_key") or "").strip()
    if not key:
        raise OpeningImportError("idempotency_key wajib diisi.")
    incoming = (payload or {}).get("lines") or []
    if not incoming:
        raise OpeningImportError("Minimal satu baris saldo awal wajib diisi.")
    submitted = _submitted_fingerprint(incoming)
    existing = await db[IMPORTS].find_one({"idempotency_key": key}, {"_id": 0})
    if existing:
        if submitted is None or existing.get("fingerprint") != submitted:
            raise OpeningImportError(
                "Idempotency key sudah digunakan dengan payload yang berbeda",
                status_code=409,
            )
        return {"import": existing, "idempotent": True}
    rows = await revalidate_submitted_lines(db, incoming)
    ready = _ready_lines(rows)
    if len(ready) != len(rows):
        raise OpeningImportError("Impor ditolak. Tidak ada stok yang diubah.", rows=rows)
    fingerprint = _fingerprint(ready)
    batch_id = str(uuid.uuid4())
    created: List[dict] = []
    try:
        async with await _client(db).start_session() as session:
            async with session.start_transaction():
                for line in ready:
                    movement = await _apply_movement_in_session(
                        db,
                        _line_request(line, actor_id=actor_id, batch_id=batch_id, idempotency_key=key),
                        session,
                    )
                    created.append({
                        "master_item_id": line["master_item_id"],
                        "branch_id": line["branch_id"],
                        "warehouse_id": line["warehouse_id"],
                        "physical_qty": line["physical_qty"],
                        "movement_id": movement.get("id"),
                    })
                document = {
                    "id": batch_id,
                    "idempotency_key": key,
                    "fingerprint": fingerprint,
                    "actor_id": actor_id,
                    "created_at": now_iso(),
                    "status": "CONFIRMED",
                    "lines": created,
                }
                await db[IMPORTS].insert_one(dict(document), session=session)
                document.pop("_id", None)
                saved = document
    except (PhysicalStockConflictError, PhysicalStockValidationError, StockOpnameInProgressError, PhysicalStockAbortError) as exc:
        raise OpeningImportError(str(exc), status_code=409) from exc
    except DuplicateKeyError:
        raced = await db[IMPORTS].find_one({"idempotency_key": key}, {"_id": 0})
        if raced and raced.get("fingerprint") == fingerprint:
            return {"import": raced, "idempotent": True}
        raise OpeningImportError(
            "Idempotency key sudah digunakan dengan payload yang berbeda",
            status_code=409,
        )
    except PyMongoError as exc:
        raise PhysicalStockUnavailableError("Impor saldo awal gagal.") from exc
    return {"import": saved, "idempotent": False}
