"""Bulk physical opening balance import.

Preview never writes stock. Confirm revalidates every row and posts
OPENING_BALANCE through the active warehouse engine:

- R2 authority ON → one Mongo multi-document transaction (existing path).
- Warehouse standalone ON / R2 OFF → claim batch + per-line standalone
  writer with idempotent resume (no multi-doc transaction).

A later file for an identity that already has an opening is rejected and
does not overwrite it.
"""
from __future__ import annotations

import asyncio
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from services.physical_stock import (
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    PhysicalStockAbortError,
    PhysicalStockConflictError,
    PhysicalStockRetryError,
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

STATUS_IMPORT_PROCESSING = "PROCESSING"
STATUS_IMPORT_CONFIRMED = "CONFIRMED"
ENGINE_R2 = "r2"
ENGINE_STANDALONE = "warehouse_standalone"
_STANDALONE_LEASE_SECONDS = 30
_STANDALONE_POLL_ATTEMPTS = 80
_STANDALONE_POLL_SLEEP_SEC = 0.05


def _lease_expiry_iso(*, seconds: int = _STANDALONE_LEASE_SECONDS) -> str:
    # Match services.physical_stock.now_iso() formatting so Mongo string compares work.
    return (
        datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds)))
    ).isoformat()

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


def _workbook(rows: List[list]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Saldo Awal"
    sheet.append(list(HEADERS))
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _item_active(master: dict) -> bool:
    if master.get("is_active") is False or master.get("active") is False:
        return False
    status = str(master.get("status") or "").strip().lower()
    return status not in {"inactive", "nonaktif", "non-active"}


def _template_warehouse(context: dict, warehouse_id: Optional[str]) -> dict:
    requested = str(warehouse_id or "").strip()
    if not requested:
        raise OpeningImportError("Pilih gudang sebelum mengunduh template.")
    warehouse = next((row for row in context["warehouses"] if row.get("id") == requested), None)
    if not warehouse:
        raise OpeningImportError("Gudang tidak dapat diidentifikasi.")
    return warehouse


def _warehouse_label(warehouse: dict) -> str:
    name = str(warehouse.get("name") or "").strip()
    code = str(warehouse.get("code") or "").strip()
    identity = str(warehouse.get("id") or "").strip()
    if name and name != identity:
        return name
    if code and code != identity:
        return code
    raise OpeningImportError("Nama gudang tidak tersedia.")


async def _blocked_master_ids(db, branch_id: str, warehouse_id: str) -> set:
    identity = {"branch_id": branch_id, "warehouse_id": warehouse_id}
    blocked = set()
    for collection in (LEDGER_COLLECTION, BALANCE_COLLECTION):
        rows = await db[collection].find(identity, {"_id": 0, "master_item_id": 1}).to_list(20000)
        for row in rows:
            master_id = str(row.get("master_item_id") or "").strip()
            if master_id:
                blocked.add(master_id)
    return blocked


async def build_template_xlsx(db, *, warehouse_id: Optional[str] = None) -> bytes:
    """Read-only workbook of items that can still receive an opening balance."""
    context = await _context(db)
    warehouse = _template_warehouse(context, warehouse_id)
    branch = context["branches"].get(warehouse.get("branch_id"))
    if not branch or branch.get("active") is not True or warehouse.get("active") is not True:
        raise OpeningImportError("Cabang atau gudang tidak aktif.")
    label = _warehouse_label(warehouse)
    blocked = await _blocked_master_ids(db, branch["id"], warehouse["id"])
    rows = []
    seen = set()
    masters = sorted(
        context["by_id"].values(),
        key=lambda master: str(master.get("item_code") or "").casefold(),
    )
    for master in masters:
        code = str(master.get("item_code") or "").strip()
        if not code or not master.get("id"):
            continue
        folded = code.casefold()
        if folded in seen or len(context["by_code"].get(code) or []) != 1:
            continue
        if not _item_active(master) or not _inventory_item(master):
            continue
        if master["id"] in blocked:
            continue
        seen.add(folded)
        unit = str(master.get("base_unit") or master.get("unit") or "").strip()
        name = str(master.get("item_name") or master.get("name") or code).strip()
        rows.append([code, name, unit, label, None])
    return _workbook(rows)


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


def _public_import(doc: dict) -> dict:
    public = dict(doc or {})
    public.pop("_id", None)
    return public


def _line_identity(line: dict) -> Tuple[str, str]:
    return (str(line.get("master_item_id") or ""), str(line.get("warehouse_id") or ""))


async def _prepare_confirm_payload(db, payload: dict) -> Tuple[str, List[dict], List[list], Optional[dict]]:
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
        return key, [], list(existing.get("fingerprint") or []), existing
    rows = await revalidate_submitted_lines(db, incoming)
    ready = _ready_lines(rows)
    if len(ready) != len(rows):
        raise OpeningImportError("Impor ditolak. Tidak ada stok yang diubah.", rows=rows)
    return key, ready, _fingerprint(ready), None


async def _confirm_opening_import_r2(db, payload: dict, *, actor_id: str) -> dict:
    """Atomic multi-doc transaction path used when R2 authority is active."""
    await require_transaction_support(_client(db))
    key, ready, fingerprint, existing = await _prepare_confirm_payload(db, payload)
    if existing:
        return {"import": _public_import(existing), "idempotent": True, "engine": existing.get("engine") or ENGINE_R2}
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
                    "status": STATUS_IMPORT_CONFIRMED,
                    "engine": ENGINE_R2,
                    "lines": created,
                }
                await db[IMPORTS].insert_one(dict(document), session=session)
                saved = _public_import(document)
    except (PhysicalStockConflictError, PhysicalStockValidationError, StockOpnameInProgressError, PhysicalStockAbortError) as exc:
        raise OpeningImportError(str(exc), status_code=409) from exc
    except DuplicateKeyError:
        raced = await db[IMPORTS].find_one({"idempotency_key": key}, {"_id": 0})
        if raced and raced.get("fingerprint") == fingerprint:
            return {
                "import": _public_import(raced),
                "idempotent": True,
                "engine": raced.get("engine") or ENGINE_R2,
            }
        raise OpeningImportError(
            "Idempotency key sudah digunakan dengan payload yang berbeda",
            status_code=409,
        )
    except PyMongoError as exc:
        raise PhysicalStockUnavailableError("Impor saldo awal gagal.") from exc
    return {"import": saved, "idempotent": False, "engine": ENGINE_R2}


async def _apply_standalone_opening_line(
    db,
    line: dict,
    *,
    actor_id: str,
    batch_id: str,
    idempotency_key: str,
) -> dict:
    from services.warehouse_stock_standalone import apply_standalone_movement

    request = _line_request(
        line,
        actor_id=actor_id,
        batch_id=batch_id,
        idempotency_key=idempotency_key,
    )
    last_error: Optional[BaseException] = None
    for _ in range(5):
        try:
            return await apply_standalone_movement(db, request, actor_id=actor_id)
        except PhysicalStockRetryError as exc:
            last_error = exc
            continue
    raise PhysicalStockUnavailableError(
        f"Impor saldo awal gagal setelah retry: {last_error}"
    )


async def _release_standalone_lease(db, *, key: str, lease_token: str) -> None:
    await db[IMPORTS].update_one(
        {"idempotency_key": key, "lease_token": lease_token, "status": STATUS_IMPORT_PROCESSING},
        {
            "$set": {
                "lease_expires_at": now_iso(),
                "updated_at": now_iso(),
            },
            "$unset": {"lease_token": ""},
        },
    )


async def _acquire_standalone_lease(db, *, key: str) -> Optional[dict]:
    """Exclusive worker lease for a PROCESSING import batch."""
    now = now_iso()
    lease_token = str(uuid.uuid4())
    doc = await db[IMPORTS].find_one_and_update(
        {
            "idempotency_key": key,
            "status": STATUS_IMPORT_PROCESSING,
            "$or": [
                {"lease_token": {"$exists": False}},
                {"lease_token": None},
                {"lease_expires_at": {"$exists": False}},
                {"lease_expires_at": None},
                {"lease_expires_at": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "lease_token": lease_token,
                "lease_expires_at": _lease_expiry_iso(),
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        return None
    public = _public_import(doc)
    public["_lease_token"] = lease_token
    return public


async def _await_confirmed_or_lease(
    db,
    *,
    key: str,
    fingerprint: List[list],
    actor_id: str,
) -> dict:
    """Wait for the lease holder to finish, or take over a stale PROCESSING batch."""
    for _ in range(_STANDALONE_POLL_ATTEMPTS):
        existing = await db[IMPORTS].find_one({"idempotency_key": key}, {"_id": 0})
        if not existing:
            raise OpeningImportError("Batch impor tidak ditemukan.", status_code=409)
        if existing.get("fingerprint") != fingerprint:
            raise OpeningImportError(
                "Idempotency key sudah digunakan dengan payload yang berbeda",
                status_code=409,
            )
        if existing.get("status") == STATUS_IMPORT_CONFIRMED:
            return {
                "import": _public_import(existing),
                "idempotent": True,
                "engine": existing.get("engine") or ENGINE_STANDALONE,
            }
        if existing.get("status") != STATUS_IMPORT_PROCESSING:
            raise OpeningImportError(
                "Status batch impor tidak dikenali.",
                status_code=409,
            )
        leased = await _acquire_standalone_lease(db, key=key)
        if leased:
            source = leased.get("source_lines") or []
            if not source:
                await _release_standalone_lease(
                    db, key=key, lease_token=str(leased.get("_lease_token") or ""),
                )
                raise OpeningImportError(
                    "Batch impor standalone tidak dapat dilanjutkan.",
                    status_code=409,
                )
            saved = await _finalize_standalone_import_lines(
                db,
                key=key,
                batch_id=str(leased.get("id") or ""),
                fingerprint=list(leased.get("fingerprint") or fingerprint),
                actor_id=actor_id,
                ready=source,
                created=list(leased.get("lines") or []),
                lease_token=str(leased.get("_lease_token") or ""),
            )
            return {
                "import": saved,
                "idempotent": True,
                "engine": ENGINE_STANDALONE,
            }
        await asyncio.sleep(_STANDALONE_POLL_SLEEP_SEC)
    raise PhysicalStockUnavailableError(
        "Impor saldo awal masih diproses oleh permintaan lain. Coba lagi."
    )


async def _finalize_standalone_import_lines(
    db,
    *,
    key: str,
    batch_id: str,
    fingerprint: List[list],
    actor_id: str,
    ready: List[dict],
    created: List[dict],
    lease_token: str,
) -> dict:
    done = {_line_identity(row) for row in created}
    working = list(created)
    try:
        for line in ready:
            identity = _line_identity(line)
            if identity in done:
                continue
            movement = await _apply_standalone_opening_line(
                db,
                line,
                actor_id=actor_id,
                batch_id=batch_id,
                idempotency_key=key,
            )
            working.append({
                "master_item_id": line["master_item_id"],
                "branch_id": line["branch_id"],
                "warehouse_id": line["warehouse_id"],
                "physical_qty": line["physical_qty"],
                "movement_id": movement.get("id"),
            })
            done.add(identity)
            await db[IMPORTS].update_one(
                {
                    "idempotency_key": key,
                    "status": STATUS_IMPORT_PROCESSING,
                    "lease_token": lease_token,
                },
                {
                    "$set": {
                        "lines": working,
                        "updated_at": now_iso(),
                        "lease_expires_at": _lease_expiry_iso(),
                    }
                },
            )
    except (PhysicalStockConflictError, PhysicalStockValidationError, StockOpnameInProgressError) as exc:
        await _release_standalone_lease(db, key=key, lease_token=lease_token)
        raise OpeningImportError(str(exc), status_code=409) from exc
    except PhysicalStockUnavailableError:
        await _release_standalone_lease(db, key=key, lease_token=lease_token)
        raise
    except PyMongoError as exc:
        await _release_standalone_lease(db, key=key, lease_token=lease_token)
        raise PhysicalStockUnavailableError("Impor saldo awal gagal.") from exc

    confirmed_at = now_iso()
    await db[IMPORTS].update_one(
        {"idempotency_key": key, "lease_token": lease_token},
        {
            "$set": {
                "status": STATUS_IMPORT_CONFIRMED,
                "lines": working,
                "engine": ENGINE_STANDALONE,
                "confirmed_at": confirmed_at,
                "updated_at": confirmed_at,
            },
            "$unset": {"lease_token": "", "lease_expires_at": ""},
        },
    )
    stored = await db[IMPORTS].find_one({"idempotency_key": key}, {"_id": 0})
    if not stored:
        return {
            "id": batch_id,
            "idempotency_key": key,
            "fingerprint": fingerprint,
            "actor_id": actor_id,
            "status": STATUS_IMPORT_CONFIRMED,
            "engine": ENGINE_STANDALONE,
            "lines": working,
            "confirmed_at": confirmed_at,
        }
    return _public_import(stored)


async def _confirm_opening_import_standalone(db, payload: dict, *, actor_id: str) -> dict:
    """Standalone path: claim batch first, post lines via pending-event writer, resume on retry.

    Not multi-document atomic. Safety:
    - unique idempotency_key claim prevents concurrent double-batch inserts
    - worker lease ensures only one finalize runs at a time
    - per-line movement idempotency_key prevents duplicate OPENING_BALANCE
    - PROCESSING + source_lines enables idempotent resume after mid-batch failure
    """
    key, ready, fingerprint, existing = await _prepare_confirm_payload(db, payload)
    if existing and existing.get("status") == STATUS_IMPORT_CONFIRMED:
        return {
            "import": _public_import(existing),
            "idempotent": True,
            "engine": existing.get("engine") or ENGINE_STANDALONE,
        }
    if existing:
        return await _await_confirmed_or_lease(
            db,
            key=key,
            fingerprint=fingerprint,
            actor_id=actor_id,
        )

    batch_id = str(uuid.uuid4())
    lease_token = str(uuid.uuid4())
    created_at = now_iso()
    source_lines = [
        {
            "master_item_id": line["master_item_id"],
            "branch_id": line["branch_id"],
            "warehouse_id": line["warehouse_id"],
            "physical_qty": line["physical_qty"],
            "item_code": line.get("item_code") or "",
        }
        for line in ready
    ]
    claim = {
        "id": batch_id,
        "idempotency_key": key,
        "fingerprint": fingerprint,
        "actor_id": actor_id,
        "created_at": created_at,
        "updated_at": created_at,
        "status": STATUS_IMPORT_PROCESSING,
        "engine": ENGINE_STANDALONE,
        "source_lines": source_lines,
        "lines": [],
        "lease_token": lease_token,
        "lease_expires_at": _lease_expiry_iso(),
    }
    try:
        await db[IMPORTS].insert_one(dict(claim))
    except DuplicateKeyError:
        raced = await db[IMPORTS].find_one({"idempotency_key": key}, {"_id": 0})
        if not raced or raced.get("fingerprint") != fingerprint:
            raise OpeningImportError(
                "Idempotency key sudah digunakan dengan payload yang berbeda",
                status_code=409,
            ) from None
        return await _await_confirmed_or_lease(
            db,
            key=key,
            fingerprint=fingerprint,
            actor_id=actor_id,
        )

    saved = await _finalize_standalone_import_lines(
        db,
        key=key,
        batch_id=batch_id,
        fingerprint=fingerprint,
        actor_id=actor_id,
        ready=source_lines,
        created=[],
        lease_token=lease_token,
    )
    return {"import": saved, "idempotent": False, "engine": ENGINE_STANDALONE}


async def confirm_opening_import(db, payload: dict, *, actor_id: str) -> dict:
    """Confirm validated opening lines on the active warehouse engine."""
    from services.r2_go_live import is_r2_authority_active
    from services.warehouse_stock_standalone import is_warehouse_stock_standalone_active

    r2_on = await is_r2_authority_active(db)
    if r2_on:
        return await _confirm_opening_import_r2(db, payload, actor_id=actor_id)
    if await is_warehouse_stock_standalone_active(db):
        return await _confirm_opening_import_standalone(db, payload, actor_id=actor_id)
    raise OpeningImportError(
        "Impor saldo awal memerlukan r2_authority_active atau warehouse_stock_standalone.",
        status_code=409,
    )
