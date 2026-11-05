#!/usr/bin/env python3
"""B32 LIVE VALIDATOR — read-only preflight for qty-unique Class-C upgrade.

Allowlist: docs/repair/REPAIR_PLAN_B32_QTY_UNIQUE.json (3 PO / 5 links).
Rule: among is_print parents on the PO, SERVICE.quantity uniquely matches one
parent.quantity. Still requires Owner approval before any PATCH.

Auth: ~/.config/eternal/b31-live-ro.env (same as B31).
PATCH AUTHORIZED: NO
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from b31_live_validator import (  # noqa: E402
    DEFAULT_RO_ENV,
    FORBIDDEN_ENV_KEYS,
    PRODUCTION_SHA,
    _die,
    _empty_parent,
    _http_json,
    _is_print_line,
    _is_service_line,
    _load_env_file,
    _oid8,
    _parse_backend_api_base_optional,
    _parse_mid_from_proposed_parent_id,
    _qty,
    _sha256_file,
)
from propose_qty_unique_parents import propose_unique_qty_parent  # noqa: E402

EXPECTED_ALLOWLIST_SHA256 = (
    "47b9f53ab197859d9886b03cc2ce4e33550d0c9a5b57807fd62d335249393114"
)
EXPECTED_ORDERS = 3
EXPECTED_LINKS = 5


def load_expectations(allowlist_path: Path, table_path: Path) -> Tuple[dict, List[dict]]:
    digest = _sha256_file(allowlist_path)
    if digest != EXPECTED_ALLOWLIST_SHA256:
        print(f"allowlist_sha256={digest} MATCH=NO expected={EXPECTED_ALLOWLIST_SHA256}")
        raise SystemExit(2)
    plan = json.loads(allowlist_path.read_text(encoding="utf-8"))
    if plan.get("write_authorized") is not False:
        _die("allowlist write_authorized must be false")
    if str(plan.get("production_sha_required") or "") != PRODUCTION_SHA:
        _die("production_sha_required mismatch")
    ids = list(plan.get("document_ids_allowlisted") or [])
    orders = list(plan.get("orders") or [])
    if len(ids) != EXPECTED_ORDERS or len(orders) != EXPECTED_ORDERS:
        _die(f"order_count broken ids={len(ids)} orders={len(orders)}")
    if int(plan.get("service_link_count") or 0) != EXPECTED_LINKS:
        _die("service_link_count != 5")
    if set(ids) != {o["order_id"] for o in orders}:
        _die("document_ids_allowlisted != orders[].order_id")
    # Must not include C_NO_PRINT holds
    hold = set(plan.get("class_c_still_hold_order_ids") or [])
    if hold & set(ids):
        _die("C_NO_PRINT hold leaked into B32 allowlist")

    table_rows = list(csv.DictReader(table_path.open(encoding="utf-8"), delimiter="\t"))
    if len(table_rows) != EXPECTED_LINKS:
        _die(f"approval table rows={len(table_rows)} != 5")
    by_key: Dict[Tuple[str, int], dict] = {}
    for row in table_rows:
        m = re.search(r"#idx=(\d+)$", row["service_id"])
        if not m:
            _die(f"bad service_id {_oid8(row['order_id'])}")
        by_key[(row["order_id"], int(m.group(1)))] = row

    links: List[dict] = []
    for o in orders:
        oid = o["order_id"]
        for it in o["dry_run_payload"]["items"]:
            sidx = int(it["line_idx"])
            pidx = int(it["parent_line_idx"])
            before = next(
                (b for b in o["before_value_checks"] if int(b["line_idx"]) == sidx),
                None,
            )
            if not before:
                _die(f"missing before_value_checks {_oid8(oid)} idx={sidx}")
            trow = by_key.get((oid, sidx))
            if not trow:
                _die(f"approval table missing {_oid8(oid)} idx={sidx}")
            if int(trow["proposed_parent_line_idx"]) != pidx:
                _die(f"parent idx mismatch {_oid8(oid)}")
            parent_mid = _parse_mid_from_proposed_parent_id(trow["proposed_parent_id"])
            if not parent_mid:
                _die(f"parent mid missing {_oid8(oid)}")
            links.append({
                "order_id": oid,
                "po_number": o.get("po_number") or trow.get("po_number"),
                "service_line_idx": sidx,
                "parent_line_idx": pidx,
                "service_master_id": before["require_service_master_id"],
                "parent_master_id": parent_mid,
                "parent_item_code": trow["proposed_parent_item_code"],
                "require_parent_empty": True,
            })
    if len(links) != EXPECTED_LINKS:
        _die(f"joined links={len(links)} != 5")
    return plan, links


def validate_link(order: dict, exp: dict) -> List[str]:
    reasons: List[str] = []
    lines = list(order.get("line_items") or [])
    sidx = exp["service_line_idx"]
    pidx = exp["parent_line_idx"]
    if sidx < 0 or sidx >= len(lines) or pidx < 0 or pidx >= len(lines):
        return ["IDX_OOB"]
    svc = lines[sidx] if isinstance(lines[sidx], dict) else {}
    parent = lines[pidx] if isinstance(lines[pidx], dict) else {}

    if str(svc.get("master_item_id") or "") != exp["service_master_id"]:
        reasons.append("SERVICE_MASTER_MISMATCH")
    if not _is_service_line(svc):
        reasons.append("SERVICE_ITEM_TYPE")
    if not _is_print_line(svc):
        reasons.append("SERVICE_NOT_PRINT")
    if exp["require_parent_empty"] and not _empty_parent(svc):
        reasons.append("PARENT_NOT_EMPTY")
    if str(parent.get("master_item_id") or "") != exp["parent_master_id"]:
        reasons.append("PARENT_MASTER_MISMATCH")
    if not _is_print_line(parent):
        reasons.append("PARENT_NOT_PRINT")
    if _is_service_line(parent):
        reasons.append("PARENT_IS_SERVICE")
    if abs(_qty(svc.get("quantity")) - _qty(parent.get("quantity"))) > 1e-9:
        reasons.append("QTY_MISMATCH")
    # Deterministic unique-qty rule must still hold live
    prop = propose_unique_qty_parent(lines, sidx)
    if not prop or int(prop["parent_line_idx"]) != pidx:
        reasons.append("QTY_UNIQUE_RULE_FAIL")
    if sidx == pidx:
        reasons.append("SELF_PARENT")
    return reasons


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="B32 live read-only validator")
    ap.add_argument("--allowlist", type=Path, required=True)
    ap.add_argument("--table", type=Path, required=True)
    ap.add_argument("--ro-env", type=Path, default=Path(os.environ.get("ETERNAL_B31_RO_ENV") or DEFAULT_RO_ENV))
    ap.add_argument("--fixture-dir", type=Path, default=None)
    args = ap.parse_args(argv)

    plan, links = load_expectations(args.allowlist, args.table)
    print(
        f"B32_LIVE_VALIDATOR allowlist_ok orders={EXPECTED_ORDERS} links={EXPECTED_LINKS} "
        f"sha={EXPECTED_ALLOWLIST_SHA256[:12]}…"
    )

    token = None
    base = None
    if not args.fixture_dir:
        env = _load_env_file(args.ro_env)
        for k in FORBIDDEN_ENV_KEYS:
            if k in env:
                _die(f"forbidden key in RO env: {k}")
        base = _parse_backend_api_base_optional(env.get("ETERNAL_B31_API_BASE") or "")
        if not base:
            _die("ETERNAL_B31_API_BASE missing/invalid")
        email = (env.get("ETERNAL_B31_EMAIL") or "").strip()
        password = (env.get("ETERNAL_B31_PASSWORD") or "").strip()
        if not email or not password:
            _die("ETERNAL_B31_EMAIL/PASSWORD required")
        code, body = _http_json(
            "POST", f"{base}/api/auth/login",
            body={"email": email, "password": password},
        )
        if code != 200:
            _die(f"login HTTP {code}")
        token = str((body or {}).get("access_token") or (body or {}).get("token") or "")
        if not token:
            _die("login missing token")

    fail = 0
    for exp in links:
        oid = exp["order_id"]
        if args.fixture_dir:
            fp = args.fixture_dir / f"{oid}.json"
            if not fp.is_file():
                print(f"FAIL {_oid8(oid)} FIXTURE_MISSING")
                fail += 1
                continue
            order = json.loads(fp.read_text(encoding="utf-8"))
        else:
            code, body = _http_json("GET", f"{base}/api/orders/{oid}", token=token)
            if code != 200:
                print(f"FAIL {_oid8(oid)} HTTP_{code}")
                fail += 1
                continue
            order = body.get("order") if isinstance(body, dict) and "order" in body else body
        reasons = validate_link(order if isinstance(order, dict) else {}, exp)
        if reasons:
            print(f"FAIL {_oid8(oid)} svc={exp['service_line_idx']} → {','.join(reasons)}")
            fail += 1
        else:
            print(
                f"PASS {_oid8(oid)} svc={exp['service_line_idx']} "
                f"parent={exp['parent_item_code']}@{exp['parent_line_idx']}"
            )

    if fail:
        print(f"B32_LIVE_VALIDATOR RESULT=FAIL failed_links={fail}/{EXPECTED_LINKS}")
        return 2
    print(f"B32_LIVE_VALIDATOR RESULT=PASS links={EXPECTED_LINKS} write_authorized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
