#!/usr/bin/env python3
"""SOP Belum-terverifikasi enumerator — READ-ONLY production inventory.

Phase-1 helper for Owner VPS. Lists every Stock Opname line with
BELUM_TERVERIFIKASI on a named session (default SOP-20261031-001), then
optionally classifies known residual drivers (B32 qty-unique / C_NO_PRINT /
unknown needing order drill-down).

Auth (local VPS only — never paste secrets into chat/CI):
  ~/.config/eternal/b31-live-ro.env   (same file as B31 validator)
    ETERNAL_B31_API_BASE=http://127.0.0.1:<backend-port>
    ETERNAL_B31_EMAIL=...
    ETERNAL_B31_PASSWORD=...

Exit 0 when inventory completes (even if unverified>0).
Exit 1 config/auth error.
Exit 2 session missing / schema incompatible / API fail-closed.

PATCH AUTHORIZED: NO
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from b31_live_validator import (  # noqa: E402
    DEFAULT_RO_ENV,
    FORBIDDEN_ENV_KEYS,
    _die,
    _http_json,
    _load_env_file,
    _parse_backend_api_base_optional,
)

# Residual after B31 (RO21 Class-C). B32 upgrades qty-unique multi-print rows.
KNOWN_B32_ORDER_IDS = {
    "2de4d6b5-2470-4497-9847-61079b1e2cc1",  # PO/2026/09/07/000028 → 100031|100051
    "0089e1db-4cee-484d-8270-b0c485065b39",  # PO/26/10/2026/000014 → 100147|100388
    "785444ca-9779-4b4b-8a53-501f4e41ff7e",  # PO/2026/09/14/000020 → 100147|100388
}
KNOWN_C_NO_PRINT_ORDER_IDS = {
    "bdd08fd1-820b-4016-867d-fcc077ce96ab",  # PO/2026/09/24/000006
    "b2988cad-9297-4644-8456-d984136c2cd3",  # PO/03/10/2026/000017
}
UNVERIFIED_STATUSES = frozenset({"BELUM_TERVERIFIKASI", "UNVERIFIED", "CUTOVER_PENDING"})
REQUIRED_LINE_KEYS = ("item_code", "master_item_id", "belum_terkirim_status")
SKU_HINTS = {
    "100031": {
        "class": "B",
        "reason_code": "A_MISSING_SERVICE_PARENT_VIA_C_MULTI_PRINT",
        "residual_orders": ["2de4d6b5-2470-4497-9847-61079b1e2cc1"],
        "fix_batch": "B32",
        "note": "After B31, sticky completion_unproven from PO/2026/09/07/000028 (prints 100031+100051; SERVICE qty 500 → unique match 100051).",
    },
    "100051": {
        "class": "B",
        "reason_code": "A_MISSING_SERVICE_PARENT_VIA_C_MULTI_PRINT",
        "residual_orders": ["2de4d6b5-2470-4497-9847-61079b1e2cc1"],
        "fix_batch": "B32",
        "note": "Same PO as 100031; sibling blast until SERVICE linked.",
    },
    "100147": {
        "class": "B",
        "reason_code": "A_MISSING_SERVICE_PARENT_VIA_C_MULTI_PRINT",
        "residual_orders": [
            "0089e1db-4cee-484d-8270-b0c485065b39",
            "785444ca-9779-4b4b-8a53-501f4e41ff7e",
        ],
        "fix_batch": "B32",
        "note": "Two multi-print POs; qty-unique SERVICE→parent proposals in B32.",
    },
    "100388": {
        "class": "B",
        "reason_code": "A_MISSING_SERVICE_PARENT_VIA_C_MULTI_PRINT",
        "residual_orders": [
            "0089e1db-4cee-484d-8270-b0c485065b39",
            "785444ca-9779-4b4b-8a53-501f4e41ff7e",
        ],
        "fix_batch": "B32",
        "note": "Sibling of 100147 on same residual POs.",
    },
    "100004": {
        "class": "G",
        "reason_code": "NEEDS_LIVE_ORDER_DRILLDOWN",
        "residual_orders": [],
        "fix_batch": None,
        "note": (
            "Not in RO21 completion_unproven set. Historical belum32 = "
            "DELIVERED_DELTA_REVIEW (engine vs indep +1000) with unproven_event_count=0. "
            "Under R2 Opname, Belum UNVERIFIED requires completion_unproven. "
            "Enumerator --causal must list live unlinked/orphan/ambiguous POs."
        ),
    },
}


def _resolve_api_base(env: Dict[str, str]) -> str:
    """Match B31 executor: prefer RO env base, else /etc backend.port discovery."""
    api_base = (env.get("ETERNAL_B31_API_BASE") or "").strip().rstrip("/")
    if not api_base:
        api_base = (_parse_backend_api_base_optional() or "").rstrip("/")
    if not api_base:
        _die(
            "ETERNAL_B31_API_BASE required in RO env "
            "(or PORT in /etc/eternal-packaging/backend.env)",
            1,
        )
    if not (api_base.startswith("http://") or api_base.startswith("https://")):
        _die(f"ETERNAL_B31_API_BASE must be http(s) URL, got={api_base[:40]!r}", 1)
    return api_base


def _login(base: str, email: str, password: str) -> str:
    # body= must be keyword — third positional arg is token in _http_json.
    code, body = _http_json(
        "POST",
        f"{base}/api/auth/login",
        token=None,
        body={"email": email, "password": password},
    )
    if code != 200:
        _die(f"login HTTP {code}", 1)
    if not isinstance(body, dict):
        _die("login response not object", 1)
    token = str(body.get("access_token") or body.get("token") or "").strip()
    if not token:
        _die("login missing access_token", 1)
    return token


def _unwrap_order(body: Any) -> dict:
    if isinstance(body, dict) and isinstance(body.get("order"), dict):
        return body["order"]
    if isinstance(body, dict):
        return body
    return {}


def _classify_sku(code: str) -> dict:
    hint = SKU_HINTS.get(str(code))
    if hint:
        return dict(hint)
    return {
        "class": "G",
        "reason_code": "UNKNOWN_PENDING_CAUSAL",
        "residual_orders": [],
        "fix_batch": None,
        "note": "Not in post-B31 residual map — run --causal against live orders.",
    }


def validate_session_schema(
    session_doc: Any,
    *,
    expect_lines: Optional[int] = None,
    expect_session: Optional[str] = None,
) -> Tuple[dict, List[dict]]:
    """Fail closed if response shape cannot support Belum inventory."""
    if not isinstance(session_doc, dict):
        raise ValueError("SESSION_NOT_OBJECT")
    # Reject nested wrappers that would make lines look empty.
    if "data" in session_doc and "lines" not in session_doc:
        raise ValueError("SESSION_WRAPPED_UNSUPPORTED")
    lines_raw = session_doc.get("lines")
    if lines_raw is None:
        raise ValueError("SESSION_MISSING_LINES")
    if not isinstance(lines_raw, list):
        raise ValueError("SESSION_LINES_NOT_LIST")
    if len(lines_raw) == 0:
        raise ValueError("SESSION_LINES_EMPTY")

    item_count = session_doc.get("item_count")
    if item_count is not None:
        try:
            ic = int(item_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("SESSION_ITEM_COUNT_INVALID") from exc
        if ic != len(lines_raw):
            raise ValueError(
                f"SESSION_ITEM_COUNT_MISMATCH item_count={ic} lines={len(lines_raw)}"
            )

    if expect_lines is not None and len(lines_raw) != int(expect_lines):
        raise ValueError(
            f"SESSION_LINE_COUNT_UNEXPECTED got={len(lines_raw)} expect={expect_lines}"
        )

    if expect_session:
        got_num = str(session_doc.get("opname_number") or "").strip()
        got_id = str(session_doc.get("id") or "").strip()
        want = str(expect_session).strip()
        if want not in (got_num, got_id):
            raise ValueError(
                f"SESSION_IDENTITY_MISMATCH want={want} "
                f"opname_number={got_num or '-'} id={got_id[:8] or '-'}"
            )

    # Require identity fields present on the public session document.
    if not str(session_doc.get("opname_number") or session_doc.get("id") or "").strip():
        raise ValueError("SESSION_MISSING_IDENTITY")

    lines: List[dict] = []
    missing_status = 0
    missing_keys = 0
    for i, raw in enumerate(lines_raw):
        if not isinstance(raw, dict):
            raise ValueError(f"SESSION_LINE_NOT_OBJECT idx={i}")
        for key in REQUIRED_LINE_KEYS:
            if key not in raw:
                missing_keys += 1
        status_present = "belum_terkirim_status" in raw
        if not status_present:
            missing_status += 1
        lines.append(raw)

    if missing_keys:
        raise ValueError(
            f"SESSION_LINE_KEYS_MISSING count={missing_keys} "
            f"required={','.join(REQUIRED_LINE_KEYS)}"
        )
    if missing_status:
        # Impossible if REQUIRED_LINE_KEYS includes the field, kept explicit.
        raise ValueError(f"SESSION_BELUM_STATUS_MISSING count={missing_status}")

    return session_doc, lines


def _scan_order_gate(order: dict) -> Dict[str, Any]:
    """Compact process-gate signals for one PO (no customer dump)."""
    from propose_qty_unique_parents import (  # local helper
        _is_print,
        _is_service,
        _qty,
        propose_all_unlinked_services,
    )

    if not isinstance(order, dict) or "line_items" not in order:
        return {
            "order_id": str((order or {}).get("id") or ""),
            "error": "ORDER_MISSING_LINE_ITEMS",
        }
    lines = [li if isinstance(li, dict) else {} for li in (order.get("line_items") or [])]
    unlinked = []
    print_idxs = []
    for i, li in enumerate(lines):
        if _is_service(li):
            pid = str(li.get("parent_line_id") or "").strip()
            pidx = li.get("parent_line_idx")
            if not pid and pidx is None:
                unlinked.append({
                    "service_line_idx": i,
                    "qty": _qty(li.get("quantity")),
                    "completed": _qty(li.get("qty_completed") or li.get("completed_qty")),
                    "master_item_id": str(li.get("master_item_id") or "")[:8],
                })
        elif _is_print(li) and not _is_service(li):
            print_idxs.append({
                "idx": i,
                "item_code": str(li.get("item_code") or ""),
                "qty": _qty(li.get("quantity")),
                "master_item_id": str(li.get("master_item_id") or "")[:8],
            })
    proposals = propose_all_unlinked_services(lines)
    oid = str(order.get("id") or "")
    return {
        "order_id": oid,
        "po_number": str(order.get("po_number") or order.get("order_number") or ""),
        "n_unlinked_service": len(unlinked),
        "n_print_parents": len(print_idxs),
        "unlinked_services": unlinked,
        "print_parents": print_idxs,
        "qty_unique_proposals": proposals,
        "class_hint": (
            "B32_CANDIDATE"
            if proposals and oid in KNOWN_B32_ORDER_IDS
            else (
                "C_NO_PRINT_HOLD"
                if oid in KNOWN_C_NO_PRINT_ORDER_IDS
                else (
                    "QTY_UNIQUE_PROPOSABLE"
                    if proposals
                    else ("UNLINKED_NO_UNIQUE_QTY" if unlinked else "NO_UNLINKED_SERVICE")
                )
            )
        ),
    }


def inventory_unverified(lines: List[dict]) -> List[dict]:
    unverified: List[dict] = []
    for line in lines:
        status = str(line.get("belum_terkirim_status") or "").strip().upper()
        if status not in UNVERIFIED_STATUSES:
            continue
        code = str(line.get("item_code") or "")
        classif = _classify_sku(code)
        unverified.append({
            "item_code": code,
            "master_item_id": str(line.get("master_item_id") or ""),
            "qty_pesanan": line.get("qty_pesanan"),
            "qty_belum_terkirim": line.get("qty_belum_terkirim"),
            "belum_terkirim_status": status,
            "classification": classif["class"],
            "reason_code": classif["reason_code"],
            "residual_order_ids": classif["residual_orders"],
            "fix_batch": classif["fix_batch"],
            "note": classif["note"],
        })
    return unverified


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SOP Belum-terverifikasi RO enumerator")
    ap.add_argument("--session", default="SOP-20261031-001", help="opname_number or session id")
    ap.add_argument(
        "--expect-lines",
        type=int,
        default=None,
        help="Fail closed unless session.lines length equals this (use 401 for SOP-20261031-001)",
    )
    ap.add_argument(
        "--ro-env",
        type=Path,
        default=Path(os.environ.get("ETERNAL_B31_RO_ENV") or DEFAULT_RO_ENV),
    )
    ap.add_argument(
        "--causal-order-ids",
        type=Path,
        default=None,
        help="Optional JSON list of order_ids to GET for process-gate classification",
    )
    ap.add_argument(
        "--include-known-residual-orders",
        action="store_true",
        help="Also GET B32 + C_NO_PRINT residual order_ids",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write full JSON inventory (0600) to this path",
    )
    ap.add_argument(
        "--fixture-session",
        type=Path,
        default=None,
        help="Offline: load session JSON instead of API",
    )
    args = ap.parse_args(argv)

    token = ""
    base = ""
    if args.fixture_session:
        try:
            session_raw = json.loads(args.fixture_session.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"SOP_ENUM FAIL fixture_read={exc}", file=sys.stderr)
            return 2
    else:
        env = _load_env_file(args.ro_env)
        for k in FORBIDDEN_ENV_KEYS:
            if k in env:
                _die(f"forbidden key in RO env: {k}", 1)
        for k in env:
            if "MONGO" in k.upper():
                _die(f"RO env forbids Mongo key {k}", 1)
        base = _resolve_api_base(env)
        email = (env.get("ETERNAL_B31_EMAIL") or "").strip()
        password = env.get("ETERNAL_B31_PASSWORD") or ""
        if not email or not password:
            _die("ETERNAL_B31_EMAIL/PASSWORD required", 1)
        token = _login(base, email, password)
        # Production route: GET /api/stock-opname-sessions/{session_id|opname_number}
        code, session_raw = _http_json(
            "GET",
            f"{base}/api/stock-opname-sessions/{args.session}",
            token=token,
        )
        if code == 404:
            print(f"SOP_ENUM FAIL session_not_found={args.session}", file=sys.stderr)
            return 2
        if code != 200:
            print(f"SOP_ENUM FAIL session_http={code}", file=sys.stderr)
            return 2

    try:
        session_doc, lines = validate_session_schema(
            session_raw,
            expect_lines=args.expect_lines,
            expect_session=args.session if not args.fixture_session else None,
        )
    except ValueError as exc:
        print(f"SOP_ENUM FAIL schema={exc}", file=sys.stderr)
        return 2

    total = len(lines)
    unverified = inventory_unverified(lines)

    causal_ids: List[str] = []
    if args.include_known_residual_orders:
        causal_ids.extend(sorted(KNOWN_B32_ORDER_IDS | KNOWN_C_NO_PRINT_ORDER_IDS))
    if args.causal_order_ids and args.causal_order_ids.is_file():
        extra = json.loads(args.causal_order_ids.read_text(encoding="utf-8"))
        if isinstance(extra, list):
            causal_ids.extend(str(x) for x in extra)
    for row in unverified:
        causal_ids.extend(row.get("residual_order_ids") or [])
    causal_ids = sorted(set(x for x in causal_ids if x))

    causal_reports: List[dict] = []
    causal_errors = 0
    if causal_ids and not args.fixture_session:
        for oid in causal_ids:
            code, body = _http_json("GET", f"{base}/api/orders/{oid}", token=token)
            if code != 200:
                causal_reports.append({"order_id": oid, "error": f"HTTP_{code}"})
                causal_errors += 1
                continue
            order = _unwrap_order(body)
            report = _scan_order_gate(order)
            if report.get("error"):
                causal_errors += 1
            causal_reports.append(report)
    elif causal_ids and args.fixture_session:
        root = args.fixture_session.parent
        for oid in causal_ids:
            fp = root / f"{oid}.json"
            if not fp.is_file():
                causal_reports.append({"order_id": oid, "error": "FIXTURE_MISSING"})
                causal_errors += 1
                continue
            report = _scan_order_gate(json.loads(fp.read_text(encoding="utf-8")))
            if report.get("error"):
                causal_errors += 1
            causal_reports.append(report)

    # Fail closed if Owner asked for residual causal and any fetch failed.
    if args.include_known_residual_orders and causal_errors:
        print(
            f"SOP_ENUM FAIL causal_errors={causal_errors}/{len(causal_ids)} "
            f"(fail closed when --include-known-residual-orders)",
            file=sys.stderr,
        )
        # Still print partial inventory for debugging, then exit 2.
        print(
            f"SOP_ENUM PARTIAL session={session_doc.get('opname_number') or session_doc.get('id')} "
            f"examined={total} unverified={len(unverified)} causal_orders={len(causal_reports)}"
        )
        return 2

    sku100004 = next((r for r in unverified if r["item_code"] == "100004"), None)
    summary = {
        "session": str(session_doc.get("opname_number") or session_doc.get("id") or args.session),
        "warehouse_id": session_doc.get("warehouse_id"),
        "branch_id": session_doc.get("branch_id"),
        "mode": session_doc.get("mode"),
        "status": session_doc.get("status"),
        "total_skus_examined": total,
        "unverified_sku_count": len(unverified),
        "unverified": unverified,
        "sku_100004": sku100004,
        "c_no_print_hold_order_ids": sorted(KNOWN_C_NO_PRINT_ORDER_IDS),
        "causal_orders_scanned": len(causal_reports),
        "causal": causal_reports,
        "endpoint": "/api/stock-opname-sessions/{opname_number|id}",
        "write_authorized": False,
    }

    print(
        f"SOP_ENUM session={summary['session']} examined={total} "
        f"unverified={len(unverified)} causal_orders={len(causal_reports)}"
    )
    for row in unverified:
        print(
            f"  SKU={row['item_code']} class={row['classification']} "
            f"reason={row['reason_code']} fix={row['fix_batch'] or '-'} "
            f"orders={len(row['residual_order_ids'])}"
        )
    if sku100004:
        print(
            f"  SKU_100004_SEPARATE class=G reason={sku100004['reason_code']} "
            f"(live causal required; do not fold into B32)"
        )
    else:
        print("  SKU_100004_SEPARATE not_in_unverified_set")
    print(
        f"  C_NO_PRINT_HOLD orders={len(KNOWN_C_NO_PRINT_ORDER_IDS)} "
        f"(no B32; Owner business decision)"
    )
    for c in causal_reports:
        if c.get("error"):
            print(f"  CAUSAL oid={str(c.get('order_id') or '')[:8]}… ERR={c['error']}")
        else:
            print(
                f"  CAUSAL po={c.get('po_number')} unlinked={c['n_unlinked_service']} "
                f"prints={c['n_print_parents']} hint={c['class_hint']} "
                f"proposals={len(c.get('qty_unique_proposals') or [])}"
            )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(args.out, 0o600)
        except OSError:
            pass
        print(f"SOP_ENUM wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
