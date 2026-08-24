"""
Accurate Online integration boundary.

- MOCK mode (default): returns bundled fixtures.
- LIVE mode: enabled when ACCURATE_CLIENT_ID + ACCURATE_CLIENT_SECRET +
  ACCURATE_DB_ID + (ACCURATE_ACCESS_TOKEN or ACCURATE_REFRESH_TOKEN) are set.
  Follows the Accurate Online OAuth flow:
    1. Refresh access token if only refresh token provided (POST /oauth/token).
    2. GET /api/open-db.do?id={db_id} to obtain per-database host + session.
    3. GET {host}/accurate/api/sales-order/detail.do?id=... or ?number=...
       with headers Authorization: Bearer {token} and X-Session-ID: {session}.
  READ-ONLY: only GET calls are ever issued.

Credentials are read from environment variables only. Nothing about the
credentials is logged, returned to the frontend, or stored in MongoDB.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


# In-memory cache for the recent-SO list (survives Accurate rate-limits gracefully)
_SO_LIST_CACHE: Dict[str, Any] = {}
_LIST_CACHE_TTL = 60  # seconds
_ITEM_HTTP_TIMEOUT = 25
_ITEM_LIST_MAX_ATTEMPTS = 2
_ITEM_LIST_RETRY_WAIT_S = 0.4
_ITEM_LIST_MAX_PAGES = 200
_ITEM_LIST_RETRYABLE = (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError)
_ITEM_DETAIL_CONCURRENCY = 4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccurateClient:
    """Read-only Accurate Online client. Falls back to MOCK when creds absent.

    Auth modes (in priority order):
      1. api_token  — ACCURATE_API_TOKEN + ACCURATE_DB_ID  (long-lived Bearer
         token issued via Accurate Developer Console; identical header format
         to an OAuth access_token per accurate.id API docs).
      2. oauth      — ACCURATE_CLIENT_ID + ACCURATE_CLIENT_SECRET + ACCURATE_DB_ID
                      + (ACCURATE_ACCESS_TOKEN or ACCURATE_REFRESH_TOKEN)
      3. mock       — no credentials
    Credentials are never logged or returned to the frontend.
    """

    def __init__(self) -> None:
        # API Token mode (preferred when set) — needs both token + signature secret
        self.api_token = os.environ.get("ACCURATE_API_TOKEN") or ""
        self.api_signature_secret = (
            os.environ.get("ACCURATE_API_SIGNATURE_SECRET")
            or os.environ.get("ACCURATE_SIGNATURE_SECRET")
            or os.environ.get("ACCURATE_API_SECRET")
            or ""
        )
        # OAuth mode (legacy / fallback)
        self.client_id = os.environ.get("ACCURATE_CLIENT_ID") or ""
        self.client_secret = os.environ.get("ACCURATE_CLIENT_SECRET") or ""
        self.access_token = os.environ.get("ACCURATE_ACCESS_TOKEN") or ""
        self.refresh_token = os.environ.get("ACCURATE_REFRESH_TOKEN") or ""
        # Shared
        self.db_id = os.environ.get("ACCURATE_DB_ID") or ""
        self.account_base_url = (os.environ.get("ACCURATE_ACCOUNT_BASE_URL") or "https://account.accurate.id").rstrip("/")
        self.host = os.environ.get("ACCURATE_HOST") or self.account_base_url

    @property
    def auth_mode(self) -> str:
        """Return which auth strategy will be used ('api_token' / 'oauth' / 'mock')."""
        if self.api_token and self.api_signature_secret and self.db_id:
            return "api_token"
        if (self.client_id and self.client_secret and self.db_id
                and (self.access_token or self.refresh_token)):
            return "oauth"
        return "mock"

    @property
    def is_live(self) -> bool:
        return self.auth_mode != "mock"

    def _signed_headers(self, bearer: str) -> Dict[str, str]:
        """Compute Accurate API-Token signed headers. Bearer + timestamp + HMAC-SHA256(secret, ts_ms) hex."""
        ts = str(int(time.time() * 1000))
        sig = hmac.new(self.api_signature_secret.encode("utf-8"), ts.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "Authorization": f"Bearer {bearer}",
            "X-Api-Timestamp": ts,
            "X-Api-Signature": sig,
        }

    # --- Live-mode helpers ------------------------------------------------
    async def _obtain_token(self) -> str:
        # API Token: long-lived bearer, no refresh flow.
        if self.api_token:
            return self.api_token
        if self.access_token:
            return self.access_token
        if not self.refresh_token:
            raise RuntimeError("No Accurate access/refresh token configured")
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.post(
                f"{self.account_base_url}/oauth/token",
                headers={"Authorization": f"Basic {basic}"},
                data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
            )
        if r.is_error:
            raise RuntimeError("Accurate token refresh failed")
        return r.json()["access_token"]

    async def _open_db(self, token: str) -> Tuple[str, str]:
        """Return (host, session) for the configured database.

        - api_token mode: use GET /api/api-token.do to resolve the host; session
          is not required for signed API-Token requests, so we return an empty
          string and downstream requests won't send X-Session-ID.
        - oauth mode: legacy open-db.do flow returns host + session.
        """
        if self.auth_mode == "api_token":
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                r = await client.get(
                    f"{self.account_base_url}/api/api-token.do",
                    headers=self._signed_headers(token),
                )
            if r.is_error:
                raise RuntimeError(f"Accurate api-token.do failed ({r.status_code})")
            body = r.json()
            if body.get("s") is False:
                raise RuntimeError("Accurate api-token.do returned auth error")
            d = body.get("d") if isinstance(body.get("d"), dict) else {}
            database = d.get("database") if isinstance(d.get("database"), dict) else {}
            host = database.get("host") or ""
            if not host:
                raise RuntimeError("Accurate returned no database host for API Token")
            return host.rstrip("/"), ""

        # oauth mode
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(
                f"{self.account_base_url}/api/open-db.do",
                headers={"Authorization": f"Bearer {token}"},
                params={"id": self.db_id},
            )
        if r.is_error:
            raise RuntimeError(f"Accurate open-db failed ({r.status_code})")
        body = r.json()
        host = body.get("host") or ""
        session = body.get("session") or ""
        if not host or not session:
            nested = body.get("data") if isinstance(body.get("data"), dict) else {}
            host = host or nested.get("host") or ""
            session = session or nested.get("session") or ""
        if not host or not session:
            raise RuntimeError("Accurate returned no database host/session")
        return host.rstrip("/"), session

    def _request_headers(self, token: str, session: str = "") -> Dict[str, str]:
        """Same Authorization / signature / session headers used by Import PO."""
        if self.auth_mode == "api_token":
            return self._signed_headers(token)
        return {"Authorization": f"Bearer {token}", "X-Session-ID": session}

    async def _live_session(self) -> Tuple[str, Dict[str, str]]:
        """Return (host, headers) for Accurate API calls. Reused by SO + item."""
        token = await self._obtain_token()
        host, session = await self._open_db(token)
        return host, self._request_headers(token, session)

    async def _live_fetch_so(self, so_number: str) -> Optional[Dict[str, Any]]:
        host, headers = await self._live_session()
        params = {"id": so_number} if so_number.isdigit() else {"number": so_number}
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            r = await client.get(
                f"{host}/accurate/api/sales-order/detail.do",
                headers=headers, params=params,
            )
        if r.status_code == 404:
            return None
        if r.is_error:
            raise RuntimeError(f"Accurate SO fetch failed ({r.status_code})")
        payload = r.json()
        if payload.get("s") is False:
            # Auth or business error — bubble a generic message (no credential info)
            raise RuntimeError("Accurate SO fetch returned failure")
        d = payload.get("d") if isinstance(payload.get("d"), dict) else None
        if d is None:
            d = payload.get("data") if isinstance(payload.get("data"), dict) else None
        if d is None:
            d = payload.get("r") if isinstance(payload.get("r"), dict) else None
        if d is None:
            return None
        return _normalize_accurate_so(d)

    # --- Public API -------------------------------------------------------
    async def fetch_sales_orders(self) -> List[Dict[str, Any]]:
        """Bulk sync is intentionally disabled in live mode."""
        if self.is_live:
            raise NotImplementedError("Bulk sync disabled — use single-PO import.")
        return _MOCK_SALES_ORDERS

    async def fetch_single_sales_order(self, so_number: str) -> Optional[Dict[str, Any]]:
        """Fetch ONE Sales Order (READ-ONLY). Returns None if not found."""
        so_number = (so_number or "").strip()
        if not so_number:
            return None
        if self.is_live:
            return await self._live_fetch_so(so_number)
        key = so_number.upper()
        for so in _MOCK_SALES_ORDERS:
            if so.get("po_number", "").upper() == key or so.get("accurate_id", "").upper() == key:
                return dict(so)
        return None

    async def list_recent_sales_orders(
        self,
        limit: int = 20,
        force: bool = False,
        page: int = 1,
        keyword: str = "",
    ) -> Dict[str, Any]:
        """List Sales Orders sorted by transDate desc (READ-ONLY). Enriches
        with customer + total via detail.do. Uses in-memory cache (60s TTL)
        keyed by (page, limit, keyword) to survive Accurate rate-limits.

        NOTE: Accurate `sales-order/list.do` REJECTS custom `fields` param
        AND `sp.sort` values that contain spaces (returns
        `{s:false, d:["Upsss..."]}`). Correct sort syntax is
        `transDate|desc`. We keep params MINIMAL and let Accurate return its
        default row schema.

        Returns {rows, total, page, pageSize, pageCount, host, mode,
                 cached, fetched_at}.
        """
        import time as _t
        limit = max(1, min(int(limit or 20), 100))
        page = max(1, int(page or 1))
        keyword = (keyword or "").strip()
        cache_key = f"list:{self.auth_mode}:{page}:{limit}:{keyword.lower()}"
        entry = _SO_LIST_CACHE.get(cache_key)
        if not force and entry and (_t.time() - entry["at"]) < _LIST_CACHE_TTL:
            return {**entry["data"], "cached": True, "fetched_at": entry["at"]}

        if not self.is_live:
            all_mock = _MOCK_SALES_ORDERS
            if keyword:
                kw = keyword.lower()
                all_mock = [
                    so for so in all_mock
                    if kw in (so.get("po_number") or "").lower()
                    or kw in (so.get("customer") or "").lower()
                ]
            start = (page - 1) * limit
            page_rows = all_mock[start:start + limit]
            rows = []
            for so in page_rows:
                rows.append({
                    "accurate_id": so.get("accurate_id"),
                    "number": so.get("po_number"),
                    "transDate": so.get("order_date"),
                    "customer": so.get("customer"),
                    "totalAmount": so.get("total"),
                    "statusName": so.get("status_label", "Mock"),
                })
            total = len(all_mock)
            payload = {
                "rows": rows, "total": total, "page": page, "pageSize": limit,
                "pageCount": max(1, (total + limit - 1) // limit),
                "host": self.host, "mode": "mock",
            }
            _SO_LIST_CACHE[cache_key] = {"data": payload, "at": _t.time()}
            return {**payload, "cached": False, "fetched_at": _t.time()}

        import asyncio as _aio
        token = await self._obtain_token()
        host, session = await self._open_db(token)

        # Step 1: list — MINIMAL params (no `fields`, sort uses pipe syntax)
        list_params: Dict[str, Any] = {
            "sp.pageSize": limit,
            "sp.page": page,
            "sp.sort": "transDate|desc",
        }
        if keyword:
            # Accurate supports simple keyword filter via `keywords` param on
            # most list endpoints; fall back to filter.number if it fails.
            list_params["keywords"] = keyword

        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            r = await client.get(
                f"{host}/accurate/api/sales-order/list.do",
                headers=self._signed_headers(token) if self.auth_mode == "api_token"
                        else {"Authorization": f"Bearer {token}", "X-Session-ID": session},
                params=list_params,
            )
        if r.is_error:
            if entry:
                return {**entry["data"], "cached": True, "stale": True, "fetched_at": entry["at"]}
            raise RuntimeError(f"Accurate list HTTP {r.status_code}")
        body = r.json()
        if body.get("s") is False:
            # Log the tenant-side error message (safe — no credential in payload) for diagnosis
            err_msg = ""
            d = body.get("d")
            if isinstance(d, list) and d:
                err_msg = str(d[0])[:180]
            _last_list_error["message"] = err_msg
            _last_list_error["at"] = _t.time()
            _last_list_error["operation"] = "sales-order/list.do"
            if entry:
                return {**entry["data"], "cached": True, "stale": True, "fetched_at": entry["at"]}
            raise RuntimeError(f"Accurate list rejected: {err_msg or 'unknown tenant error'}")
        rows_raw = body.get("d") or []
        sp = body.get("sp") or {}
        total = sp.get("rowCount") or len(rows_raw)
        page_count = sp.get("pageCount") or 1

        # Step 2: enrich each row with detail (customer/total/status) — parallel best-effort
        sem = _aio.Semaphore(4)

        async def _enrich(row):
            oid = row.get("id")
            async with sem:
                try:
                    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                        d = await client.get(
                            f"{host}/accurate/api/sales-order/detail.do",
                            headers=self._signed_headers(token) if self.auth_mode == "api_token"
                                    else {"Authorization": f"Bearer {token}", "X-Session-ID": session},
                            params={"id": oid},
                        )
                    det_body = d.json() if not d.is_error else {}
                    det = det_body.get("d") if isinstance(det_body.get("d"), dict) else {}
                    cust = det.get("customer") if isinstance(det.get("customer"), dict) else {}
                    return {
                        "accurate_id": str(oid) if oid is not None else None,
                        "number": det.get("number") or row.get("number"),
                        "transDate": det.get("transDate") or row.get("transDate"),
                        "customer": (cust or {}).get("name") or "-",
                        "totalAmount": det.get("totalAmount"),
                        "statusName": det.get("statusName") or det.get("orderStatus") or None,
                    }
                except Exception:
                    return {
                        "accurate_id": str(oid) if oid is not None else None,
                        "number": row.get("number"),
                        "transDate": row.get("transDate"),
                        "customer": None,
                        "totalAmount": None,
                        "statusName": None,
                    }

        enriched = await _aio.gather(*[_enrich(r) for r in rows_raw])
        payload = {
            "rows": enriched, "total": total, "page": page, "pageSize": limit,
            "pageCount": page_count, "host": host, "mode": "live",
        }
        _SO_LIST_CACHE[cache_key] = {"data": payload, "at": _t.time()}
        return {**payload, "cached": False, "fetched_at": _t.time()}

    # --- Item / Barang-Jasa (same client, auth, headers, timeouts as SO) -----
    async def fetch_item_detail(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Fetch ONE item via Accurate `item/detail.do` (READ-ONLY).

        Same auth/headers/timeout as sales-order/detail.do. Returns None if
        the item is not found. Never logs credentials.
        """
        item_id = str(item_id or "").strip()
        if not item_id:
            return None
        if not self.is_live:
            for raw in _MOCK_ITEMS:
                if str(raw.get("id")) == item_id:
                    return normalize_accurate_item(raw)
            return None
        token = await self._obtain_token()
        host, session = await self._open_db(token)
        async with httpx.AsyncClient(timeout=_ITEM_HTTP_TIMEOUT, follow_redirects=True) as client:
            return await self._fetch_item_detail_live(
                client,
                token=token,
                host=host,
                session=session,
                item_id=item_id,
                raise_on_error=True,
            )

    async def list_items(
        self,
        limit: int = 100,
        page: int = 1,
    ) -> Dict[str, Any]:
        """One page of Accurate items via `item/list.do` (READ-ONLY).

        Pagination params match Import PO (`sp.pageSize`, `sp.page`).
        Do NOT send `fields`: this tenant rejects custom fields on list.do
        (sales-order/list.do returned {s:false}; item/list.do with extra
        fields such as itemType/unit1Name/suspended raises HTTP 4xx and
        skipped the s:false retry — production POST /master-items/sync 502).
        """
        limit = max(1, min(int(limit or 100), 100))
        page = max(1, int(page or 1))
        if not self.is_live:
            start = (page - 1) * limit
            page_rows = _MOCK_ITEMS[start:start + limit]
            total = len(_MOCK_ITEMS)
            return {
                "rows": [normalize_accurate_item(r) for r in page_rows],
                "total": total,
                "page": page,
                "pageSize": limit,
                "pageCount": max(1, (total + limit - 1) // limit),
                "host": self.host,
                "mode": "mock",
            }

        token = await self._obtain_token()
        host, session = await self._open_db(token)
        async with httpx.AsyncClient(timeout=_ITEM_HTTP_TIMEOUT, follow_redirects=True) as http:
            return await self._list_items_live_page(
                http, token=token, host=host, session=session, limit=limit, page=page,
            )

    def _item_list_payload(self, body: Dict[str, Any], *, limit: int, page: int, host: str) -> Dict[str, Any]:
        rows_raw = body.get("d") or []
        if not isinstance(rows_raw, list):
            rows_raw = []
        sp = body.get("sp") or {}
        total = sp.get("rowCount") if sp.get("rowCount") is not None else len(rows_raw)
        page_count = sp.get("pageCount") or max(1, (int(total) + limit - 1) // limit)
        rows = []
        for raw in rows_raw:
            if not isinstance(raw, dict):
                continue
            norm = normalize_accurate_item(raw)
            if norm:
                rows.append(norm)
        return {
            "rows": rows,
            "total": int(total),
            "page": page,
            "pageSize": limit,
            "pageCount": int(page_count),
            "host": host,
            "mode": "live",
        }

    async def _list_items_live_page(
        self,
        http: httpx.AsyncClient,
        *,
        token: str,
        host: str,
        session: str,
        limit: int,
        page: int,
    ) -> Dict[str, Any]:
        list_params: Dict[str, Any] = {
            "sp.pageSize": limit,
            "sp.page": page,
        }
        body = await self._get_item_list_body(
            http, host, token, session, list_params, page=page,
        )
        if body is None:
            err = _last_list_error.get("message") or "unknown tenant error"
            raise RuntimeError(f"Accurate item list rejected: {err}")
        return self._item_list_payload(body, limit=limit, page=page, host=host)

    async def _get_item_list_body(
        self,
        http: httpx.AsyncClient,
        host: str,
        token: str,
        session: str,
        params: Dict[str, Any],
        *,
        page: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        last_exc: Optional[BaseException] = None
        for attempt in range(1, _ITEM_LIST_MAX_ATTEMPTS + 1):
            # Fresh HMAC timestamp/signature per HTTP attempt (same as Import PO).
            headers = self._request_headers(token, session)
            try:
                r = await http.get(
                    f"{host}/accurate/api/item/list.do",
                    headers=headers,
                    params=params,
                )
            except _ITEM_LIST_RETRYABLE as e:
                last_exc = e
                _record_list_error(
                    operation="item/list.do",
                    message="timeout" if isinstance(e, httpx.TimeoutException) else "request failed",
                    exception=e,
                    page=page,
                    attempt=attempt,
                )
                logger.warning(
                    "Accurate master item sync failed operation=master_items_sync "
                    "endpoint=item/list.do page=%s attempt=%s exception=%s",
                    page if page is not None else "-",
                    attempt,
                    type(e).__name__,
                )
                if attempt < _ITEM_LIST_MAX_ATTEMPTS:
                    await asyncio.sleep(_ITEM_LIST_RETRY_WAIT_S)
                    continue
                raise RuntimeError("Accurate item list timeout") from e
            except httpx.HTTPError as e:
                _record_list_error(
                    operation="item/list.do",
                    message="request failed",
                    exception=e,
                    page=page,
                    attempt=attempt,
                )
                raise RuntimeError("Accurate item list request failed") from e

            if attempt > 1:
                logger.info(
                    "Accurate master item sync operation=master_items_sync "
                    "endpoint=item/list.do page=%s attempt=%s result=success",
                    page if page is not None else "-",
                    attempt,
                )
            if r.is_error:
                _record_list_error(
                    operation="item/list.do",
                    status=r.status_code,
                    message=f"HTTP {r.status_code}",
                    page=page,
                    attempt=attempt,
                )
                raise RuntimeError(f"Accurate item list HTTP {r.status_code}")
            try:
                body = r.json()
            except Exception as e:
                _record_list_error(
                    operation="item/list.do",
                    status=r.status_code,
                    message="invalid response",
                    exception=e,
                    page=page,
                    attempt=attempt,
                )
                raise RuntimeError(f"Accurate item list invalid response ({r.status_code})") from e
            if body.get("s") is False:
                err_msg = ""
                d = body.get("d")
                if isinstance(d, list) and d:
                    err_msg = str(d[0])[:180]
                _record_list_error(
                    operation="item/list.do",
                    status=r.status_code,
                    message=err_msg,
                    page=page,
                    attempt=attempt,
                )
                return None
            return body if isinstance(body, dict) else None
        raise RuntimeError("Accurate item list timeout") from last_exc

    async def fetch_all_items(self) -> List[Dict[str, Any]]:
        """Paginate `item/list.do` until the Accurate dataset is exhausted.

        Live mode opens api-token.do / database host ONCE per sync, reuses one
        httpx client, and regenerates signed headers per GET (Import PO pattern).
        """
        page_size = 100
        if not self.is_live:
            all_rows: List[Dict[str, Any]] = []
            page = 1
            while page <= _ITEM_LIST_MAX_PAGES:
                payload = await self.list_items(limit=page_size, page=page)
                rows = payload.get("rows") or []
                all_rows.extend(rows)
                page_count = int(payload.get("pageCount") or 1)
                if page >= page_count or not rows:
                    break
                page += 1
            return await self._enrich_incomplete_item_rows(all_rows)

        token = await self._obtain_token()
        host, session = await self._open_db(token)
        all_rows: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=_ITEM_HTTP_TIMEOUT, follow_redirects=True) as http:
            page = 1
            while page <= _ITEM_LIST_MAX_PAGES:
                payload = await self._list_items_live_page(
                    http, token=token, host=host, session=session, limit=page_size, page=page,
                )
                rows = payload.get("rows") or []
                all_rows.extend(rows)
                page_count = int(payload.get("pageCount") or 1)
                if page >= page_count or not rows:
                    break
                page += 1
            all_rows = await self._enrich_incomplete_item_rows(
                all_rows, http=http, token=token, host=host, session=session,
            )
        return all_rows

    async def _fetch_item_detail_live(
        self,
        http: httpx.AsyncClient,
        *,
        token: str,
        host: str,
        session: str,
        item_id: str,
        raise_on_error: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """GET item/detail.do on an existing session/http client. Fresh signed headers per try."""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, _ITEM_LIST_MAX_ATTEMPTS + 1):
            headers = self._request_headers(token, session)
            try:
                r = await http.get(
                    f"{host}/accurate/api/item/detail.do",
                    headers=headers,
                    params={"id": item_id},
                )
            except _ITEM_LIST_RETRYABLE as e:
                last_exc = e
                if attempt < _ITEM_LIST_MAX_ATTEMPTS:
                    await asyncio.sleep(_ITEM_LIST_RETRY_WAIT_S)
                    continue
                if raise_on_error:
                    raise RuntimeError("Accurate item fetch timeout") from e
                return None
            except httpx.HTTPError as e:
                if raise_on_error:
                    raise RuntimeError("Accurate item fetch request failed") from e
                return None
            if r.status_code == 404:
                return None
            if r.is_error:
                if raise_on_error:
                    raise RuntimeError(f"Accurate item fetch failed ({r.status_code})")
                return None
            try:
                payload = r.json()
            except Exception as e:
                if raise_on_error:
                    raise RuntimeError(
                        f"Accurate item fetch invalid response ({r.status_code})"
                    ) from e
                return None
            if not isinstance(payload, dict):
                return None
            if payload.get("s") is False:
                if raise_on_error:
                    raise RuntimeError("Accurate item fetch returned failure")
                return None
            d = _item_detail_body(payload)
            if d is None:
                return None
            return normalize_accurate_item(d)
        if raise_on_error:
            raise RuntimeError("Accurate item fetch timeout") from last_exc
        return None

    async def _enrich_incomplete_item_rows(
        self,
        rows: List[Dict[str, Any]],
        *,
        http: Optional[httpx.AsyncClient] = None,
        token: str = "",
        host: str = "",
        session: str = "",
    ) -> List[Dict[str, Any]]:
        """Fill missing code/name from item/detail.do. Skip rows that already have both.

        Live path reuses the sync http client + token/host. Concurrency is chunked
        to _ITEM_DETAIL_CONCURRENCY — never one unbounded gather of the whole set.
        """
        if not rows:
            return rows
        incomplete = [r for r in rows if item_list_row_incomplete(r)]
        if not incomplete:
            return rows

        async def _one(row: Dict[str, Any]) -> Dict[str, Any]:
            aid = str(row.get("accurate_item_id") or "").strip()
            try:
                if http is None:
                    detail = await self.fetch_item_detail(aid)
                else:
                    detail = await self._fetch_item_detail_live(
                        http,
                        token=token,
                        host=host,
                        session=session,
                        item_id=aid,
                        raise_on_error=False,
                    )
            except Exception:
                return row
            return merge_accurate_item_row(row, detail)

        enriched_by_id: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(incomplete), _ITEM_DETAIL_CONCURRENCY):
            chunk = incomplete[i:i + _ITEM_DETAIL_CONCURRENCY]
            parts = await asyncio.gather(*[_one(r) for r in chunk], return_exceptions=True)
            for row, part in zip(chunk, parts):
                aid = str(row.get("accurate_item_id") or "")
                if isinstance(part, Exception) or not isinstance(part, dict):
                    enriched_by_id[aid] = row
                else:
                    enriched_by_id[aid] = part

        out: List[Dict[str, Any]] = []
        for row in rows:
            aid = str(row.get("accurate_item_id") or "")
            out.append(enriched_by_id.get(aid, row) if aid in enriched_by_id else row)
        return out


def sanitize_accurate_error(client: AccurateClient, msg: str) -> str:
    """Strip credential values from Accurate error strings before they leave the backend."""
    out = str(msg or "")
    for secret in (client.api_token, client.api_signature_secret, client.client_secret, client.access_token, client.refresh_token):
        if secret and secret in out:
            return "Accurate error"
    return out[:200]


# Last known list.do error from tenant (safe — no credential/token/signature/headers)
_last_list_error: Dict[str, Any] = {
    "message": "",
    "at": 0,
    "operation": "",
    "status": None,
    "exception": "",
    "page": None,
    "attempt": None,
}


def get_last_list_error() -> Dict[str, Any]:
    return dict(_last_list_error)


def _record_list_error(
    *,
    operation: str,
    status: Optional[int] = None,
    message: str = "",
    exception: Optional[BaseException] = None,
    page: Optional[int] = None,
    attempt: Optional[int] = None,
) -> None:
    _last_list_error["operation"] = operation
    _last_list_error["status"] = status
    _last_list_error["message"] = str(message or "")[:180]
    _last_list_error["exception"] = type(exception).__name__ if exception else ""
    _last_list_error["page"] = page
    _last_list_error["attempt"] = attempt
    _last_list_error["at"] = time.time()


def log_master_items_sync_failure(
    client: AccurateClient,
    exc: BaseException,
    *,
    endpoint: str = "item/list.do",
) -> None:
    """Safe server-side diagnostic. Never logs credentials, headers, or cookies."""
    last = get_last_list_error()
    msg = sanitize_accurate_error(client, str(exc))
    tenant = sanitize_accurate_error(client, last.get("message") or "")
    status = last.get("status")
    op = last.get("operation") or endpoint
    logger.warning(
        "Accurate master item sync failed operation=master_items_sync endpoint=%s page=%s attempt=%s status=%s exception=%s message=%s",
        op,
        "-" if last.get("page") in (None, "") else last.get("page"),
        "-" if last.get("attempt") in (None, "") else last.get("attempt"),
        "-" if status in (None, "") else status,
        type(exc).__name__,
        msg or tenant or "-",
    )


def _extract_accurate_item_id(li: Dict[str, Any], item: Any) -> Optional[str]:
    """Stable Accurate item identity from a Sales Order line. Never uses name."""
    raw = None
    if isinstance(item, dict):
        raw = item.get("id")
    if raw is None:
        raw = li.get("itemId") or li.get("itemID")
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw)


def _blank_to_none(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _item_detail_body(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    d = payload.get("d") if isinstance(payload.get("d"), dict) else None
    if d is None:
        d = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if d is None:
        d = payload.get("r") if isinstance(payload.get("r"), dict) else None
    return d if isinstance(d, dict) else None


def _extract_item_unit(raw: Dict[str, Any]) -> Optional[str]:
    """Unit from Accurate item list/detail. Only documented Accurate keys."""
    for key in ("unit1Name", "itemUnitName", "unitName"):
        unit = _blank_to_none(raw.get(key))
        if unit:
            return unit
    for key in ("unit1", "itemUnit"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            unit = _blank_to_none(nested.get("name") or nested.get("unitName"))
            if unit:
                return unit
    return None


def item_list_row_incomplete(row: Optional[Dict[str, Any]]) -> bool:
    """True when list.do left code or name empty — those live on item/detail.do."""
    if not isinstance(row, dict):
        return False
    if not str(row.get("accurate_item_id") or "").strip():
        return False
    name = str(row.get("item_name") or "").strip()
    code = str(row.get("item_code") or "").strip()
    return not name or not code


def merge_accurate_item_row(
    base: Dict[str, Any],
    detail: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Overlay detail fields onto a list row. Never invent values. Never use id as name."""
    if not isinstance(detail, dict):
        return base
    out = dict(base)
    for key in ("item_code", "item_name", "item_type", "unit", "accurate_updated_at"):
        val = detail.get(key)
        if val is None or val == "":
            continue
        out[key] = val
    if detail.get("is_active") is not None:
        out["is_active"] = detail["is_active"]
    return out


def normalize_accurate_item(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Coerce an Accurate item/list or item/detail row into Master Item shape.

    Official item/list.do example uses fields=id,name,no. This tenant rejects
    custom `fields` (HTTP 400), so default list rows may only contain `id`.
    item/detail.do returns no/name/itemType and unit1Name when present.
    Missing optional fields stay None — we do not invent harga, stok, barcode,
    kategori, or satuan, and we never use Accurate id as the name.
    Identity is always `accurate_item_id` (Accurate `id`).
    """
    if not isinstance(raw, dict):
        return None
    item_id = raw.get("id")
    if item_id is None or str(item_id).strip() == "":
        return None
    item_type = _blank_to_none(raw.get("itemType") or raw.get("item_type"))
    unit = _extract_item_unit(raw)
    suspended = raw.get("suspended")
    inactive = raw.get("inactive")
    is_active: Optional[bool] = None
    if isinstance(suspended, bool):
        is_active = not suspended
    elif isinstance(inactive, bool):
        is_active = not inactive
    accurate_updated = raw.get("lastUpdate") or raw.get("lastUpdated") or None
    name = _blank_to_none(raw.get("name") if raw.get("name") not in (None, "") else raw.get("itemName"))
    code = raw.get("no")
    if code in (None, ""):
        code = raw.get("itemNo")
    code = _blank_to_none(code)
    return {
        "accurate_item_id": str(item_id),
        "item_code": code,
        "item_name": name,
        "item_type": item_type,
        "unit": unit,
        "is_active": is_active,
        "accurate_updated_at": str(accurate_updated) if accurate_updated else None,
    }


def _normalize_accurate_so(d: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce Accurate's Sales Order detail payload into our internal shape.

    Accurate's live schema varies by tenant; unknown fields are set to None so
    the UI shows 'Perlu verifikasi' rather than guessing.
    """
    so_notes = _extract_so_notes_text(d)
    so_colors = _detect_colors(so_notes)
    lines_raw = d.get("detailItem") or d.get("detailItems") or d.get("items") or []
    lines: List[Dict[str, Any]] = []
    multi_line = len(lines_raw) > 1
    for li in lines_raw:
        item = li.get("item") or {}
        if not isinstance(item, dict):
            item = {}
        # Fields to scan for color, in priority order
        explicit_color = item.get("color") or li.get("color") or li.get("itemColor") or item.get("itemColor") or None
        desc = li.get("description") or li.get("detailNotes") or li.get("itemDesc") or ""
        item_notes = li.get("notes") or li.get("memo") or ""
        item_name_text = item.get("name") or li.get("itemName") or ""

        color_val: Optional[str] = None
        color_source: Optional[str] = None
        color_candidates: List[str] = []
        needs_verify: bool = False

        if explicit_color and str(explicit_color).strip():
            color_val = str(explicit_color).strip()
            color_source = "item.color"
        else:
            # scan item-level free text (desc + notes + item name)
            item_text = " ".join([desc or "", item_notes or "", item_name_text or ""]).strip()
            colors_in_item = _detect_colors(item_text)
            if len(colors_in_item) == 1:
                color_val = colors_in_item[0]
                color_source = "item.description"
            elif len(colors_in_item) > 1:
                color_candidates = colors_in_item
                needs_verify = True
                color_source = "item.description"
            else:
                # Fallback to SO-level memo
                if len(so_colors) == 1 and not multi_line:
                    color_val = so_colors[0]
                    color_source = "so.notes"
                elif so_colors:
                    color_candidates = so_colors
                    needs_verify = True
                    color_source = "so.notes"

        line_notes = (li.get("detailNotes") or li.get("description") or li.get("notes") or None)
        line: Dict[str, Any] = {
            "accurate_item_id": _extract_accurate_item_id(li, item),
            "item_code": item.get("no") or li.get("itemNo") or None,
            "name": item.get("name") or li.get("itemName") or li.get("description") or "-",
            "quantity": int(li.get("quantity") or li.get("qty") or 0),
            "unit": li.get("itemUnitName") or li.get("unit") or "pcs",
            "color": color_val,
            "notes": line_notes,
        }
        if color_candidates:
            line["color_candidates"] = color_candidates
        if needs_verify:
            line["color_needs_verify"] = True
        if color_source:
            line["color_source"] = color_source
        lines.append(line)
    cust = d.get("customer") or {}
    ship = d.get("shipToAddress") or d.get("shipTo") or {}
    salesperson = (d.get("salesman") or {}).get("name") if isinstance(d.get("salesman"), dict) else d.get("salesman")
    created_by = (d.get("createUser") or d.get("createdByUser") or {})
    created_by_name = created_by.get("name") if isinstance(created_by, dict) else created_by
    return {
        "accurate_id": str(d.get("id") or d.get("number") or ""),
        "po_number": str(d.get("number") or d.get("orderNumber") or ""),
        "customer": cust.get("name") if isinstance(cust, dict) else str(cust) if cust else None,
        "outlet": d.get("branchName") or (d.get("branch") or {}).get("name"),
        "recipient": ship.get("attention") or ship.get("name") or None,
        "address": ship.get("address") or d.get("shipAddress") or None,
        "payment_terms": (d.get("paymentTerm") or {}).get("name") if isinstance(d.get("paymentTerm"), dict) else d.get("paymentTerm"),
        "sales": salesperson,
        "created_by_user": created_by_name,
        "expedition": d.get("expedition") or None,
        "delivery_zone": (d.get("shipVia") or {}).get("name") if isinstance(d.get("shipVia"), dict) else d.get("shipVia"),
        "seller": salesperson,
        "order_date_offset_days": 0,
        "line_items": lines,
        "so_notes_raw": so_notes or None,
    }


# -------- Color detection helpers --------
# Standard color palette (Indonesian + English + some brand-common variants)
_COLOR_PATTERNS: List[Tuple[str, List[str]]] = [
    ("PUTIH", ["putih", "white"]),
    ("HITAM", ["hitam", "black"]),
    ("MERAH", ["merah", "red"]),
    ("BIRU", ["biru", "blue", "navy"]),
    ("HIJAU", ["hijau", "green", "tosca", "toska"]),
    ("KUNING", ["kuning", "yellow"]),
    ("ORANGE", ["orange", "oranye", "jingga"]),
    ("UNGU", ["ungu", "purple", "violet", "lilac", "lavender"]),
    ("COKLAT", ["coklat", "cokelat", "brown", "kraft"]),
    ("PINK", ["pink", "merah muda", "peach", "salem"]),
    ("GOLD", ["gold", "emas"]),
    ("SILVER", ["silver", "perak"]),
    ("BENING", ["bening", "transparan", "transparent", "clear"]),
    ("ABU-ABU", ["abu-abu", "abu abu", "grey", "gray"]),
    ("KREM", ["krem", "cream", "ivory", "beige"]),
    ("MAROON", ["maroon", "marun"]),
]


def _detect_colors(text: str) -> List[str]:
    """Detect explicit color mentions in the given text. Returns a
    de-duplicated list of canonical color names (uppercase Indonesian).
    Returns empty list when no color word is present.
    """
    if not text:
        return []
    import re
    hay = text.lower()
    found: List[str] = []
    for canonical, variants in _COLOR_PATTERNS:
        for v in variants:
            # word boundary — avoid matching 'pink' inside 'pinkusan' etc.
            if re.search(rf"(?<![a-z0-9]){re.escape(v)}(?![a-z0-9])", hay):
                if canonical not in found:
                    found.append(canonical)
                break
    return found


def _extract_so_notes_text(d: Dict[str, Any]) -> str:
    """Aggregate SO-level free text where color hints may appear."""
    parts = []
    for key in ("description", "memo", "notes", "poNumberFromCustomer", "orderNotes", "note"):
        v = d.get(key)
        if v:
            parts.append(str(v))
    return " ".join(parts).strip()


# ---- Mock data ----
# Stable Accurate item ids (never names) for mock Master Barang/Jasa + PO lines.
_MOCK_ITEMS: List[Dict[str, Any]] = [
    {"id": 101, "no": "PC-12", "name": "Paper cup 12oz", "itemType": "INVENTORY", "unit1Name": "pcs", "suspended": False},
    {"id": 104, "no": "PC-16", "name": "Paper cup 16oz", "itemType": "INVENTORY", "unit1Name": "pcs", "suspended": False},
    {"id": 105, "no": "PC-8", "name": "Paper cup 8oz", "itemType": "INVENTORY", "unit1Name": "pcs", "suspended": False},
    {"id": 106, "no": "PB-M", "name": "Paper bag medium", "itemType": "INVENTORY", "unit1Name": "pcs", "suspended": False},
    {"id": 202, "no": "LB-M", "name": "Lunchbox medium", "itemType": "INVENTORY", "unit1Name": "pcs", "suspended": False},
    {"id": 203, "no": "JP-1L", "name": "Jumbo plastic 1L", "itemType": "INVENTORY", "unit1Name": "pcs", "suspended": False},
    {"id": 301, "no": "SVC-CETAK", "name": "Jasa cetak sablon", "itemType": "SERVICE", "unit1Name": "pcs", "suspended": False},
    {"id": 302, "no": "PC-12-OLD", "name": "Paper cup 12oz (nonaktif)", "itemType": "INVENTORY", "unit1Name": "pcs", "suspended": True},
]


_MOCK_SALES_ORDERS: List[Dict[str, Any]] = [
    {
        "accurate_id": "ACC-SO-1049",
        "po_number": "PO-1049",
        "customer": "Kopi Rumah",
        "outlet": "Kopi Rumah — Kelapa Gading",
        "recipient": "Dimas Putra",
        "address": "Jl. Boulevard Raya, Kelapa Gading",
        "payment_terms": "Net 14",
        "sales": "Vira",
        "created_by_user": "Rina (Sales Admin)",
        "expedition": "Internal fleet",
        "delivery_zone": "Jakarta Utara",
        "seller": "Vira",
        "order_date_offset_days": 0,
        "line_items": [
            {"accurate_item_id": "101", "item_code": "PC-12", "name": "Paper cup 12oz", "quantity": 900, "unit": "pcs", "color": "Navy / putih", "notes": ""},
        ],
    },
    {
        "accurate_id": "ACC-SO-1050",
        "po_number": "PO-1050",
        "customer": "Sate Padang Uni",
        "outlet": "Sate Padang Uni — Tebet",
        "recipient": "Uni Sari",
        "address": "Jl. Tebet Barat Dalam 8",
        "payment_terms": "Cash on delivery",
        "sales": "Alfi",
        "expedition": "Third-party",
        "delivery_zone": "Jakarta Selatan",
        "seller": "Alfi",
        "order_date_offset_days": 0,
        "line_items": [
            {"accurate_item_id": "202", "item_code": "LB-M", "name": "Lunchbox medium", "quantity": 600, "unit": "pcs", "color": "Merah / krem", "notes": "Logo tengah, ukuran 60mm"},
            {"accurate_item_id": "203", "item_code": "JP-1L", "name": "Jumbo plastic 1L", "quantity": 400, "unit": "pcs", "color": "Krem", "notes": ""},
        ],
    },
    {
        "accurate_id": "ACC-SO-2001",
        "po_number": "PO-2001",
        "customer": "Warung Sate Pak Gino",
        "outlet": "Warung Sate Pak Gino — Cipete",
        "recipient": "Pak Gino",
        "address": "Jl. Cipete Raya No. 22, Jakarta Selatan",
        "payment_terms": "Net 7",
        "sales": "Alfi",
        "created_by_user": "Rina (Sales Admin)",
        "expedition": "Internal fleet",
        "delivery_zone": "Jakarta Selatan",
        "seller": "Alfi",
        "order_date_offset_days": 0,
        "line_items": [
            {"accurate_item_id": "104", "item_code": "PC-16", "name": "Paper cup 16oz", "quantity": 2500, "unit": "pcs", "color": "Merah / kuning", "notes": "Logo pak Gino, tinta food-grade"},
            {"accurate_item_id": "105", "item_code": "PC-8", "name": "Paper cup 8oz", "quantity": 1500, "unit": "pcs", "color": "Merah / kuning", "notes": ""},
        ],
    },
    {
        "accurate_id": "ACC-SO-2002",
        "po_number": "PO-2002",
        "customer": "Roti Sederhana",
        "outlet": "Roti Sederhana — PIK",
        "recipient": "Bu Rini",
        "address": "Ruko PIK Avenue Blok C-14, Jakarta Utara",
        "payment_terms": "Net 30",
        "sales": "Vira",
        "created_by_user": "Rina (Sales Admin)",
        "expedition": "Third-party",
        "delivery_zone": "Jakarta Utara",
        "seller": "Vira",
        "order_date_offset_days": -1,
        "line_items": [
            {"accurate_item_id": "106", "item_code": "PB-M", "name": "Paper bag medium", "quantity": 3000, "unit": "pcs", "color": "Cokelat kraft", "notes": "Logo emboss di depan"},
        ],
    },
]


# Fields that Accurate owns and can safely refresh on existing PO
_SALES_FIELDS = (
    "customer", "outlet", "recipient", "address", "payment_terms",
    "sales", "created_by_user",
    "expedition", "delivery_zone", "seller", "order_date",
    "estimated_delivery", "line_items", "total_quantity", "item_count",
)


async def sync_sales_orders(db, client: AccurateClient) -> Dict[str, Any]:
    """Pull Sales Orders from Accurate (or mock) and upsert into `orders` collection.

    Deduplication: uses `po_number` as the natural key. Never overwrites
    workflow state on existing docs.
    """
    now = datetime.now(timezone.utc)
    src_orders = await client.fetch_sales_orders()

    imported = 0
    updated = 0
    skipped = 0
    imported_pos: List[str] = []
    updated_pos: List[str] = []

    for so in src_orders:
        po_number = so["po_number"]
        existing = await db.orders.find_one({"po_number": po_number})

        offset = so.get("order_date_offset_days", 0)
        order_date = (now + timedelta(days=offset)).isoformat()
        deadline = (now + timedelta(days=offset + 3)).isoformat()

        total_qty = sum(int(li.get("quantity", 0)) for li in so.get("line_items", []))
        try:
            from services.master_items import resolve_po_line_items, MasterItemUnresolvedError
            so["line_items"] = await resolve_po_line_items(
                db, client, so.get("line_items") or [], require_master=True,
            )
        except MasterItemUnresolvedError:
            skipped += 1
            continue
        sales_payload = {
            "customer": so["customer"],
            "outlet": so["outlet"],
            "recipient": so["recipient"],
            "address": so["address"],
            "payment_terms": so["payment_terms"],
            "sales": so.get("sales"),                    # tenaga penjual
            "created_by_user": so.get("created_by_user"),  # pembuat PO / user input di Accurate
            "expedition": so["expedition"],
            "delivery_zone": so["delivery_zone"],
            "seller": so["seller"],
            "order_date": existing["order_date"] if existing else order_date,
            "estimated_delivery": existing["estimated_delivery"] if existing else deadline,
            "line_items": so["line_items"],
            "total_quantity": total_qty,
            "item_count": len(so["line_items"]),
            "updated_at": now_iso(),
            "accurate_synced_at": now_iso(),
            "accurate_source": "mock" if not client.is_live else "live",
        }

        if existing:
            # Only refresh sales-side fields. Preserve all workflow state.
            update = {k: v for k, v in sales_payload.items() if k in _SALES_FIELDS or k in ("updated_at", "accurate_synced_at", "accurate_source")}
            await db.orders.update_one({"po_number": po_number}, {"$set": update})
            updated += 1
            updated_pos.append(po_number)
        else:
            new = {
                "id": str(uuid.uuid4()),
                "po_number": po_number,
                "priority": "normal",
                "urgency_approved_by": None,
                "payment_status": "Waiting",
                "design_status": "Waiting",
                "operator_splits": [],
                "design_files": [],
                "sample_files": [],
                "notes": "",
                "current_stage": 0,
                "status_label": "Order Masuk",
                "packing_status": "Belum",
                "delivery_status": "Belum Terkirim",
                "delivery_note_number": "",
                "loading_checklist": None,
                "timeline": [{
                    "stage": 0,
                    "stage_label": "Order Masuk",
                    "at": now_iso(),
                    "by_email": "accurate-sync",
                    "by_role": "system",
                    "by_name": "Accurate Sync",
                    "note": f"Diimport dari Accurate ({'mock' if not client.is_live else 'live'})",
                }],
                "created_at": now_iso(),
                "deadline": deadline,
                **sales_payload,
            }
            await db.orders.insert_one(new)
            imported += 1
            imported_pos.append(po_number)

    return {
        "status": "ok",
        "mode": "live" if client.is_live else "mock",
        "imported": imported,
        "imported_po_numbers": imported_pos,
        "updated": updated,
        "updated_po_numbers": updated_pos,
        "skipped": skipped,
        "message": (
            f"{imported} PO baru diimport, {updated} PO existing di-refresh (workflow tidak diubah)."
            if imported or updated else "Semua PO sudah sinkron."
        ),
    }
