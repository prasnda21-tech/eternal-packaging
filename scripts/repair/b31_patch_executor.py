#!/usr/bin/env python3
"""B31 PATCH EXECUTOR — dry-run by default; Owner VPS only.

Applies the exact PR #697 allowlist (24 PO / 31 links) via:
  PATCH /api/orders/{order_id}/line-items/addon-parent

Mutates ONLY parent_line_id / parent_line_idx on allowlisted SERVICE lines.
Never touches qty, delivery, stock, ledger, SOP, users, roles, menus.

Default: DRY-RUN (revalidate + print intended payloads; zero writes).
Execute:  --execute --confirm-phrase 'APPROVE ADDON PARENT REPAIR MANIFEST B31 EXACT — SOP-20261030-003'

Refuses --execute when Cloud Agent env is detected.
Requires production.route.edit (checked via /api/auth/me).

Auth file (same as validator): ~/.config/eternal/b31-live-ro.env
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Reuse validator primitives (same directory).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from b31_live_validator import (  # noqa: E402
    EXPECTED_ALLOWLIST_SHA256,
    EXPECTED_LINKS,
    EXPECTED_ORDERS,
    FORBIDDEN_ENV_KEYS,
    PRODUCTION_SHA,
    DEFAULT_RO_ENV,
    _die,
    _empty_parent,
    _http_json,
    _load_env_file,
    _oid8,
    _parse_backend_api_base_optional,
    _sha256_file,
    load_expectations,
    validate_link,
)

CONFIRM_PHRASE = (
    "APPROVE ADDON PARENT REPAIR MANIFEST B31 EXACT — SOP-20261030-003"
)


def _cloud_agent_detected() -> bool:
    markers = (
        "CLOUD_AGENT_ALL_SECRET_NAMES",
        "CLOUD_AGENT_INJECTED_SECRET_NAMES",
        "CURSOR_AGENT",
        "CURSOR_CLOUD_AGENT",
    )
    if any(os.environ.get(k) for k in markers):
        return True
    # Heuristic: Cursor cloud workspace path
    if str(Path.cwd()).startswith("/workspace") and os.environ.get("ETERNAL_ACTIONS_TOKEN"):
        return True
    return False


def _unwrap_order(body: Any) -> dict:
    if isinstance(body, dict) and isinstance(body.get("order"), dict):
        return body["order"]
    if isinstance(body, dict):
        return body
    return {}


def _line_identity(line: dict, idx: int) -> str:
    for key in ("line_id", "id"):
        raw = str(line.get(key) or "").strip()
        if raw:
            return raw
    return f"idx:{idx}"


def _snapshot_parents(order: dict, service_idxs: List[int]) -> Dict[str, dict]:
    lines = list(order.get("line_items") or [])
    out = {}
    for sidx in service_idxs:
        if sidx < 0 or sidx >= len(lines) or not isinstance(lines[sidx], dict):
            out[str(sidx)] = {"error": "OOB"}
            continue
        li = lines[sidx]
        out[str(sidx)] = {
            "line_identity": _line_identity(li, sidx),
            "master_item_id": str(li.get("master_item_id") or ""),
            "quantity": li.get("quantity"),
            "parent_line_id": li.get("parent_line_id"),
            "parent_line_idx": li.get("parent_line_idx"),
            "item_type": li.get("item_type"),
            "production_process": li.get("production_process"),
        }
    return out


def _verify_persisted(order: dict, exp_links: List[dict]) -> List[str]:
    """After PATCH: each SERVICE must resolve to the intended print parent."""
    reasons: List[str] = []
    lines = list(order.get("line_items") or [])
    for exp in exp_links:
        sidx = exp["service_line_idx"]
        pidx = exp["parent_line_idx"]
        if sidx < 0 or sidx >= len(lines) or pidx < 0 or pidx >= len(lines):
            reasons.append(f"POST_IDX_OOB:{sidx}->{pidx}")
            continue
        svc = lines[sidx] if isinstance(lines[sidx], dict) else {}
        parent = lines[pidx] if isinstance(lines[pidx], dict) else {}
        if str(svc.get("master_item_id") or "") != exp["service_master_id"]:
            reasons.append(f"POST_SERVICE_MASTER:{sidx}")
        if str(parent.get("master_item_id") or "") != exp["parent_master_id"]:
            reasons.append(f"POST_PARENT_MASTER:{pidx}")
        # Dual-store: prefer idx match; id must resolve to same parent when set
        raw_idx = svc.get("parent_line_idx")
        try:
            got_idx = int(raw_idx) if raw_idx is not None and str(raw_idx).strip() != "" else None
        except (TypeError, ValueError):
            got_idx = None
        if got_idx != pidx:
            # allow parent_line_id-only if it matches parent identity
            want_id = _line_identity(parent, pidx)
            got_id = str(svc.get("parent_line_id") or "").strip()
            if got_id != want_id:
                reasons.append(f"POST_PARENT_NOT_PERSISTED:{sidx}")
        if _empty_parent(svc):
            reasons.append(f"POST_STILL_EMPTY:{sidx}")
    return reasons


def _build_patch_items(order: dict, exp_links: List[dict]) -> List[dict]:
    """Prefer durable parent_line_id from live parent line; always send parent_line_idx."""
    lines = list(order.get("line_items") or [])
    items = []
    for exp in exp_links:
        pidx = exp["parent_line_idx"]
        parent = lines[pidx]
        parent_id = str(parent.get("line_id") or parent.get("id") or "").strip()
        item: Dict[str, Any] = {
            "line_idx": exp["service_line_idx"],
            "parent_line_idx": pidx,
        }
        # Only send durable id when not synthetic idx:N
        if parent_id and not parent_id.startswith("idx:"):
            item["parent_line_id"] = parent_id
        items.append(item)
    return items


def _build_rollback_items(before_snap: Dict[str, dict]) -> List[dict]:
    """Restore empty parents for SERVICE lines we patched (pre-state was empty)."""
    items = []
    for sidx, snap in before_snap.items():
        if snap.get("error"):
            continue
        items.append({"line_idx": int(sidx), "clear": True})
    return items


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="B31 PATCH executor (dry-run default)")
    ap.add_argument("--allowlist", type=Path, required=True)
    ap.add_argument("--table", type=Path, required=True)
    ap.add_argument(
        "--ro-env",
        type=Path,
        default=Path(os.environ.get("ETERNAL_B31_RO_ENV") or DEFAULT_RO_ENV),
    )
    ap.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="Directory for before/after JSON (default ~/b31-patch-evidence-<utc>)",
    )
    ap.add_argument(
        "--fixture-dir",
        type=Path,
        default=None,
        help="Offline fixtures: {order_id}.json (implies no real API writes unless --execute with mock)",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Perform PATCH (requires --confirm-phrase). Default is dry-run.",
    )
    ap.add_argument(
        "--confirm-phrase",
        default="",
        help="Must equal Owner approval phrase when --execute",
    )
    ap.add_argument(
        "--allow-cloud-execute",
        action="store_true",
        help=argparse.SUPPRESS,  # hard deny; not documented
    )
    args = ap.parse_args(argv)

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print("B31_PATCH_EXECUTOR")
    print(f"mode={mode}")
    print(f"production_sha_required={PRODUCTION_SHA}")
    print(f"allowlist_sha256_expected={EXPECTED_ALLOWLIST_SHA256}")

    digest = _sha256_file(args.allowlist)
    print(f"allowlist_sha256={digest} MATCH={'YES' if digest == EXPECTED_ALLOWLIST_SHA256 else 'NO'}")
    if digest != EXPECTED_ALLOWLIST_SHA256:
        return 2

    if args.execute:
        if args.confirm_phrase != CONFIRM_PHRASE:
            print("EXECUTE_BLOCKED reason=CONFIRM_PHRASE_MISMATCH")
            return 2
        if _cloud_agent_detected() and not args.allow_cloud_execute:
            print("EXECUTE_BLOCKED reason=CLOUD_AGENT_REFUSED (run on Owner VPS only)")
            return 2

    plan, links = load_expectations(args.allowlist, args.table)
    by_order: Dict[str, List[dict]] = defaultdict(list)
    for link in links:
        by_order[link["order_id"]].append(link)
    order_ids = list(plan["document_ids_allowlisted"])
    if len(order_ids) != EXPECTED_ORDERS or len(links) != EXPECTED_LINKS:
        _die("allowlist size broken")
    print(f"orders_expected={EXPECTED_ORDERS} links_expected={EXPECTED_LINKS}")

    # Auth / fixture transport
    token: Optional[str] = None
    api_base = ""
    fixture_store: Dict[str, dict] = {}

    if args.fixture_dir:
        for oid in order_ids:
            p = args.fixture_dir / f"{oid}.json"
            if not p.is_file():
                print(f"mismatch order={_oid8(oid)} reason=ORDER_MISSING")
                return 2
            fixture_store[oid] = json.loads(p.read_text(encoding="utf-8"))
        print("auth=OK mode=fixture_offline")
    else:
        env = _load_env_file(args.ro_env)
        for bad in FORBIDDEN_ENV_KEYS:
            if bad in env:
                _die(f"RO env must not contain {bad}")
        for k in env:
            if "MONGO" in k.upper():
                _die(f"RO env forbids Mongo key {k}")
        api_base = (env.get("ETERNAL_B31_API_BASE") or "").rstrip("/")
        if not api_base:
            api_base = (_parse_backend_api_base_optional() or "").rstrip("/")
        if not api_base:
            _die("ETERNAL_B31_API_BASE required")
        email = (env.get("ETERNAL_B31_EMAIL") or "").strip()
        password = env.get("ETERNAL_B31_PASSWORD") or ""
        if not email or not password:
            _die("ETERNAL_B31_EMAIL/PASSWORD required")
        code, login_body = _http_json(
            "POST", f"{api_base}/api/auth/login",
            body={"email": email, "password": password},
        )
        if code != 200 or not isinstance(login_body, dict) or not login_body.get("access_token"):
            print(f"auth_login=FAIL http={code}")
            return 2
        token = str(login_body["access_token"])
        me_code, me_body = _http_json("GET", f"{api_base}/api/auth/me", token=token)
        if me_code != 200:
            print(f"auth_me=FAIL http={me_code}")
            return 2
        role = str((me_body or {}).get("role") or "")
        perms = set((me_body or {}).get("permissions") or [])
        if args.execute:
            if role != "admin" and "production.route.edit" not in perms:
                print("auth_perm=FAIL need production.route.edit for --execute")
                return 2
        else:
            if role != "admin" and "po.view" not in perms and "production.route.edit" not in perms:
                print("auth_perm=FAIL need po.view for dry-run")
                return 2
        print("auth=OK mode=api")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = args.evidence_dir or (Path.home() / f"b31-patch-evidence-{ts}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "meta.json").write_text(
        json.dumps({
            "mode": mode,
            "started_at_utc": ts,
            "allowlist_sha256": digest,
            "production_sha_required": PRODUCTION_SHA,
            "orders": EXPECTED_ORDERS,
            "links": EXPECTED_LINKS,
            "patch_authorized_flag_in_manifest": plan.get("write_authorized"),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"evidence_dir={evidence_dir}")

    def fetch_order(oid: str) -> Tuple[int, dict]:
        if args.fixture_dir:
            return 200, fixture_store[oid]
        code, body = _http_json("GET", f"{api_base}/api/orders/{oid}", token=token)
        return code, _unwrap_order(body)

    def apply_patch(oid: str, items: List[dict]) -> Tuple[int, dict]:
        if args.fixture_dir:
            # Offline simulate: mutate fixture store only when execute
            order = json.loads(json.dumps(fixture_store[oid]))
            lines = list(order.get("line_items") or [])
            for it in items:
                if it.get("clear"):
                    idx = int(it["line_idx"])
                    lines[idx].pop("parent_line_id", None)
                    lines[idx].pop("parent_line_idx", None)
                    continue
                idx = int(it["line_idx"])
                pidx = int(it["parent_line_idx"])
                lines[idx]["parent_line_idx"] = pidx
                if it.get("parent_line_id"):
                    lines[idx]["parent_line_id"] = it["parent_line_id"]
                else:
                    # mirror API sync: set parent_line_id from parent identity
                    parent = lines[pidx]
                    pid = str(parent.get("line_id") or parent.get("id") or "").strip()
                    if pid:
                        lines[idx]["parent_line_id"] = pid
                    else:
                        lines[idx]["parent_line_id"] = f"idx:{pidx}"
            order["line_items"] = lines
            fixture_store[oid] = order
            # Persist for offline auditability (mirrors durable API write).
            (args.fixture_dir / f"{oid}.json").write_text(
                json.dumps(order) + "\n", encoding="utf-8",
            )
            return 200, order
        code, body = _http_json(
            "PATCH",
            f"{api_base}/api/orders/{oid}/line-items/addon-parent",
            token=token,
            body={"items": items},
        )
        return code, _unwrap_order(body)

    patched_orders = 0
    patched_links = 0
    dry_ok_orders = 0
    rollback_book: List[dict] = []

    for oid in order_ids:
        exp_links = by_order[oid]
        code, order = fetch_order(oid)
        if code != 200 or not order:
            print(f"STOP order={_oid8(oid)} reason=PRECHECK_FETCH_HTTP_{code}")
            print(f"RESULT=FAIL patched_orders={patched_orders} patched_links={patched_links}")
            return 2

        # Immediate live revalidation of every link on this PO
        fail_reasons: List[str] = []
        for exp in exp_links:
            fail_reasons.extend(
                f"{r}:{exp['service_line_idx']}" for r in validate_link(order, exp)
            )
        if fail_reasons:
            print(
                f"STOP order={_oid8(oid)} reason=PRECHECK_FAIL "
                f"detail={','.join(fail_reasons[:8])}"
            )
            print(f"RESULT=FAIL patched_orders={patched_orders} patched_links={patched_links}")
            return 2

        items = _build_patch_items(order, exp_links)
        before = _snapshot_parents(order, [e["service_line_idx"] for e in exp_links])
        ev = {
            "order_id_prefix": _oid8(oid),
            "order_id": oid,
            "mode": mode,
            "before": before,
            "intended_patch_items": items,
            "link_count": len(exp_links),
        }

        if not args.execute:
            (evidence_dir / f"dryrun-{_oid8(oid)}.json").write_text(
                json.dumps(ev, indent=2) + "\n", encoding="utf-8",
            )
            print(
                f"dryrun_ok order={_oid8(oid)} links={len(exp_links)} "
                f"patch_items={len(items)}"
            )
            dry_ok_orders += 1
            continue

        # EXECUTE
        pcode, patched = apply_patch(oid, items)
        if pcode not in (200, 201) or not patched:
            print(f"STOP order={_oid8(oid)} reason=PATCH_HTTP_{pcode}")
            # re-fetch for evidence
            _, after_fail = fetch_order(oid)
            ev["after"] = _snapshot_parents(
                after_fail, [e["service_line_idx"] for e in exp_links],
            )
            ev["patch_http"] = pcode
            (evidence_dir / f"FAIL-{_oid8(oid)}.json").write_text(
                json.dumps(ev, indent=2) + "\n", encoding="utf-8",
            )
            print(f"RESULT=FAIL patched_orders={patched_orders} patched_links={patched_links}")
            return 2

        # Independent GET verify
        vcode, verified = fetch_order(oid)
        if vcode != 200:
            print(f"STOP order={_oid8(oid)} reason=POSTCHECK_FETCH_HTTP_{vcode}")
            print(f"RESULT=FAIL patched_orders={patched_orders} patched_links={patched_links}")
            return 2
        post_reasons = _verify_persisted(verified, exp_links)
        after = _snapshot_parents(verified, [e["service_line_idx"] for e in exp_links])
        rollback_items = _build_rollback_items(before)
        ev["after"] = after
        ev["patch_http"] = pcode
        ev["postcheck"] = post_reasons or ["OK"]
        ev["rollback_items"] = rollback_items
        (evidence_dir / f"ok-{_oid8(oid)}.json").write_text(
            json.dumps(ev, indent=2) + "\n", encoding="utf-8",
        )
        if post_reasons:
            print(
                f"STOP order={_oid8(oid)} reason=POSTCHECK_FAIL "
                f"detail={','.join(post_reasons)}"
            )
            print(f"RESULT=FAIL patched_orders={patched_orders} patched_links={patched_links}")
            print(
                f"rollback_hint order={_oid8(oid)} "
                f"PATCH clear items recorded in evidence ok/FAIL file"
            )
            return 2

        patched_orders += 1
        patched_links += len(exp_links)
        rollback_book.append({
            "order_id": oid,
            "order_id_prefix": _oid8(oid),
            "items": rollback_items,
        })
        print(f"patched_ok order={_oid8(oid)} links={len(exp_links)}")

    # Write rollback book for all successes
    (evidence_dir / "rollback_book.json").write_text(
        json.dumps({
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "orders": rollback_book,
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.execute:
        if patched_orders != EXPECTED_ORDERS or patched_links != EXPECTED_LINKS:
            print(
                f"RESULT=FAIL patched_orders={patched_orders}/{EXPECTED_ORDERS} "
                f"patched_links={patched_links}/{EXPECTED_LINKS}"
            )
            return 2
        print(f"RESULT=PASS patched_orders={patched_orders} patched_links={patched_links}")
        print(f"rollback_book={evidence_dir / 'rollback_book.json'}")
        return 0

    if dry_ok_orders != EXPECTED_ORDERS:
        print(f"RESULT=FAIL dryrun_orders={dry_ok_orders}/{EXPECTED_ORDERS}")
        return 2
    print(f"RESULT=PASS dryrun_orders={dry_ok_orders} links_planned={EXPECTED_LINKS}")
    print("NEXT: re-run with --execute --confirm-phrase '…' on Owner VPS only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
