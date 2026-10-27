"""Product Unit Conversion Setting Service (PR-B Multi-UOM).

Persists unit conversion settings attached to canonical Eternal product ID
(master_item_id).

Backward compatible with legacy single-pair fields:
  transaction_uom / conversion_factor / base_uom

PR-B adds alternate_uoms[]:
  [{ "uom": "PACK", "factor_to_base": 50 }, ...]

Canonical stock base for INVENTORY is PCS. All ledger qty use base_qty PCS.
Changing conversion factors only affects FUTURE transaction snapshots.
Changing Base UOM is blocked once stock history exists (R2 ledger/balances,
legacy stock_movements, or PSS initialized).
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

COLLECTION = "product_unit_settings"
BASE_UOM_PCS = "PCS"

# Physical stock history collections used to lock base UOM changes.
PHYSICAL_LEDGER_COLLECTION = "physical_stock_ledger"
PHYSICAL_BALANCE_COLLECTION = "physical_stock_balances"
LEGACY_MOVEMENTS_COLLECTION = "stock_movements"


class UnitConversionError(ValueError):
    """Invalid multi-UOM conversion request (unknown UOM, bad factor, etc.)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


REASON_UNKNOWN_UOM = "UNKNOWN_UOM"
REASON_INVALID_FACTOR = "INVALID_CONVERSION_FACTOR"
REASON_FRACTIONAL_PCS = "FRACTIONAL_BASE_QTY"
REASON_DOUBLE_CONVERSION = "DOUBLE_CONVERSION"
REASON_DUPLICATE_UOM = "DUPLICATE_UOM"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_uom(uom: Any) -> str:
    return str(uom or "").strip().upper()


def validate_conversion_factor(factor: Any) -> float:
    if factor is None:
        raise ValueError("Faktor konversi wajib diisi")
    try:
        f = float(factor)
    except (TypeError, ValueError) as e:
        raise ValueError("Faktor konversi harus berupa angka") from e

    if math.isnan(f) or math.isinf(f):
        raise ValueError("Faktor konversi tidak valid (NaN / Infinity)")
    if f <= 0:
        raise ValueError("Faktor konversi harus lebih besar dari 0")
    if f == int(f):
        return int(f)
    return round(f, 6)


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise UnitConversionError(REASON_INVALID_FACTOR, "Qty/faktor tidak valid") from exc


def _factor_out(factor: Decimal) -> Any:
    if factor == factor.to_integral_value():
        return int(factor)
    return float(factor)


def legacy_pair_as_alternates(setting: Optional[dict]) -> List[Dict[str, Any]]:
    """Normalize legacy single-pair setting into alternate_uoms list (no base row)."""
    if not setting:
        return []
    if isinstance(setting.get("alternate_uoms"), list) and setting.get("alternate_uoms"):
        out: List[Dict[str, Any]] = []
        seen = set()
        for row in setting["alternate_uoms"]:
            if not isinstance(row, dict):
                continue
            uom = normalize_uom(row.get("uom") or row.get("transaction_uom"))
            if not uom or uom == BASE_UOM_PCS or uom in seen:
                continue
            try:
                factor = validate_conversion_factor(
                    row.get("factor_to_base", row.get("conversion_factor"))
                )
            except ValueError:
                continue
            seen.add(uom)
            out.append({"uom": uom, "factor_to_base": factor})
        return out

    tx = normalize_uom(setting.get("transaction_uom"))
    base = normalize_uom(setting.get("base_uom") or BASE_UOM_PCS)
    if not tx or tx == base:
        return []
    try:
        factor = validate_conversion_factor(setting.get("conversion_factor"))
    except ValueError:
        return []
    return [{"uom": tx, "factor_to_base": factor}]


def factor_map_from_setting(setting: Optional[dict], *, base_uom: str = BASE_UOM_PCS) -> Dict[str, Decimal]:
    """UOM → factor_to_base (base itself = 1)."""
    base = normalize_uom(base_uom) or BASE_UOM_PCS
    mapping: Dict[str, Decimal] = {base: Decimal("1")}
    for row in legacy_pair_as_alternates(setting):
        uom = normalize_uom(row.get("uom"))
        if not uom or uom == base:
            continue
        mapping[uom] = _dec(row.get("factor_to_base"))
    # Legacy primary pair if not already in alternates
    if setting and not (isinstance(setting.get("alternate_uoms"), list) and setting.get("alternate_uoms")):
        tx = normalize_uom(setting.get("transaction_uom"))
        if tx and tx != base and tx not in mapping:
            try:
                mapping[tx] = _dec(validate_conversion_factor(setting.get("conversion_factor")))
            except (ValueError, UnitConversionError):
                pass
    return mapping


def canonicalize_to_base(
    *,
    transaction_qty: Any,
    transaction_uom: Any,
    setting: Optional[dict] = None,
    master: Optional[dict] = None,
    require_integer_base: bool = True,
    already_base: bool = False,
) -> Dict[str, Any]:
    """Authoritative conversion: transaction UOM → base PCS snapshot.

    Factor always comes from validated master setting (never trust client factor).
    If already_base=True, rejects any non-base UOM (prevents double conversion).
    """
    base = normalize_uom(
        (setting or {}).get("base_uom")
        or (master or {}).get("unit")
        or BASE_UOM_PCS
    ) or BASE_UOM_PCS
    # Inventory canonical base is PCS; allow legacy non-PCS base only when already stored.
    tx_uom = normalize_uom(transaction_uom) or base
    tx_qty = _dec(transaction_qty)
    if tx_qty <= 0:
        raise UnitConversionError(REASON_INVALID_FACTOR, "Qty transaksi harus > 0")

    if already_base:
        if tx_uom != base:
            raise UnitConversionError(
                REASON_DOUBLE_CONVERSION,
                f"Qty sudah dalam {base}; jangan konversi ulang dari {tx_uom}",
            )
        factor = Decimal("1")
        base_qty = tx_qty
    else:
        mapping = factor_map_from_setting(setting, base_uom=base)
        if tx_uom not in mapping:
            raise UnitConversionError(
                REASON_UNKNOWN_UOM,
                f"Satuan '{tx_uom}' tidak terdaftar pada master barang",
            )
        factor = mapping[tx_uom]
        if factor <= 0:
            raise UnitConversionError(REASON_INVALID_FACTOR, "Faktor konversi harus > 0")
        base_qty = tx_qty * factor

    if require_integer_base:
        if base_qty != base_qty.to_integral_value():
            raise UnitConversionError(
                REASON_FRACTIONAL_PCS,
                f"Hasil konversi {base_qty} bukan bilangan bulat {base}",
            )
        base_qty = base_qty.to_integral_value()

    return {
        "transaction_qty": float(tx_qty) if tx_qty != tx_qty.to_integral_value() else int(tx_qty),
        "transaction_uom": tx_uom,
        "conversion_factor": _factor_out(factor),
        "base_qty": int(base_qty) if require_integer_base else float(base_qty),
        "base_uom": base,
    }


def normalize_alternate_uoms_payload(
    payload: Dict[str, Any],
    *,
    base_uom: str,
    existing: Optional[dict] = None,
) -> Tuple[List[Dict[str, Any]], str, Any]:
    """Parse alternate_uoms and/or legacy single-pair fields.

    Returns (alternate_uoms, primary_transaction_uom, primary_factor).
    """
    base = normalize_uom(base_uom) or BASE_UOM_PCS
    alternates: List[Dict[str, Any]] = []
    seen = set()

    raw_list = payload.get("alternate_uoms")
    if raw_list is None and existing is not None:
        raw_list = legacy_pair_as_alternates(existing)

    if isinstance(raw_list, list):
        for row in raw_list:
            if not isinstance(row, dict):
                raise HTTPException(400, "alternate_uoms berisi baris tidak valid")
            uom = normalize_uom(row.get("uom") or row.get("transaction_uom"))
            if not uom:
                raise HTTPException(400, "Nama satuan alternatif wajib diisi")
            if uom == base:
                raise HTTPException(400, f"Satuan {base} tidak boleh diulang sebagai alternatif")
            if uom in seen:
                raise HTTPException(400, f"Satuan duplikat: {uom}")
            try:
                factor = validate_conversion_factor(
                    row.get("factor_to_base", row.get("conversion_factor"))
                )
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            seen.add(uom)
            alternates.append({"uom": uom, "factor_to_base": factor})

    # Legacy single-pair fields still accepted (upsert/replace that UOM factor).
    # When alternate_uoms is omitted, prefer explicit transaction_uom + factor as primary.
    legacy_tx = normalize_uom(payload.get("transaction_uom"))
    if legacy_tx and legacy_tx != base and "alternate_uoms" not in payload:
        try:
            factor = validate_conversion_factor(payload.get("conversion_factor"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        alternates = [{"uom": legacy_tx, "factor_to_base": factor}] + [
            a for a in alternates if a["uom"] != legacy_tx
        ]
        seen.add(legacy_tx)

    if not alternates:
        return [], base, 1

    primary = alternates[0]
    return alternates, primary["uom"], primary["factor_to_base"]


async def get_product_unit_setting(db, master_item_id: str) -> Optional[Dict[str, Any]]:
    """Pure read. Returns unit setting doc or None."""
    mid = str(master_item_id or "").strip()
    if not mid:
        return None
    return await db[COLLECTION].find_one({"master_item_id": mid}, {"_id": 0})


async def master_has_stock_history(db, master_item_id: str, master: Optional[dict] = None) -> bool:
    """True if base UOM must stay locked (R2 ledger/balances, legacy movements, PSS)."""
    mid = str(master_item_id or "").strip()
    if not mid:
        return False
    accurate_id = str((master or {}).get("accurate_item_id") or "").strip()

    # R2 physical ledger / balances
    for coll_name in (PHYSICAL_LEDGER_COLLECTION, PHYSICAL_BALANCE_COLLECTION):
        coll = getattr(db, coll_name, None)
        if coll is None:
            try:
                coll = db[coll_name]
            except Exception:
                coll = None
        if coll is not None and hasattr(coll, "count_documents"):
            n = await coll.count_documents({"master_item_id": mid})
            if n > 0:
                return True

    # Legacy stock_movements
    mov = getattr(db, LEGACY_MOVEMENTS_COLLECTION, None)
    if mov is None:
        try:
            mov = db[LEGACY_MOVEMENTS_COLLECTION]
        except Exception:
            mov = None
    if mov is not None and hasattr(mov, "count_documents"):
        clauses = [{"master_item_id": mid}]
        if accurate_id:
            clauses.append({"accurate_item_id": accurate_id})
        n = await mov.count_documents({"$or": clauses} if len(clauses) > 1 else clauses[0])
        if n > 0:
            return True

    # PSS initialized
    state_col = getattr(db, "product_stock_state", None)
    if state_col is not None:
        state = await state_col.find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
        if state and (state.get("initialized") or state.get("status") in ("INITIALIZING", "INITIALIZED")):
            return True
    return False


async def save_product_unit_setting(
    db,
    master_item_id: str,
    payload: Dict[str, Any],
    user: Dict[str, Any],
) -> Dict[str, Any]:
    """Saves or updates unit setting for a product (legacy pair + alternate_uoms)."""
    mid = str(master_item_id or "").strip()
    if not mid:
        raise HTTPException(400, "master_item_id wajib diisi")

    master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    if not master:
        master = await db.master_items.find_one({"accurate_item_id": mid}, {"_id": 0})
        if master:
            mid = master["id"]
        else:
            raise HTTPException(404, f"Produk dengan ID '{mid}' tidak ditemukan")

    existing = await get_product_unit_setting(db, mid)
    item_type = str(master.get("item_type") or "").strip().upper()

    # INVENTORY: canonical base PCS. Others keep legacy flexibility.
    if item_type == "INVENTORY" or not item_type:
        default_base = BASE_UOM_PCS
    else:
        default_base = normalize_uom(master.get("unit") or BASE_UOM_PCS) or BASE_UOM_PCS

    requested_base = normalize_uom(
        payload.get("base_uom") or (existing or {}).get("base_uom") or default_base
    )
    if item_type == "INVENTORY":
        # Enforce PCS as stock base for inventory masters.
        if requested_base and requested_base != BASE_UOM_PCS:
            if await master_has_stock_history(db, mid, master):
                raise HTTPException(
                    400,
                    "Satuan dasar persediaan harus PCS. Perubahan base pada master "
                    "yang sudah ber-saldo/ledger memerlukan migrasi terpisah.",
                )
            # New inventory without history: still force PCS (Owner rule).
            requested_base = BASE_UOM_PCS
        base_uom = BASE_UOM_PCS
    else:
        base_uom = requested_base or BASE_UOM_PCS

    # Lock base change when history exists
    prior_base = normalize_uom((existing or {}).get("base_uom") or "") if existing else ""
    if existing and prior_base and prior_base != base_uom:
        if await master_has_stock_history(db, mid, master):
            raise HTTPException(
                400,
                "Satuan Dasar tidak dapat diubah karena produk sudah memiliki "
                "riwayat mutasi / saldo stok. Gunakan migrasi terpisah.",
            )

    # PSS initialized lock (extra explicit message)
    state_col = getattr(db, "product_stock_state", None)
    if state_col is not None:
        state = await state_col.find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
        if state and (state.get("initialized") or state.get("status") in ("INITIALIZING", "INITIALIZED")):
            if normalize_uom(state.get("base_uom") or base_uom) != base_uom:
                raise HTTPException(
                    400,
                    "Satuan Dasar tidak dapat diubah karena saldo awal / stok fisik produk sudah diinisialisasi. "
                    "Perubahan satuan dasar akan merusak interpretasi saldo historis.",
                )

    alternates, primary_tx, primary_factor = normalize_alternate_uoms_payload(
        payload or {},
        base_uom=base_uom,
        existing=existing,
    )

    now = now_iso()
    setting_id = (existing or {}).get("id") or str(uuid.uuid4())
    doc = {
        "id": setting_id,
        "master_item_id": mid,
        "accurate_item_id": str(master.get("accurate_item_id") or ""),
        "base_uom": base_uom,
        # Legacy single-pair fields kept for older readers (primary alternate).
        "transaction_uom": primary_tx,
        "conversion_factor": primary_factor,
        "alternate_uoms": alternates,
        "updated_at": now,
        "updated_by": user.get("email") or user.get("id") or "system",
        "updated_by_name": user.get("name") or user.get("email") or "",
    }
    if not existing:
        doc["created_at"] = now

    await db[COLLECTION].update_one(
        {"master_item_id": mid},
        {"$set": doc},
        upsert=True,
    )

    saved = await db[COLLECTION].find_one({"master_item_id": mid}, {"_id": 0})
    return saved
