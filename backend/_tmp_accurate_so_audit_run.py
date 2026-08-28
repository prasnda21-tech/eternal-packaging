"""CLI: walk live GET sales-order/detail.do for Outlet / Zona / Warna paths.

Temporary. Do not wire to FastAPI or the frontend. Do not commit dumps.
Never prints tokens, Authorization, secrets, or full payloads.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from services.accurate import (  # noqa: E402
    AccurateClient,
    AccurateRequestError,
    accurate_http_timeout,
    new_correlation_id,
)
from services.accurate_so_audit import (  # noqa: E402
    DEFAULT_TARGET_VALUES,
    filter_target_hits,
    format_hits,
    interesting_scalars,
    unique_key_names,
    walk_scalar_paths,
)

KNOWN_SO = "PO/2026/08/28/000004"
SEED_NUMBERS = [KNOWN_SO]


def _print_section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _print_scalars(rows: List[tuple], limit: int = 80) -> None:
    if not rows:
        print("(none)")
        return
    for path, value in rows[:limit]:
        shown = value if len(value) <= 160 else value[:157] + "..."
        print(f"  {path} = {shown!r}")
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more")


async def _fetch_raw_json(
    client: AccurateClient,
    http: httpx.AsyncClient,
    url: str,
    params: Dict[str, str],
    *,
    logical_name: str,
    so_number: str = "",
) -> Optional[Dict[str, Any]]:
    cid = new_correlation_id()
    try:
        r = await client._authed_get(
            http,
            url,
            params=params,
            logical_name=logical_name,
            so_number=so_number,
            correlation_id=cid,
        )
    except AccurateRequestError as e:
        print(f"  request_error category={e.category} http={e.http_status}")
        return None
    try:
        payload = r.json()
    except Exception:
        print("  invalid_json")
        return None
    if not isinstance(payload, dict):
        print("  payload_not_object")
        return None
    if payload.get("s") is False:
        print("  accurate_s_false")
        return None
    d = payload.get("d")
    if isinstance(d, dict):
        return d
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    print("  empty_body")
    return None


def _item_ids(so: Dict[str, Any]) -> List[str]:
    lines = so.get("detailItem") or so.get("detailItems") or []
    ids: List[str] = []
    if not isinstance(lines, list):
        return ids
    for li in lines:
        if not isinstance(li, dict):
            continue
        item = li.get("item") if isinstance(li.get("item"), dict) else {}
        raw = item.get("id") or li.get("itemId") or li.get("itemNo")
        if raw not in (None, ""):
            ids.append(str(raw))
    return ids


def _audit_body(label: str, body: Dict[str, Any], targets: List[str]) -> None:
    scalars = walk_scalar_paths(body)
    hits = filter_target_hits(scalars, targets)
    _print_section(f"{label} target hits ({len(hits)})")
    print(format_hits(hits))
    _print_section(f"{label} interesting-key scalars")
    _print_scalars(interesting_scalars(scalars), limit=120)
    keys = unique_key_names(body)
    _print_section(f"{label} unique keys ({len(keys)})")
    print(", ".join(keys) if keys else "(none)")


async def main() -> int:
    extra_targets = [a for a in sys.argv[1:] if not a.startswith("-")]
    targets = list(DEFAULT_TARGET_VALUES)
    for t in extra_targets:
        if t.upper() not in {x.upper() for x in targets}:
            targets.append(t)

    client = AccurateClient()
    print(f"auth_mode={client.auth_mode}")
    print(f"targets={targets}")
    if not client.is_live:
        print("LIVE_UNAVAILABLE: Accurate client is mock. Cannot prove JSON paths.")
        return 2

    host, _session = await client._ensure_db_session()
    print(f"db_host_set={bool(host)}")

    timeout = accurate_http_timeout()
    numbers: List[str] = list(SEED_NUMBERS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
        # list.do only (no per-row enrich) to collect extra SO numbers.
        list_url = f"{host.rstrip('/')}/accurate/api/sales-order/list.do"
        try:
            lr = await client._authed_get(
                http,
                list_url,
                params={"sp.pageSize": 8, "sp.page": 1, "sp.sort": "transDate|desc"},
                logical_name="sales-order/list.do",
                correlation_id=new_correlation_id(),
            )
            list_body = lr.json() if not lr.is_error else {}
        except AccurateRequestError as e:
            print(f"list_error category={e.category}")
            list_body = {}
        rows_raw = list_body.get("d") if isinstance(list_body, dict) else None
        if not isinstance(rows_raw, list):
            rows_raw = []
        print(f"list_rows={len(rows_raw)}")
        for row in rows_raw:
            if not isinstance(row, dict):
                continue
            num = str(row.get("number") or "").strip()
            if num and num not in numbers:
                numbers.append(num)
        numbers = numbers[:4]
        print(f"so_numbers={numbers}")

        fetched = 0
        item_ids_seen: List[str] = []
        for so_number in numbers:
            _print_section(f"SO {so_number}")
            url = f"{host.rstrip('/')}/accurate/api/sales-order/detail.do"
            body = await _fetch_raw_json(
                client,
                http,
                url,
                client._so_detail_params(so_number),
                logical_name="sales-order/detail.do",
                so_number=so_number,
            )
            if body is None:
                continue
            fetched += 1
            print(f"so_id={body.get('id')} number={body.get('number')}")
            _audit_body(f"SO {so_number}", body, targets)
            for iid in _item_ids(body):
                if iid not in item_ids_seen:
                    item_ids_seen.append(iid)

        print(f"\nso_fetched={fetched}")
        if fetched < 3:
            print("NEED_MORE_SO: fewer than 3 detail.do bodies")

        # Diagnostic-only item/detail.do: prove whether color lives on item master.
        # Not a production mapping change.
        for iid in item_ids_seen[:3]:
            _print_section(f"ITEM {iid} item/detail.do (diagnostic only)")
            item_url = f"{host.rstrip('/')}/accurate/api/item/detail.do"
            item_body = await _fetch_raw_json(
                client,
                http,
                item_url,
                {"id": iid},
                logical_name="item/detail.do",
            )
            if item_body is None:
                continue
            print(f"item_name={item_body.get('name')!r} item_no={item_body.get('no')!r}")
            _audit_body(f"ITEM {iid}", item_body, targets)

    return 0 if fetched >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
