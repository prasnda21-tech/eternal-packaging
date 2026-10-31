#!/usr/bin/env python3
"""B31 LIVE VALIDATOR — read-only production preflight (no PATCH).

Validates the exact PR #697 allowlist (24 PO / 31 SERVICE→parent links) against
live PO documents via authenticated ERP API (GET /api/orders/{id}).

Authentication (Owner VPS local only — never paste secrets into chat/CI):
  File: ~/.config/eternal/b31-live-ro.env
    ETERNAL_B31_API_BASE=http://127.0.0.1:<backend-port>
    ETERNAL_B31_EMAIL=<erp-user-with-po.view>
    ETERNAL_B31_PASSWORD=<password>

Refuses:
  - allowlist SHA mismatch
  - any Mongo URI / app RW credential reuse
  - missing/partial coverage
  - parent already linked when empty-parent expected
  - index / master / print / qty mismatches

Prints compact counts + mismatch reason codes only (no customer/PO dumps).
Exit 0 = all 31 PASS. Exit 2 = FAIL closed. Exit 1 = usage/config error.

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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EXPECTED_ALLOWLIST_SHA256 = (
    "e9d5de1c46ecd4121fa97d72e80539d8fb84eb0feaef758fedd809dbabd28417"
)
EXPECTED_ORDERS = 24
EXPECTED_LINKS = 31
PRODUCTION_SHA = "baa24a13393eedf06aea0ddf2f0793d3956955f9"
DEFAULT_RO_ENV = Path.home() / ".config" / "eternal" / "b31-live-ro.env"
FORBIDDEN_ENV_KEYS = {
    "MONGO_URL",
    "STANDALONE_MONGO_URL",
    "JWT_SECRET",
    "ACCURATE_API_TOKEN",
    "ACCURATE_API_SIGNATURE_SECRET",
    "ETERNAL_ACTIONS_TOKEN",
    "ETERNAL_DEPLOY_SHARED_SECRET",
}


def _die(msg: str, code: int = 1) -> None:
    print(f"B31_LIVE_VALIDATOR FAIL config: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_env_file(path: Path) -> Dict[str, str]:
    if not path.is_file():
        _die(f"RO env missing: {path} (create locally on VPS; do not paste secrets)")
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        out[k] = v
    return out


def _parse_backend_api_base_optional() -> Optional[str]:
    """Optional non-secret port discovery from backend.env (never reads MONGO/JWT)."""
    path = Path("/etc/eternal-packaging/backend.env")
    if not path.is_file():
        return None
    allowed = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k in FORBIDDEN_ENV_KEYS or "MONGO" in k.upper() or "SECRET" in k.upper() or "PASS" in k.upper() or "TOKEN" in k.upper() or "JWT" in k.upper():
            continue
        if k in ("PORT", "BACKEND_PORT", "APP_PORT", "UVICORN_PORT", "HOST", "BACKEND_HOST"):
            allowed[k] = v.strip().strip("'").strip('"')
    port = allowed.get("BACKEND_PORT") or allowed.get("UVICORN_PORT") or allowed.get("APP_PORT") or allowed.get("PORT")
    host = allowed.get("BACKEND_HOST") or allowed.get("HOST") or "127.0.0.1"
    if not port:
        return None
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def _http_json(method: str, url: str, token: Optional[str] = None, body: Optional[dict] = None) -> Tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = {"detail": raw[:200]}
        return e.code, payload


def _qty(v: Any) -> float:
    try:
        if v is None or str(v).strip() == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _empty_parent(line: dict) -> bool:
    pid = str(line.get("parent_line_id") or "").strip()
    raw_idx = line.get("parent_line_idx")
    idx_empty = raw_idx is None or str(raw_idx).strip() == ""
    return (not pid) and idx_empty


def _is_print_line(line: dict) -> bool:
    proc = str(line.get("production_process") or "").strip().lower()
    return proc == "print"


def _is_service_line(line: dict) -> bool:
    return str(line.get("item_type") or "").strip().upper() == "SERVICE"


def _oid8(order_id: str) -> str:
    return (order_id or "")[:8]


def _parse_mid_from_proposed_parent_id(text: str) -> str:
    m = re.search(r"mid=([0-9a-fA-F-]{36})", text or "")
    return m.group(1) if m else ""


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
        _die(f"allowlist order_count broken ids={len(ids)} orders={len(orders)}")
    if int(plan.get("service_link_count") or 0) != EXPECTED_LINKS:
        _die("service_link_count != 31")
    if set(ids) != {o["order_id"] for o in orders}:
        _die("document_ids_allowlisted != orders[].order_id")

    table_rows = list(csv.DictReader(table_path.open(encoding="utf-8"), delimiter="\t"))
    if len(table_rows) != EXPECTED_LINKS:
        _die(f"approval table rows={len(table_rows)} != 31")

    # Join plan before-checks with table parent SKU/mid.
    by_key: Dict[Tuple[str, int], dict] = {}
    for row in table_rows:
        oid = row["order_id"]
        # service_id format …#idx=N
        m = re.search(r"#idx=(\d+)$", row["service_id"])
        if not m:
            _die(f"bad service_id in table for {_oid8(oid)}")
        sidx = int(m.group(1))
        by_key[(oid, sidx)] = row

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
                _die(f"missing before_value_checks for {_oid8(oid)} idx={sidx}")
            trow = by_key.get((oid, sidx))
            if not trow:
                _die(f"approval table missing {_oid8(oid)} idx={sidx}")
            if int(trow["proposed_parent_line_idx"]) != pidx:
                _die(f"parent idx mismatch plan vs table {_oid8(oid)}")
            parent_mid = _parse_mid_from_proposed_parent_id(trow["proposed_parent_id"])
            if not parent_mid:
                _die(f"parent mid missing in table {_oid8(oid)}")
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
        _die(f"joined links={len(links)} != 31")
    # Class-C must not appear
    c_hold = set(plan.get("class_c_hold_order_ids") or [])
    if c_hold & set(ids):
        _die("class-C order leaked into allowlist")
    return plan, links


def validate_link(order: dict, exp: dict) -> List[str]:
    reasons: List[str] = []
    oid = str(order.get("id") or "").strip()
    if oid != exp["order_id"]:
        reasons.append("ORDER_ID_MISMATCH")
        return reasons
    lines = list(order.get("line_items") or [])
    sidx = exp["service_line_idx"]
    pidx = exp["parent_line_idx"]
    if sidx < 0 or sidx >= len(lines):
        reasons.append("SERVICE_IDX_OOB")
        return reasons
    if pidx < 0 or pidx >= len(lines):
        reasons.append("PARENT_IDX_OOB")
        return reasons
    svc = lines[sidx] if isinstance(lines[sidx], dict) else {}
    parent = lines[pidx] if isinstance(lines[pidx], dict) else {}

    smid = str(svc.get("master_item_id") or "").strip()
    if smid != exp["service_master_id"]:
        reasons.append("SERVICE_MASTER_MISMATCH")
    if not _is_service_line(svc):
        reasons.append("SERVICE_ITEM_TYPE")
    if not _is_print_line(svc):
        reasons.append("SERVICE_NOT_PRINT")
    if exp["require_parent_empty"] and not _empty_parent(svc):
        reasons.append("PARENT_NOT_EMPTY")

    pmid = str(parent.get("master_item_id") or "").strip()
    if pmid != exp["parent_master_id"]:
        reasons.append("PARENT_MASTER_MISMATCH")
    if not _is_print_line(parent):
        reasons.append("PARENT_NOT_PRINT")
    if _is_service_line(parent):
        reasons.append("PARENT_IS_SERVICE")

    # Quantity: process Add On ordered qty should match physical print parent
    if abs(_qty(svc.get("quantity")) - _qty(parent.get("quantity"))) > 1e-9:
        reasons.append("QTY_MISMATCH")

    # Two-color safety: SERVICE must not equal parent idx
    if sidx == pidx:
        reasons.append("SELF_PARENT")
    return reasons


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="B31 live read-only validator")
    ap.add_argument("--allowlist", type=Path, required=True, help="REPAIR_PLAN_DRYRUN.json")
    ap.add_argument("--table", type=Path, required=True, help="APPROVAL_TABLE_B31.tsv")
    ap.add_argument(
        "--ro-env",
        type=Path,
        default=Path(os.environ.get("ETERNAL_B31_RO_ENV") or DEFAULT_RO_ENV),
        help="Local RO API credentials file (never commit)",
    )
    ap.add_argument(
        "--fixture-dir",
        type=Path,
        default=None,
        help="Offline test: load orders as {order_id}.json instead of API",
    )
    args = ap.parse_args(argv)

    print("B31_LIVE_VALIDATOR")
    print(f"production_sha_required={PRODUCTION_SHA}")
    print("patch_authorized=NO")

    digest = _sha256_file(args.allowlist)
    match = digest == EXPECTED_ALLOWLIST_SHA256
    print(f"allowlist_sha256={digest} MATCH={'YES' if match else 'NO'}")
    if not match:
        return 2

    try:
        plan, links = load_expectations(args.allowlist, args.table)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1

    print(f"orders_expected={EXPECTED_ORDERS} links_expected={EXPECTED_LINKS}")

    token: Optional[str] = None
    api_base = ""
    if args.fixture_dir:
        fixture_dir = args.fixture_dir
        if not fixture_dir.is_dir():
            _die(f"fixture-dir missing: {fixture_dir}")
    else:
        env = _load_env_file(args.ro_env)
        for bad in FORBIDDEN_ENV_KEYS:
            if bad in env:
                _die(f"RO env must not contain {bad} (no app RW credential reuse)")
        for k in env:
            if "MONGO" in k.upper():
                _die(f"RO env forbids Mongo key {k}")
        api_base = (env.get("ETERNAL_B31_API_BASE") or "").rstrip("/")
        if not api_base:
            api_base = (_parse_backend_api_base_optional() or "").rstrip("/")
        if not api_base:
            _die("ETERNAL_B31_API_BASE required in RO env (or discoverable PORT in backend.env)")
        email = (env.get("ETERNAL_B31_EMAIL") or "").strip()
        password = env.get("ETERNAL_B31_PASSWORD") or ""
        if not email or not password:
            _die("ETERNAL_B31_EMAIL and ETERNAL_B31_PASSWORD required in RO env")
        code, login_body = _http_json(
            "POST",
            f"{api_base}/api/auth/login",
            body={"email": email, "password": password},
        )
        if code != 200 or not isinstance(login_body, dict) or not login_body.get("access_token"):
            print(f"auth_login=FAIL http={code}")
            return 2
        token = str(login_body["access_token"])
        # Confirm identity without printing email
        me_code, me_body = _http_json("GET", f"{api_base}/api/auth/me", token=token)
        if me_code != 200:
            print(f"auth_me=FAIL http={me_code}")
            return 2
        perms = set((me_body or {}).get("permissions") or [])
        role = str((me_body or {}).get("role") or "")
        if role != "admin" and "po.view" not in perms and not any(
            p in perms for p in ("work_panel.view", "delivery_note.view", "schedule.view")
        ):
            print("auth_perm=FAIL need po.view (or admin)")
            return 2
        print("auth=OK mode=api_readonly")

    # Fetch exactly allowlisted orders
    order_ids = list(plan["document_ids_allowlisted"])
    fetched: Dict[str, dict] = {}
    fetch_fail = 0
    for oid in order_ids:
        if args.fixture_dir:
            fpath = args.fixture_dir / f"{oid}.json"
            if not fpath.is_file():
                fetch_fail += 1
                print(f"mismatch order={_oid8(oid)} reason=ORDER_MISSING")
                continue
            fetched[oid] = json.loads(fpath.read_text(encoding="utf-8"))
        else:
            code, body = _http_json("GET", f"{api_base}/api/orders/{oid}", token=token)
            if code != 200 or not isinstance(body, dict):
                fetch_fail += 1
                print(f"mismatch order={_oid8(oid)} reason=ORDER_FETCH_HTTP_{code}")
                continue
            # Some APIs wrap {order: {...}}
            order = body.get("order") if isinstance(body.get("order"), dict) else body
            fetched[oid] = order

    print(f"orders_fetched={len(fetched)} orders_missing={EXPECTED_ORDERS - len(fetched)}")
    if len(fetched) != EXPECTED_ORDERS or fetch_fail:
        print("RESULT=FAIL incomplete_order_coverage")
        return 2

    ok = 0
    bad = 0
    multi_color_orders = 0
    # Count two-SERVICE orders in expectations
    from collections import Counter
    oc = Counter(x["order_id"] for x in links)
    multi_color_orders = sum(1 for _, n in oc.items() if n >= 2)

    for exp in links:
        order = fetched[exp["order_id"]]
        reasons = validate_link(order, exp)
        if reasons:
            bad += 1
            print(
                f"mismatch order={_oid8(exp['order_id'])} "
                f"svc_idx={exp['service_line_idx']} "
                f"parent_idx={exp['parent_line_idx']} "
                f"reason={','.join(reasons)}"
            )
        else:
            ok += 1

    print(f"links_ok={ok} links_mismatch={bad} multi_service_orders={multi_color_orders}")
    if ok != EXPECTED_LINKS or bad != 0:
        print("RESULT=FAIL")
        return 2
    print("RESULT=PASS")
    print("PATCH AUTHORIZED: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
