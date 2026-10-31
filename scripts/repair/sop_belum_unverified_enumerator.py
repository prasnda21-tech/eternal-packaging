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

Exit 0 always when inventory completes (even if unverified>0).
Exit 1 config/auth error. Exit 2 session missing / API fail-closed.

PATCH AUTHORIZED: NO
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _login(base: str, email: str, password: str) -> str:
    code, body = _http_json(
        "POST",
        f"{base}/api/auth/login",
        {"email": email, "password": password},
        token=None,
    )
    if code != 200:
        _die(f"login HTTP {code}", 1)
    token = str((body or {}).get("access_token") or (body or {}).get("token") or "").strip()
    if not token:
        _die("login missing token", 1)
    return token


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


def _scan_order_gate(order: dict) -> Dict[str, Any]:
    """Compact process-gate signals for one PO (no customer dump)."""
    from propose_qty_unique_parents import (  # local helper
        _is_print,
        _is_service,
        _qty,
        propose_all_unlinked_services,
    )

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
    return {
        "order_id": str(order.get("id") or ""),
        "po_number": str(order.get("po_number") or order.get("order_number") or ""),
        "n_unlinked_service": len(unlinked),
        "n_print_parents": len(print_idxs),
        "unlinked_services": unlinked,
        "print_parents": print_idxs,
        "qty_unique_proposals": proposals,
        "class_hint": (
            "B32_CANDIDATE"
            if proposals and str(order.get("id")) in KNOWN_B32_ORDER_IDS
            else (
                "C_NO_PRINT_HOLD"
                if str(order.get("id")) in KNOWN_C_NO_PRINT_ORDER_IDS
                else ("QTY_UNIQUE_PROPOSABLE" if proposals else ("UNLINKED_NO_UNIQUE_QTY" if unlinked else "NO_UNLINKED_SERVICE"))
            )
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SOP Belum-terverifikasi RO enumerator")
    ap.add_argument("--session", default="SOP-20261031-001", help="opname_number or session id")
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

    session_doc: dict
    token = ""
    base = ""
    if args.fixture_session:
        session_doc = json.loads(args.fixture_session.read_text(encoding="utf-8"))
    else:
        env = _load_env_file(args.ro_env)
        for k in FORBIDDEN_ENV_KEYS:
            if k in env:
                _die(f"forbidden key in RO env: {k}", 1)
        base = _parse_backend_api_base_optional(env.get("ETERNAL_B31_API_BASE") or "")
        if not base:
            _die("ETERNAL_B31_API_BASE missing/invalid", 1)
        email = (env.get("ETERNAL_B31_EMAIL") or "").strip()
        password = (env.get("ETERNAL_B31_PASSWORD") or "").strip()
        if not email or not password:
            _die("ETERNAL_B31_EMAIL/PASSWORD required", 1)
        token = _login(base, email, password)
        code, session_doc = _http_json(
            "GET", f"{base}/api/stock-opname-sessions/{args.session}", token=token,
        )
        if code == 404:
            print(f"SOP_ENUM FAIL session_not_found={args.session}", file=sys.stderr)
            return 2
        if code != 200 or not isinstance(session_doc, dict):
            print(f"SOP_ENUM FAIL session_http={code}", file=sys.stderr)
            return 2

    lines = list(session_doc.get("lines") or [])
    total = len(lines)
    unverified: List[dict] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        status = str(line.get("belum_terkirim_status") or "").strip().upper()
        # Strict: only explicit unverified statuses (never invent from null alone).
        if status not in {"BELUM_TERVERIFIKASI", "UNVERIFIED", "CUTOVER_PENDING"}:
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

    causal_ids: List[str] = []
    if args.include_known_residual_orders:
        causal_ids.extend(sorted(KNOWN_B32_ORDER_IDS | KNOWN_C_NO_PRINT_ORDER_IDS))
    if args.causal_order_ids and args.causal_order_ids.is_file():
        extra = json.loads(args.causal_order_ids.read_text(encoding="utf-8"))
        if isinstance(extra, list):
            causal_ids.extend(str(x) for x in extra)
    # Also pull residual ids declared on unverified SKUs
    for row in unverified:
        causal_ids.extend(row.get("residual_order_ids") or [])
    causal_ids = sorted(set(x for x in causal_ids if x))

    causal_reports: List[dict] = []
    if causal_ids and not args.fixture_session:
        for oid in causal_ids:
            code, order = _http_json("GET", f"{base}/api/orders/{oid}", token=token)
            if code != 200 or not isinstance(order, dict):
                causal_reports.append({"order_id": oid, "error": f"HTTP_{code}"})
                continue
            causal_reports.append(_scan_order_gate(order))
    elif causal_ids and args.fixture_session:
        # Offline: look for sibling fixture files {order_id}.json next to session
        root = args.fixture_session.parent
        for oid in causal_ids:
            fp = root / f"{oid}.json"
            if not fp.is_file():
                causal_reports.append({"order_id": oid, "error": "FIXTURE_MISSING"})
                continue
            causal_reports.append(_scan_order_gate(json.loads(fp.read_text(encoding="utf-8"))))

    summary = {
        "session": str(session_doc.get("opname_number") or session_doc.get("id") or args.session),
        "warehouse_id": session_doc.get("warehouse_id"),
        "branch_id": session_doc.get("branch_id"),
        "mode": session_doc.get("mode"),
        "status": session_doc.get("status"),
        "total_skus_examined": total,
        "unverified_sku_count": len(unverified),
        "unverified": unverified,
        "causal_orders_scanned": len(causal_reports),
        "causal": causal_reports,
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
    for c in causal_reports:
        if c.get("error"):
            print(f"  CAUSAL oid={c['order_id'][:8]}… ERR={c['error']}")
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
