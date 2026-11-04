#!/usr/bin/env python3
"""Build a versioned YGY-MAIN ATS baseline from Owner Accurate free-stock export.

Input file = Stok Dapat Dijual already net of open orders at cutoff.
Does NOT write physical_stock_balances / ledger.
Does NOT activate production. activate=False by default (preview write only).

Expected columns (case-insensitive, common Accurate exports):
  kode / item_code / no barang
  nama (optional)
  satuan / unit
  qty / accurate_ats / stok / stok dapat dijual

Usage:
  python3 build_ats_ygy_baseline_from_accurate.py \\
    --file /path/StokDapatDijual.xlsx \\
    --cutoff '2026-11-04T22:30:00+07:00' \\
    [--mongo-url ...] [--activate]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.stock_ats_baseline_store import (  # noqa: E402
    YGY_WAREHOUSE_ID,
    build_baseline_document,
    write_new_baseline_version,
)
from services.stock_ats_cutover import CutoverError, quantity  # noqa: E402


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


_KODE_ALIASES = {
    "kode", "kodebarang", "nobarang", "itemcode", "code", "sku", "itemno",
}
_NAMA_ALIASES = {"nama", "namabarang", "itemname", "name", "description"}
_UNIT_ALIASES = {"satuan", "unit", "uom"}
_QTY_ALIASES = {
    "qty", "quantity", "accurateats", "stok", "stock", "stokdapatdijual",
    "ats", "available", "saldo", "qtyats",
}


def _map_headers(fieldnames) -> dict:
    mapping = {}
    for raw in fieldnames or []:
        key = _norm_header(raw)
        if key in _KODE_ALIASES:
            mapping["kode"] = raw
        elif key in _NAMA_ALIASES:
            mapping["nama"] = raw
        elif key in _UNIT_ALIASES:
            mapping["satuan"] = raw
        elif key in _QTY_ALIASES:
            mapping["qty"] = raw
    missing = [k for k in ("kode", "satuan", "qty") if k not in mapping]
    if missing:
        raise SystemExit(f"Missing required columns {missing}; got {list(fieldnames)}")
    return mapping


def _read_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect)
            mapping = _map_headers(reader.fieldnames)
            out = []
            for row in reader:
                out.append({
                    "kode": str(row.get(mapping["kode"]) or "").strip(),
                    "nama": str(row.get(mapping.get("nama", ""), "") or "").strip(),
                    "satuan": str(row.get(mapping["satuan"]) or "").strip() or "PCS",
                    "qty": row.get(mapping["qty"]),
                })
            return out
    if suffix in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise SystemExit("openpyxl required for xlsx: pip install openpyxl") from exc
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = next(rows_iter, None)
        if not header:
            raise SystemExit("empty workbook")
        mapping = _map_headers([str(h or "") for h in header])
        idx = {k: list(header).index(v) for k, v in mapping.items()}
        out = []
        for values in rows_iter:
            if values is None:
                continue
            kode = str(values[idx["kode"]] or "").strip() if idx["kode"] < len(values) else ""
            if not kode:
                continue
            satuan = (
                str(values[idx["satuan"]] or "").strip()
                if idx["satuan"] < len(values) else ""
            ) or "PCS"
            qty = values[idx["qty"]] if idx["qty"] < len(values) else None
            nama = ""
            if "nama" in idx and idx["nama"] < len(values):
                nama = str(values[idx["nama"]] or "").strip()
            out.append({"kode": kode, "nama": nama, "satuan": satuan, "qty": qty})
        return out
    raise SystemExit(f"Unsupported file type: {suffix}")


async def _match_masters(db, raw_rows: list[dict]) -> dict:
    codes = sorted({r["kode"] for r in raw_rows if r.get("kode")})
    docs = await db.master_items.find(
        {"item_code": {"$in": codes}},
        {"_id": 0, "id": 1, "item_code": 1, "name": 1, "unit": 1, "active": 1, "item_type": 1},
    ).to_list(max(len(codes) * 3, 1))
    by_code: dict = {}
    for d in docs:
        code = str(d.get("item_code") or "").strip()
        by_code.setdefault(code, []).append(d)
    return by_code


def _units_compatible(a: str, b: str) -> bool:
    return str(a or "").strip().upper() == str(b or "").strip().upper()


async def main_async(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        print(f"STOP: file not found: {path}", file=sys.stderr)
        return 2
    cutoff = str(args.cutoff).strip()
    raw_rows = _read_rows(path)
    file_sha = _sha256_file(path)

    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(args.mongo_url)
    db = client[args.db_name]
    by_code = await _match_masters(db, raw_rows)

    eligible = []
    inactive = []
    unmatched = []
    for row in raw_rows:
        kode = row["kode"]
        if not kode:
            continue
        try:
            qty = quantity(row.get("qty") if row.get("qty") not in ("", None) else 0)
        except CutoverError:
            unmatched.append({**row, "reason": "INVALID_QTY"})
            continue
        cands = by_code.get(kode) or []
        if not cands:
            unmatched.append({**row, "reason": "NO_MASTER_ITEM"})
            continue
        unit_matches = [
            c for c in cands
            if _units_compatible(row.get("satuan") or "PCS", c.get("unit") or "PCS")
        ]
        pick = unit_matches[0] if len(unit_matches) == 1 else (
            cands[0] if len(cands) == 1 else None
        )
        if pick is None:
            unmatched.append({**row, "reason": "AMBIGUOUS_MASTER_OR_UNIT"})
            continue
        payload = {
            "kode": kode,
            "nama": row.get("nama") or pick.get("name") or "",
            "satuan": str(row.get("satuan") or pick.get("unit") or "PCS"),
            "accurate_ats": float(qty),
            "master_item_id": str(pick.get("id")),
            "eligible_master": bool(pick.get("active", True)) and str(
                pick.get("item_type") or "INVENTORY"
            ).upper() not in ("SERVICE", "NON_INVENTORY"),
        }
        if not payload["eligible_master"]:
            inactive.append({**payload, "reason": "INACTIVE_OR_NON_INVENTORY"})
            payload["eligible_master"] = False
            # Still keep in rows for EXCLUDED reporting, but mark ineligible
            eligible.append(payload)
        else:
            eligible.append(payload)

    doc = build_baseline_document(
        cutoff_at=cutoff,
        rows=eligible,
        source_file_name=path.name,
        source_file_sha256=file_sha,
        inactive_report=inactive,
        unmatched_report=unmatched,
    )
    result = write_new_baseline_version(doc, activate=bool(args.activate))
    summary = {
        "ok": True,
        "baseline_id": result["baseline_id"],
        "path": result["path"],
        "activated": result["activated"],
        "cutoff_at": cutoff,
        "warehouse_id": YGY_WAREHOUSE_ID,
        "row_count": len(eligible),
        "eligible_count": sum(1 for r in eligible if r.get("eligible_master")),
        "inactive_count": len(inactive),
        "unmatched_count": len(unmatched),
        "source_sha256": doc["source_sha256"],
        "source_file_sha256": file_sha,
        "semantics": "FREE_STOCK_AT_CUTOFF",
        "physical_write": False,
        "note": "Outside-file masters are not zeroed; inactive reported separately",
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if unmatched:
        print(json.dumps({"unmatched_sample": unmatched[:20]}, indent=2, ensure_ascii=False))
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", required=True, help="Accurate Stok Dapat Dijual csv/xlsx")
    p.add_argument("--cutoff", required=True, help="ISO cutoff with timezone, e.g. 2026-11-04T22:30:00+07:00")
    p.add_argument("--mongo-url", default="mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    p.add_argument("--db-name", default="eternal_packaging")
    p.add_argument(
        "--activate",
        action="store_true",
        help="Point registry active to this version (still no production env mutation)",
    )
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
