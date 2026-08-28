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
import random
import time
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

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

# Shared Accurate HTTP policy (Sales Order + session). Item list keeps its own
# bounded retry so existing master-item tests stay stable.
ACCURATE_CONNECT_TIMEOUT_S = 5.0
ACCURATE_READ_TIMEOUT_S = 20.0
ACCURATE_WRITE_TIMEOUT_S = 10.0
ACCURATE_POOL_TIMEOUT_S = 5.0
ACCURATE_MAX_TRANSIENT_ATTEMPTS = 3  # initial try + 2 retries
ACCURATE_BACKOFF_BASE_S = 0.5
ACCURATE_BACKOFF_MAX_S = 4.0
ACCURATE_SESSION_TTL_S = 240
ACCURATE_SO_FETCH_CONCURRENCY = 3
ACCURATE_LOOKUP_CONCURRENCY = 3
ACCURATE_NAME_CACHE_TTL_S = 600.0  # 10 minutes
ACCURATE_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
ACCURATE_BRANCH_DETAIL_PATH = "/accurate/api/branch/detail.do"
# Official OpenAPI (account.accurate.id/open-api/json.do) has no user-by-id
# endpoint. /api/userinfo.do is the current OAuth user only. Do not guess.
ACCURATE_HTTP_LIMITS = httpx.Limits(
    max_keepalive_connections=8,
    max_connections=16,
    keepalive_expiry=30.0,
)
_HTTP_RETRYABLE_EXC = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    ConnectionResetError,
    ConnectionAbortedError,
)

# In-memory TTL cache + single-flight for branch (and any future) id→name lookups.
_NAME_CACHE: Dict[str, Tuple[float, Optional[str]]] = {}
_NAME_INFLIGHT: Dict[str, "asyncio.Future[Optional[str]]"] = {}
_NAME_GUARD = asyncio.Lock()
_SO_INTERNAL_CREATED_BY_ID = "_created_by_id"
_SO_INTERNAL_BRANCH_ID = "_branch_id"


def clear_accurate_name_cache() -> None:
    """Tests only. Drops TTL cache and in-flight lookups."""
    _NAME_CACHE.clear()
    _NAME_INFLIGHT.clear()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccurateErrorCategory:
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    ACCURATE_SERVER_ERROR = "ACCURATE_SERVER_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_USER_MESSAGE = {
    AccurateErrorCategory.TIMEOUT: "Accurate sedang lambat. Silakan coba lagi.",
    AccurateErrorCategory.RATE_LIMITED: "Batas request Accurate sedang tercapai. Sistem akan mencoba kembali.",
    AccurateErrorCategory.TOKEN_EXPIRED: "Sesi Accurate perlu diperbarui.",
    AccurateErrorCategory.UNAUTHORIZED: "Akses ke Accurate ditolak.",
    AccurateErrorCategory.NOT_FOUND: "Sales Order tidak ditemukan di Accurate.",
    AccurateErrorCategory.ACCURATE_SERVER_ERROR: "Accurate sedang bermasalah. Silakan coba lagi.",
    AccurateErrorCategory.CONNECTION_ERROR: "Koneksi ke Accurate terputus. Silakan coba lagi.",
    AccurateErrorCategory.INVALID_RESPONSE: "Data dari Accurate tidak lengkap.",
    AccurateErrorCategory.INTERNAL_ERROR: "Terjadi kesalahan saat memproses Sales Order.",
}

_RETRYABLE_CATEGORIES = frozenset({
    AccurateErrorCategory.TIMEOUT,
    AccurateErrorCategory.RATE_LIMITED,
    AccurateErrorCategory.ACCURATE_SERVER_ERROR,
    AccurateErrorCategory.CONNECTION_ERROR,
    AccurateErrorCategory.TOKEN_EXPIRED,
})


def user_message_for(category: str) -> str:
    return _USER_MESSAGE.get(category, _USER_MESSAGE[AccurateErrorCategory.INTERNAL_ERROR])


class AccurateRequestError(Exception):
    """Typed Accurate failure. str() is always the safe user-facing message."""

    def __init__(
        self,
        category: str,
        *,
        technical_message: str = "",
        http_status: Optional[int] = None,
        retryable: Optional[bool] = None,
        user_message: Optional[str] = None,
    ) -> None:
        self.category = category or AccurateErrorCategory.INTERNAL_ERROR
        self.technical_message = str(technical_message or "")[:180]
        self.http_status = http_status
        self.retryable = (self.category in _RETRYABLE_CATEGORIES) if retryable is None else bool(retryable)
        self.user_message = user_message or user_message_for(self.category)
        self.stale_session = False
        super().__init__(self.user_message)

    def to_result(self, so_number: str) -> Dict[str, Any]:
        return so_fetch_result(
            so_number,
            success=False,
            error_code=self.category,
            user_message=self.user_message,
            retryable=self.retryable,
            technical_message=self.technical_message,
        )


def so_fetch_result(
    so_number: str,
    *,
    success: bool,
    data: Optional[Dict[str, Any]] = None,
    error_code: Optional[str] = None,
    user_message: Optional[str] = None,
    retryable: bool = False,
    technical_message: str = "",
) -> Dict[str, Any]:
    return {
        "success": bool(success),
        "sales_order_number": so_number,
        "so_number": so_number,
        "data": data,
        "error_code": error_code,
        "user_message": user_message,
        "retryable": bool(retryable),
        "technical_message": technical_message[:180] if technical_message else "",
    }


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


def accurate_http_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=ACCURATE_CONNECT_TIMEOUT_S,
        read=ACCURATE_READ_TIMEOUT_S,
        write=ACCURATE_WRITE_TIMEOUT_S,
        pool=ACCURATE_POOL_TIMEOUT_S,
    )


def _backoff_seconds(attempt: int, retry_after: Optional[float] = None) -> float:
    if retry_after is not None:
        return min(max(0.0, retry_after), ACCURATE_BACKOFF_MAX_S * 2)
    exp = ACCURATE_BACKOFF_BASE_S * (2 ** max(0, attempt - 1))
    jitter = random.uniform(0.0, exp * 0.25)
    return min(exp + jitter, ACCURATE_BACKOFF_MAX_S)


def _parse_retry_after(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _classify_http_status(status: int) -> str:
    if status in (401,):
        return AccurateErrorCategory.TOKEN_EXPIRED
    if status in (403,):
        return AccurateErrorCategory.UNAUTHORIZED
    if status == 404:
        return AccurateErrorCategory.NOT_FOUND
    if status == 429:
        return AccurateErrorCategory.RATE_LIMITED
    if status in (500, 502, 503, 504):
        return AccurateErrorCategory.ACCURATE_SERVER_ERROR
    if status >= 400:
        return AccurateErrorCategory.INVALID_RESPONSE
    return AccurateErrorCategory.INTERNAL_ERROR


def _classify_exception(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return AccurateErrorCategory.TIMEOUT
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, ConnectionResetError, ConnectionAbortedError)):
        return AccurateErrorCategory.CONNECTION_ERROR
    return AccurateErrorCategory.INTERNAL_ERROR


# Phrase lists for Accurate {s:false} bodies. Do not match bare "auth" / "session".
_TOKEN_EXPIRED_PHRASES = (
    "token expired", "invalid token", "access token", "unauthenticated",
    "not logged", "login required", "token tidak valid", "token habis",
)
_SESSION_STALE_PHRASES = (
    "session expired", "invalid session", "no session", "x-session",
    "session id", "sesi habis", "sesi telah habis", "sesi tidak valid",
    "sesi anda", "session tidak",
)
_FORBIDDEN_PHRASES = (
    "forbidden", "unauthorized", "unauthorised", "access denied",
    "permission denied", "tidak berhak", "tidak punya hak",
    "tidak diizinkan", "permission", "not authorized", "not authorised",
)


def _text_has_phrase(text: str, phrases: Tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(p in low for p in phrases)


def _is_session_stale_text(text: str) -> bool:
    return _text_has_phrase(text, _SESSION_STALE_PHRASES)


def _classify_s_false(payload: Any) -> str:
    """Classify Accurate {s:false} without treating permission errors as token expiry.

    Bare substrings like 'auth' or 'session' are intentionally not used — they
    match permanent forbidden messages such as 'not authorized'.
    """
    low = _safe_s_false_detail(payload).lower()
    if _is_session_stale_text(low):
        return AccurateErrorCategory.TOKEN_EXPIRED
    if _text_has_phrase(low, _TOKEN_EXPIRED_PHRASES) or (
        "token" in low and any(k in low for k in ("expired", "invalid", "habis"))
    ):
        return AccurateErrorCategory.TOKEN_EXPIRED
    if _text_has_phrase(low, _FORBIDDEN_PHRASES):
        return AccurateErrorCategory.UNAUTHORIZED
    if any(k in low for k in ("not found", "tidak ditemukan", "no data")):
        return AccurateErrorCategory.NOT_FOUND
    return AccurateErrorCategory.INVALID_RESPONSE


def _safe_s_false_detail(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    d = payload.get("d")
    if isinstance(d, list) and d:
        return str(d[0])[:180]
    if isinstance(d, str):
        return d[:180]
    return str(payload.get("message") or "")[:180]


def coerce_accurate_quantity(val: Any) -> int:
    """Parse Accurate qty without crashing. Unparseable / negative → 0."""
    if val is None or val is False or val is True:
        return 0
    if isinstance(val, int):
        return val if val > 0 else 0
    if isinstance(val, float):
        if val != val or val <= 0:  # NaN
            return 0
        return int(val)
    s = str(val).strip().replace(",", "")
    if not s:
        return 0
    try:
        n = float(s)
    except (TypeError, ValueError):
        return 0
    if n != n or n <= 0:
        return 0
    return int(n)


def validate_normalized_so(so: Optional[Dict[str, Any]]) -> None:
    """Raise AccurateRequestError(INVALID_RESPONSE) when required import fields are missing."""
    if not isinstance(so, dict):
        raise AccurateRequestError(
            AccurateErrorCategory.INVALID_RESPONSE,
            technical_message="normalized SO is not an object",
            retryable=False,
        )
    if not str(so.get("po_number") or "").strip():
        raise AccurateRequestError(
            AccurateErrorCategory.INVALID_RESPONSE,
            technical_message="missing po_number",
            retryable=False,
        )
    if not str(so.get("customer") or "").strip():
        raise AccurateRequestError(
            AccurateErrorCategory.INVALID_RESPONSE,
            technical_message="missing customer",
            retryable=False,
        )
    lines = so.get("line_items") or []
    if not isinstance(lines, list) or not lines:
        raise AccurateRequestError(
            AccurateErrorCategory.INVALID_RESPONSE,
            technical_message="missing detail items",
            retryable=False,
        )
    for i, li in enumerate(lines):
        if not isinstance(li, dict):
            raise AccurateRequestError(
                AccurateErrorCategory.INVALID_RESPONSE,
                technical_message=f"line {i} is not an object",
                retryable=False,
            )
        if not str(li.get("accurate_item_id") or "").strip():
            raise AccurateRequestError(
                AccurateErrorCategory.INVALID_RESPONSE,
                technical_message=f"line {i} missing item identifier",
                retryable=False,
            )
        if coerce_accurate_quantity(li.get("quantity")) <= 0:
            raise AccurateRequestError(
                AccurateErrorCategory.INVALID_RESPONSE,
                technical_message=f"line {i} missing/invalid quantity",
                retryable=False,
            )


def _log_accurate_request(
    *,
    correlation_id: str,
    endpoint: str,
    so_number: str = "",
    http_status: Optional[int] = None,
    duration_ms: int = 0,
    retry_count: int = 0,
    error_category: str = "",
) -> None:
    line = (
        "accurate_request correlation_id=%s endpoint=%s so_number=%s http_status=%s duration_ms=%s retry_count=%s error_category=%s"
    )
    args = (
        correlation_id or "-",
        endpoint or "-",
        so_number or "-",
        "-" if http_status is None else http_status,
        duration_ms,
        retry_count,
        error_category or "ok",
    )
    if error_category:
        logger.warning(line, *args)
    else:
        logger.info(line, *args)


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
        # In-memory token/session (never persisted). Locks prevent stampede refresh.
        self._token_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._cached_access_token = self.access_token
        self._token_version = 0
        self._cached_host: Optional[str] = None
        self._cached_db_session: Optional[str] = None
        self._session_cached_at = 0.0

    def _invalidate_db_session(self) -> None:
        self._cached_host = None
        self._cached_db_session = None
        self._session_cached_at = 0.0

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
    async def _obtain_token(self, *, force_refresh: bool = False) -> str:
        # API Token: long-lived bearer, no refresh flow.
        if self.api_token:
            return self.api_token
        async with self._token_lock:
            if not force_refresh and self._cached_access_token:
                return self._cached_access_token
            if not force_refresh and self.access_token:
                self._cached_access_token = self.access_token
                return self._cached_access_token
            if not self.refresh_token:
                raise AccurateRequestError(
                    AccurateErrorCategory.UNAUTHORIZED,
                    technical_message="no refresh token configured",
                    retryable=False,
                )
            token = await self._refresh_oauth_unlocked()
            return token

    async def _refresh_oauth_if_stale(self, failed_version: int) -> str:
        """Single-flight OAuth refresh. Concurrent 401s share one refresh."""
        async with self._token_lock:
            if self._token_version > failed_version and self._cached_access_token:
                return self._cached_access_token
            if not self.refresh_token:
                raise AccurateRequestError(
                    AccurateErrorCategory.UNAUTHORIZED,
                    technical_message="refresh token missing",
                    retryable=False,
                )
            return await self._refresh_oauth_unlocked()

    async def _refresh_oauth_unlocked(self) -> str:
        """Caller must hold `_token_lock`. Never logs token values."""
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        url = f"{self.account_base_url}/oauth/token"
        last_err: Optional[AccurateRequestError] = None
        async with httpx.AsyncClient(
            timeout=accurate_http_timeout(),
            follow_redirects=True,
            limits=ACCURATE_HTTP_LIMITS,
        ) as client:
            for attempt in range(1, ACCURATE_MAX_TRANSIENT_ATTEMPTS + 1):
                t0 = time.perf_counter()
                try:
                    r = await client.post(
                        url,
                        headers={"Authorization": f"Basic {basic}"},
                        data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
                    )
                except _HTTP_RETRYABLE_EXC as e:
                    cat = _classify_exception(e)
                    last_err = AccurateRequestError(cat, technical_message=type(e).__name__)
                    _log_accurate_request(
                        correlation_id="-",
                        endpoint="oauth/token",
                        http_status=None,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        retry_count=attempt - 1,
                        error_category=cat,
                    )
                    if attempt < ACCURATE_MAX_TRANSIENT_ATTEMPTS:
                        await asyncio.sleep(_backoff_seconds(attempt))
                        continue
                    raise last_err from e
                duration_ms = int((time.perf_counter() - t0) * 1000)
                if r.status_code in ACCURATE_TRANSIENT_STATUS:
                    cat = _classify_http_status(r.status_code)
                    last_err = AccurateRequestError(cat, http_status=r.status_code, technical_message=f"oauth HTTP {r.status_code}")
                    _log_accurate_request(
                        correlation_id="-",
                        endpoint="oauth/token",
                        http_status=r.status_code,
                        duration_ms=duration_ms,
                        retry_count=attempt - 1,
                        error_category=cat,
                    )
                    if attempt < ACCURATE_MAX_TRANSIENT_ATTEMPTS:
                        wait = _parse_retry_after(r) if r.status_code == 429 else None
                        await asyncio.sleep(_backoff_seconds(attempt, wait))
                        continue
                    raise last_err
                _log_accurate_request(
                    correlation_id="-",
                    endpoint="oauth/token",
                    http_status=r.status_code,
                    duration_ms=duration_ms,
                    retry_count=attempt - 1,
                    error_category="" if not r.is_error else AccurateErrorCategory.TOKEN_EXPIRED,
                )
                if r.is_error:
                    raise AccurateRequestError(
                        AccurateErrorCategory.TOKEN_EXPIRED,
                        http_status=r.status_code,
                        technical_message="token refresh failed",
                        retryable=False,
                    )
                try:
                    body = r.json()
                except Exception as e:
                    raise AccurateRequestError(
                        AccurateErrorCategory.INVALID_RESPONSE,
                        technical_message="token refresh invalid JSON",
                        retryable=False,
                    ) from e
                token = (body or {}).get("access_token") if isinstance(body, dict) else None
                if not token:
                    raise AccurateRequestError(
                        AccurateErrorCategory.UNAUTHORIZED,
                        technical_message="token refresh missing access_token",
                        retryable=False,
                    )
                self._cached_access_token = str(token)
                self.access_token = self._cached_access_token
                rotated = (body or {}).get("refresh_token") if isinstance(body, dict) else None
                if rotated:
                    self.refresh_token = str(rotated)
                self._token_version += 1
                self._invalidate_db_session()
                return self._cached_access_token
        raise last_err or AccurateRequestError(
            AccurateErrorCategory.UNAUTHORIZED,
            technical_message="token refresh failed",
            retryable=False,
        )

    async def _http_get_retries(
        self,
        http: httpx.AsyncClient,
        url: str,
        *,
        headers_fn: Callable[[], Dict[str, str]],
        params: Optional[Dict[str, Any]] = None,
        logical_name: str,
        so_number: str = "",
        correlation_id: str = "",
        max_attempts: int = ACCURATE_MAX_TRANSIENT_ATTEMPTS,
    ) -> httpx.Response:
        """GET with transient retry. Regenerates signed headers every attempt.

        Does not retry permanent 4xx (except 429). 401/403 abort the loop so
        the caller can refresh OAuth at most once.
        """
        last_err: Optional[AccurateRequestError] = None
        attempts = max(1, int(max_attempts or 1))
        for attempt in range(1, attempts + 1):
            t0 = time.perf_counter()
            headers = headers_fn()
            try:
                r = await http.get(url, headers=headers, params=params)
            except _HTTP_RETRYABLE_EXC as e:
                cat = _classify_exception(e)
                last_err = AccurateRequestError(cat, technical_message=type(e).__name__)
                _log_accurate_request(
                    correlation_id=correlation_id,
                    endpoint=logical_name,
                    so_number=so_number,
                    http_status=None,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    retry_count=attempt - 1,
                    error_category=cat,
                )
                if attempt < attempts:
                    await asyncio.sleep(_backoff_seconds(attempt))
                    continue
                raise last_err from e
            except httpx.HTTPError as e:
                cat = AccurateErrorCategory.CONNECTION_ERROR
                _log_accurate_request(
                    correlation_id=correlation_id,
                    endpoint=logical_name,
                    so_number=so_number,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    retry_count=attempt - 1,
                    error_category=cat,
                )
                raise AccurateRequestError(cat, technical_message=type(e).__name__, retryable=False) from e

            duration_ms = int((time.perf_counter() - t0) * 1000)
            status = r.status_code

            if status in ACCURATE_TRANSIENT_STATUS:
                cat = _classify_http_status(status)
                last_err = AccurateRequestError(cat, http_status=status, technical_message=f"HTTP {status}")
                _log_accurate_request(
                    correlation_id=correlation_id,
                    endpoint=logical_name,
                    so_number=so_number,
                    http_status=status,
                    duration_ms=duration_ms,
                    retry_count=attempt - 1,
                    error_category=cat,
                )
                if attempt < attempts:
                    wait = _parse_retry_after(r) if status == 429 else None
                    await asyncio.sleep(_backoff_seconds(attempt, wait))
                    continue
                raise last_err

            if status in (401, 403):
                # api_token: a stale HMAC timestamp can 401; retry with a fresh signature.
                # HTTP 403 is permission/forbidden — never retried, never treated as expiry.
                if self.auth_mode == "api_token" and status == 401 and attempt < attempts:
                    _log_accurate_request(
                        correlation_id=correlation_id,
                        endpoint=logical_name,
                        so_number=so_number,
                        http_status=status,
                        duration_ms=duration_ms,
                        retry_count=attempt - 1,
                        error_category=AccurateErrorCategory.TOKEN_EXPIRED,
                    )
                    await asyncio.sleep(_backoff_seconds(attempt))
                    continue
                cat = _classify_http_status(status)
                _log_accurate_request(
                    correlation_id=correlation_id,
                    endpoint=logical_name,
                    so_number=so_number,
                    http_status=status,
                    duration_ms=duration_ms,
                    retry_count=attempt - 1,
                    error_category=cat,
                )
                raise AccurateRequestError(cat, http_status=status, technical_message=f"HTTP {status}", retryable=False)

            _log_accurate_request(
                correlation_id=correlation_id,
                endpoint=logical_name,
                so_number=so_number,
                http_status=status,
                duration_ms=duration_ms,
                retry_count=attempt - 1,
                error_category="" if not r.is_error else _classify_http_status(status),
            )
            if status == 404:
                raise AccurateRequestError(
                    AccurateErrorCategory.NOT_FOUND,
                    http_status=404,
                    technical_message="HTTP 404",
                    retryable=False,
                )
            if r.is_error:
                raise AccurateRequestError(
                    _classify_http_status(status),
                    http_status=status,
                    technical_message=f"HTTP {status}",
                    retryable=False,
                )
            return r
        raise last_err or AccurateRequestError(
            AccurateErrorCategory.INTERNAL_ERROR,
            technical_message="request failed",
        )

    def _rebase_accurate_url(self, url: str, host: str) -> str:
        """Keep the Accurate API path when the database host changes after reopen."""
        host = (host or "").rstrip("/")
        marker = "/accurate/api/"
        if host and marker in url:
            return host + marker + url.split(marker, 1)[1]
        return url

    async def _authed_get(
        self,
        http: httpx.AsyncClient,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        logical_name: str,
        so_number: str = "",
        correlation_id: str = "",
        allow_token_refresh: bool = True,
        max_attempts: int = ACCURATE_MAX_TRANSIENT_ATTEMPTS,
    ) -> httpx.Response:
        token_version = self._token_version

        def _headers() -> Dict[str, str]:
            # Headers rebuilt every attempt so HMAC timestamps stay fresh.
            token = self.api_token or self._cached_access_token or self.access_token
            return self._request_headers(token, self._cached_db_session or "")

        try:
            return await self._http_get_retries(
                http, url, headers_fn=_headers, params=params,
                logical_name=logical_name, so_number=so_number, correlation_id=correlation_id,
                max_attempts=max_attempts,
            )
        except AccurateRequestError as e:
            # HTTP 403 / UNAUTHORIZED is permanent forbidden — do not refresh.
            if (
                allow_token_refresh
                and e.category == AccurateErrorCategory.TOKEN_EXPIRED
                and self.auth_mode == "oauth"
            ):
                await self._refresh_oauth_if_stale(token_version)
                new_host, _ = await self._ensure_db_session()
                url = self._rebase_accurate_url(url, new_host)
                return await self._http_get_retries(
                    http, url, headers_fn=_headers, params=params,
                    logical_name=logical_name, so_number=so_number, correlation_id=correlation_id,
                    max_attempts=1,
                )
            raise

    async def _open_db(self, token: str) -> Tuple[str, str]:
        """Return (host, session) for the configured database.

        - api_token mode: use GET /api/api-token.do to resolve the host; session
          is not required for signed API-Token requests, so we return an empty
          string and downstream requests won't send X-Session-ID.
        - oauth mode: legacy open-db.do flow returns host + session.
        """
        async with httpx.AsyncClient(
            timeout=accurate_http_timeout(),
            follow_redirects=True,
            limits=ACCURATE_HTTP_LIMITS,
        ) as http:
            if self.auth_mode == "api_token":
                r = await self._http_get_retries(
                    http,
                    f"{self.account_base_url}/api/api-token.do",
                    headers_fn=lambda: self._signed_headers(token),
                    logical_name="api-token.do",
                )
                try:
                    body = r.json()
                except Exception as e:
                    raise AccurateRequestError(
                        AccurateErrorCategory.INVALID_RESPONSE,
                        technical_message="api-token.do invalid JSON",
                    ) from e
                if not isinstance(body, dict):
                    raise AccurateRequestError(
                        AccurateErrorCategory.INVALID_RESPONSE,
                        technical_message="api-token.do not an object",
                    )
                if body.get("s") is False:
                    raise AccurateRequestError(
                        _classify_s_false(body),
                        technical_message=_safe_s_false_detail(body) or "api-token.do s:false",
                        retryable=False,
                    )
                d = body.get("d") if isinstance(body.get("d"), dict) else {}
                database = d.get("database") if isinstance(d.get("database"), dict) else {}
                host = database.get("host") or ""
                if not host:
                    raise AccurateRequestError(
                        AccurateErrorCategory.INVALID_RESPONSE,
                        technical_message="api-token.do missing host",
                        retryable=False,
                    )
                return host.rstrip("/"), ""

            r = await self._http_get_retries(
                http,
                f"{self.account_base_url}/api/open-db.do",
                headers_fn=lambda: {"Authorization": f"Bearer {token}"},
                params={"id": self.db_id},
                logical_name="open-db.do",
            )
            try:
                body = r.json()
            except Exception as e:
                raise AccurateRequestError(
                    AccurateErrorCategory.INVALID_RESPONSE,
                    technical_message="open-db.do invalid JSON",
                ) from e
            if not isinstance(body, dict):
                raise AccurateRequestError(
                    AccurateErrorCategory.INVALID_RESPONSE,
                    technical_message="open-db.do not an object",
                )
            host = body.get("host") or ""
            session = body.get("session") or ""
            if not host or not session:
                nested = body.get("data") if isinstance(body.get("data"), dict) else {}
                host = host or nested.get("host") or ""
                session = session or nested.get("session") or ""
            if not host or not session:
                raise AccurateRequestError(
                    AccurateErrorCategory.INVALID_RESPONSE,
                    technical_message="open-db missing host/session",
                    retryable=False,
                )
            return host.rstrip("/"), session

    def _request_headers(self, token: str, session: str = "") -> Dict[str, str]:
        """Same Authorization / signature / session headers used by Import PO."""
        if self.auth_mode == "api_token":
            return self._signed_headers(token)
        return {"Authorization": f"Bearer {token}", "X-Session-ID": session}

    async def _ensure_db_session(self, *, force: bool = False) -> Tuple[str, str]:
        """Cached (host, session). Concurrent callers share one open-db/api-token.do."""
        async with self._session_lock:
            now = time.monotonic()
            if (
                not force
                and self._cached_host
                and (now - self._session_cached_at) < ACCURATE_SESSION_TTL_S
            ):
                return self._cached_host, self._cached_db_session or ""
            token_version = self._token_version
            token = await self._obtain_token()
            try:
                host, session = await self._open_db(token)
            except AccurateRequestError as e:
                if e.category == AccurateErrorCategory.TOKEN_EXPIRED and self.auth_mode == "oauth":
                    token = await self._refresh_oauth_if_stale(token_version)
                    host, session = await self._open_db(token)
                else:
                    raise
            self._cached_host = host
            self._cached_db_session = session
            self._session_cached_at = time.monotonic()
            self.host = host or self.host
            return host, session

    async def _live_session(self) -> Tuple[str, Dict[str, str]]:
        """Return (host, headers) for Accurate API calls. Reused by SO + item."""
        host, session = await self._ensure_db_session()
        token = await self._obtain_token()
        return host, self._request_headers(token, session)

    def _so_detail_params(self, so_number: str) -> Dict[str, str]:
        return {"id": so_number} if so_number.isdigit() else {"number": so_number}

    def _extract_so_body(self, payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        if payload.get("s") is False:
            detail = _safe_s_false_detail(payload) or "sales-order/detail.do s:false"
            err = AccurateRequestError(
                _classify_s_false(payload),
                technical_message=detail,
                retryable=False,
            )
            err.stale_session = _is_session_stale_text(detail)
            raise err
        d = payload.get("d") if isinstance(payload.get("d"), dict) else None
        if d is None:
            d = payload.get("data") if isinstance(payload.get("data"), dict) else None
        if d is None:
            d = payload.get("r") if isinstance(payload.get("r"), dict) else None
        return d if isinstance(d, dict) else None

    async def _lookup_cached_name(
        self,
        cache_key: str,
        fetcher: Callable[[], Any],
    ) -> Optional[str]:
        """Return cached name or run fetcher once (single-flight). Never raises."""
        now = time.monotonic()
        hit = _NAME_CACHE.get(cache_key)
        if hit and hit[0] > now:
            return hit[1]
        async with _NAME_GUARD:
            hit = _NAME_CACHE.get(cache_key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
            existing = _NAME_INFLIGHT.get(cache_key)
            if existing is not None:
                wait_fut = existing
                starter = False
            else:
                wait_fut = asyncio.get_running_loop().create_future()
                _NAME_INFLIGHT[cache_key] = wait_fut
                starter = True
        if not starter:
            try:
                return await wait_fut
            except Exception:
                return None
        try:
            name, store = await fetcher()
            if store:
                _NAME_CACHE[cache_key] = (time.monotonic() + ACCURATE_NAME_CACHE_TTL_S, name)
            if not wait_fut.done():
                wait_fut.set_result(name)
            return name
        except Exception as exc:
            logger.warning(
                "accurate_request endpoint=%s error_category=%s exception=%s",
                cache_key.split(":", 1)[0] or "-",
                AccurateErrorCategory.INTERNAL_ERROR,
                type(exc).__name__,
            )
            if not wait_fut.done():
                wait_fut.set_result(None)
            return None
        finally:
            _NAME_INFLIGHT.pop(cache_key, None)

    async def _fetch_branch_name(
        self,
        http: httpx.AsyncClient,
        host: str,
        branch_id: str,
        *,
        correlation_id: str,
    ) -> Tuple[Optional[str], bool]:
        """GET official /accurate/api/branch/detail.do?id=. Returns (name, cache?)."""
        url = f"{host.rstrip('/')}{ACCURATE_BRANCH_DETAIL_PATH}"
        try:
            r = await self._authed_get(
                http, url,
                params={"id": branch_id},
                logical_name="branch/detail.do",
                correlation_id=correlation_id,
            )
        except AccurateRequestError as e:
            logger.warning(
                "accurate_request correlation_id=%s endpoint=branch/detail.do so_number=- http_status=%s duration_ms=0 retry_count=0 error_category=%s",
                correlation_id or "-",
                "-" if e.http_status is None else e.http_status,
                e.category,
            )
            return None, (not e.retryable)
        try:
            payload = r.json()
        except Exception:
            logger.warning(
                "accurate_request correlation_id=%s endpoint=branch/detail.do so_number=- http_status=%s duration_ms=0 retry_count=0 error_category=%s",
                correlation_id or "-",
                r.status_code,
                AccurateErrorCategory.INVALID_RESPONSE,
            )
            return None, True
        if not isinstance(payload, dict):
            return None, True
        if payload.get("s") is False:
            cat = _classify_s_false(payload)
            logger.warning(
                "accurate_request correlation_id=%s endpoint=branch/detail.do so_number=- http_status=%s duration_ms=0 retry_count=0 error_category=%s",
                correlation_id or "-",
                r.status_code,
                cat,
            )
            return None, (cat not in _RETRYABLE_CATEGORIES)
        body = _item_detail_body(payload)
        if not isinstance(body, dict):
            return None, True
        name = _accurate_display_name(body) or _blank_to_none(body.get("branchName"))
        return name, True

    async def _enrich_creator_and_outlet(
        self,
        http: httpx.AsyncClient,
        host: str,
        results: List[Dict[str, Any]],
        *,
        correlation_id: str,
    ) -> None:
        """Resolve unique branchId via branch/detail.do. Creator has no official user-by-id API."""
        need_branch: Dict[str, List[Dict[str, Any]]] = {}
        for row in results:
            if not row.get("success"):
                continue
            so = row.get("data")
            if not isinstance(so, dict):
                continue
            if not so.get("outlet"):
                bid = str(so.get(_SO_INTERNAL_BRANCH_ID) or "").strip()
                if bid:
                    need_branch.setdefault(bid, []).append(so)

        sem = asyncio.Semaphore(ACCURATE_LOOKUP_CONCURRENCY)

        async def _one_branch(bid: str) -> Tuple[str, Optional[str]]:
            async with sem:
                name = await self._lookup_cached_name(
                    f"branch:{bid}",
                    lambda: self._fetch_branch_name(http, host, bid, correlation_id=correlation_id),
                )
                return bid, name

        if need_branch:
            pairs = await asyncio.gather(
                *[_one_branch(bid) for bid in need_branch],
                return_exceptions=True,
            )
            for item in pairs:
                if isinstance(item, Exception):
                    logger.warning(
                        "accurate_request correlation_id=%s endpoint=branch/detail.do so_number=- error_category=%s exception=%s",
                        correlation_id or "-",
                        AccurateErrorCategory.INTERNAL_ERROR,
                        type(item).__name__,
                    )
                    continue
                bid, name = item
                if name:
                    for so in need_branch.get(bid, []):
                        if not so.get("outlet"):
                            so["outlet"] = name

        for row in results:
            so = row.get("data") if row.get("success") else None
            if isinstance(so, dict):
                so.pop(_SO_INTERNAL_CREATED_BY_ID, None)
                so.pop(_SO_INTERNAL_BRANCH_ID, None)

    async def _fetch_so_on_http(
        self,
        http: httpx.AsyncClient,
        so_number: str,
        host: str,
        *,
        correlation_id: str,
        session_retry: bool = True,
    ) -> Dict[str, Any]:
        url = f"{host.rstrip('/')}/accurate/api/sales-order/detail.do"
        try:
            r = await self._authed_get(
                http, url,
                params=self._so_detail_params(so_number),
                logical_name="sales-order/detail.do",
                so_number=so_number,
                correlation_id=correlation_id,
                allow_token_refresh=session_retry,
                max_attempts=ACCURATE_MAX_TRANSIENT_ATTEMPTS if session_retry else 1,
            )
        except AccurateRequestError as e:
            return e.to_result(so_number)
        except Exception as e:
            err = AccurateRequestError(
                AccurateErrorCategory.INTERNAL_ERROR,
                technical_message=type(e).__name__,
            )
            logger.warning(
                "accurate_request correlation_id=%s endpoint=sales-order/detail.do so_number=%s error_category=%s exception=%s",
                correlation_id or "-", so_number, err.category, type(e).__name__,
            )
            return err.to_result(so_number)
        try:
            payload = r.json()
        except Exception:
            return AccurateRequestError(
                AccurateErrorCategory.INVALID_RESPONSE,
                technical_message="detail.do invalid JSON",
                retryable=False,
            ).to_result(so_number)
        try:
            d = self._extract_so_body(payload)
        except AccurateRequestError as e:
            if session_retry and e.stale_session:
                self._invalidate_db_session()
                new_host, _ = await self._ensure_db_session(force=True)
                return await self._fetch_so_on_http(
                    http, so_number, new_host, correlation_id=correlation_id, session_retry=False,
                )
            return e.to_result(so_number)
        if d is None:
            return AccurateRequestError(
                AccurateErrorCategory.NOT_FOUND,
                technical_message="detail.do empty body",
                retryable=False,
            ).to_result(so_number)
        try:
            so = _normalize_accurate_so(d)
            validate_normalized_so(so)
        except AccurateRequestError as e:
            return e.to_result(so_number)
        except Exception as e:
            return AccurateRequestError(
                AccurateErrorCategory.INVALID_RESPONSE,
                technical_message=type(e).__name__,
                retryable=False,
            ).to_result(so_number)
        return so_fetch_result(so_number, success=True, data=so)

    async def _live_fetch_so(self, so_number: str) -> Optional[Dict[str, Any]]:
        rows = await self.fetch_sales_order_results([so_number])
        row = rows[0] if rows else so_fetch_result(
            so_number, success=False,
            error_code=AccurateErrorCategory.INTERNAL_ERROR,
            user_message=user_message_for(AccurateErrorCategory.INTERNAL_ERROR),
        )
        if row.get("success"):
            return row.get("data")
        if row.get("error_code") == AccurateErrorCategory.NOT_FOUND:
            return None
        raise AccurateRequestError(
            row.get("error_code") or AccurateErrorCategory.INTERNAL_ERROR,
            technical_message=row.get("technical_message") or "",
            retryable=bool(row.get("retryable")),
            user_message=row.get("user_message") or user_message_for(AccurateErrorCategory.INTERNAL_ERROR),
        )

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
                found = dict(so)
                validate_normalized_so(found)
                return found
        return None

    async def fetch_sales_order_results(
        self,
        so_numbers: List[str],
        *,
        correlation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch many Sales Orders with bounded concurrency. Never fails the batch.

        One SO error is isolated. Live mode opens the Accurate DB session once
        and reuses a pooled HTTP client.
        """
        cid = correlation_id or new_correlation_id()
        numbers = [str(n).strip() for n in (so_numbers or []) if str(n).strip()]
        if not numbers:
            return []
        if not self.is_live:
            out: List[Dict[str, Any]] = []
            for sn in numbers:
                try:
                    so = await self.fetch_single_sales_order(sn)
                except AccurateRequestError as e:
                    out.append(e.to_result(sn))
                    continue
                except Exception as e:
                    out.append(AccurateRequestError(
                        AccurateErrorCategory.INTERNAL_ERROR,
                        technical_message=type(e).__name__,
                    ).to_result(sn))
                    continue
                if not so:
                    out.append(AccurateRequestError(
                        AccurateErrorCategory.NOT_FOUND,
                        retryable=False,
                    ).to_result(sn))
                else:
                    out.append(so_fetch_result(sn, success=True, data=so))
            return out

        try:
            host, _session = await self._ensure_db_session()
        except AccurateRequestError as e:
            return [e.to_result(sn) for sn in numbers]
        except Exception as e:
            err = AccurateRequestError(
                AccurateErrorCategory.INTERNAL_ERROR,
                technical_message=type(e).__name__,
            )
            return [err.to_result(sn) for sn in numbers]

        sem = asyncio.Semaphore(ACCURATE_SO_FETCH_CONCURRENCY)

        async def _one(sn: str) -> Dict[str, Any]:
            async with sem:
                return await self._fetch_so_on_http(http, sn, host, correlation_id=cid)

        async with httpx.AsyncClient(
            timeout=accurate_http_timeout(),
            follow_redirects=True,
            limits=ACCURATE_HTTP_LIMITS,
        ) as http:
            parts = await asyncio.gather(*[_one(n) for n in numbers], return_exceptions=True)
            out = []
            for sn, part in zip(numbers, parts):
                if isinstance(part, AccurateRequestError):
                    out.append(part.to_result(sn))
                elif isinstance(part, Exception):
                    out.append(AccurateRequestError(
                        AccurateErrorCategory.INTERNAL_ERROR,
                        technical_message=type(part).__name__,
                    ).to_result(sn))
                else:
                    out.append(part)
            try:
                await self._enrich_creator_and_outlet(http, host, out, correlation_id=cid)
            except Exception as e:
                logger.warning(
                    "accurate_request correlation_id=%s endpoint=branch/detail.do so_number=- error_category=%s exception=%s",
                    cid,
                    AccurateErrorCategory.INTERNAL_ERROR,
                    type(e).__name__,
                )
                for row in out:
                    so = row.get("data") if isinstance(row, dict) else None
                    if isinstance(so, dict):
                        so.pop(_SO_INTERNAL_CREATED_BY_ID, None)
                        so.pop(_SO_INTERNAL_BRANCH_ID, None)
            return out

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

        cid = new_correlation_id()
        try:
            host, _session = await self._ensure_db_session()
        except AccurateRequestError:
            if entry:
                return {**entry["data"], "cached": True, "stale": True, "fetched_at": entry["at"]}
            raise

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

        async with httpx.AsyncClient(
            timeout=accurate_http_timeout(),
            follow_redirects=True,
            limits=ACCURATE_HTTP_LIMITS,
        ) as http:
            try:
                r = await self._authed_get(
                    http,
                    f"{host}/accurate/api/sales-order/list.do",
                    params=list_params,
                    logical_name="sales-order/list.do",
                    correlation_id=cid,
                )
            except AccurateRequestError:
                if entry:
                    return {**entry["data"], "cached": True, "stale": True, "fetched_at": entry["at"]}
                raise
            try:
                body = r.json()
            except Exception as e:
                if entry:
                    return {**entry["data"], "cached": True, "stale": True, "fetched_at": entry["at"]}
                raise AccurateRequestError(
                    AccurateErrorCategory.INVALID_RESPONSE,
                    technical_message="list.do invalid JSON",
                ) from e
            if not isinstance(body, dict) or body.get("s") is False:
                err_msg = _safe_s_false_detail(body) if isinstance(body, dict) else "invalid list body"
                _last_list_error["message"] = err_msg
                _last_list_error["at"] = _t.time()
                _last_list_error["operation"] = "sales-order/list.do"
                if entry:
                    return {**entry["data"], "cached": True, "stale": True, "fetched_at": entry["at"]}
                raise AccurateRequestError(
                    AccurateErrorCategory.INVALID_RESPONSE,
                    technical_message=err_msg or "list rejected",
                    retryable=False,
                )
            rows_raw = body.get("d") or []
            if not isinstance(rows_raw, list):
                rows_raw = []
            sp = body.get("sp") or {}
            total = sp.get("rowCount") or len(rows_raw)
            page_count = sp.get("pageCount") or 1

            # Step 2: enrich each row with detail — bounded concurrency, shared client
            sem = asyncio.Semaphore(ACCURATE_SO_FETCH_CONCURRENCY)

            async def _enrich(row):
                oid = row.get("id") if isinstance(row, dict) else None
                fallback = {
                    "accurate_id": str(oid) if oid is not None else None,
                    "number": row.get("number") if isinstance(row, dict) else None,
                    "transDate": row.get("transDate") if isinstance(row, dict) else None,
                    "customer": None,
                    "totalAmount": None,
                    "statusName": None,
                }
                async with sem:
                    try:
                        d = await self._authed_get(
                            http,
                            f"{host}/accurate/api/sales-order/detail.do",
                            params={"id": oid},
                            logical_name="sales-order/detail.do",
                            so_number=str(row.get("number") or oid or ""),
                            correlation_id=cid,
                        )
                        det_body = d.json() if not d.is_error else {}
                        det = det_body.get("d") if isinstance(det_body.get("d"), dict) else {}
                        cust = det.get("customer") if isinstance(det.get("customer"), dict) else {}
                        return {
                            "accurate_id": str(oid) if oid is not None else None,
                            "number": det.get("number") or (row.get("number") if isinstance(row, dict) else None),
                            "transDate": det.get("transDate") or (row.get("transDate") if isinstance(row, dict) else None),
                            "customer": (cust or {}).get("name") or "-",
                            "totalAmount": det.get("totalAmount"),
                            "statusName": det.get("statusName") or det.get("orderStatus") or None,
                        }
                    except Exception:
                        return fallback

            enriched = await asyncio.gather(*[_enrich(r) for r in rows_raw], return_exceptions=True)
            rows_out = [x if isinstance(x, dict) else {
                "accurate_id": None, "number": None, "transDate": None,
                "customer": None, "totalAmount": None, "statusName": None,
            } for x in enriched]
        payload = {
            "rows": rows_out, "total": total, "page": page, "pageSize": limit,
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

    async def fetch_all_receive_items(self) -> Dict[str, Any]:
        """Penerimaan Barang: list pages then detail. Token/host/http once.

        Official Accurate OpenAPI (account.accurate.id/open-api/json.do):
        GET /api/receive-item/list.do and GET /api/receive-item/detail.do
        (scope receive_item_view). Do not send `fields` — this tenant rejects
        custom fields on list.do. Detail is required for line quantity + item id.
        """
        if not self.is_live:
            rows = [normalize_receive_item(r) for r in _MOCK_RECEIVE_ITEMS]
            return {
                "receipts": [r for r in rows if r],
                "failed": [],
                "complete": True,
                "mode": "mock",
                "list_pages": 1,
                "detail_required": True,
            }
        token = await self._obtain_token()
        host, session = await self._open_db(token)
        list_rows: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=_ITEM_HTTP_TIMEOUT, follow_redirects=True) as http:
            page = 1
            page_count = 1
            while page <= _ITEM_LIST_MAX_PAGES:
                payload = await self._list_receive_items_live_page(
                    http, token=token, host=host, session=session, limit=100, page=page,
                )
                list_rows.extend(payload.get("rows") or [])
                page_count = int(payload.get("pageCount") or 1)
                if page >= page_count or not (payload.get("rows") or []):
                    break
                page += 1
            details, failed = await self._fetch_receive_item_details(
                http, token=token, host=host, session=session, list_rows=list_rows,
            )
        complete = not failed
        return {
            "receipts": details,
            "failed": failed,
            "complete": complete,
            "mode": "live",
            "list_pages": page,
            "detail_required": True,
        }

    async def _list_receive_items_live_page(
        self,
        http: httpx.AsyncClient,
        *,
        token: str,
        host: str,
        session: str,
        limit: int,
        page: int,
    ) -> Dict[str, Any]:
        last_exc: Optional[BaseException] = None
        for attempt in range(1, _ITEM_LIST_MAX_ATTEMPTS + 1):
            headers = self._request_headers(token, session)
            try:
                r = await http.get(
                    f"{host}/accurate/api/receive-item/list.do",
                    headers=headers,
                    params={"sp.pageSize": limit, "sp.page": page},
                )
            except _ITEM_LIST_RETRYABLE as e:
                last_exc = e
                logger.warning(
                    "Accurate receive-item list failed endpoint=receive-item/list.do "
                    "page=%s attempt=%s exception=%s",
                    page, attempt, type(e).__name__,
                )
                if attempt < _ITEM_LIST_MAX_ATTEMPTS:
                    await asyncio.sleep(_ITEM_LIST_RETRY_WAIT_S)
                    continue
                raise RuntimeError("Accurate receive-item list timeout") from e
            if r.is_error:
                logger.warning(
                    "Accurate receive-item list failed endpoint=receive-item/list.do "
                    "page=%s attempt=%s status=%s",
                    page, attempt, r.status_code,
                )
                if attempt < _ITEM_LIST_MAX_ATTEMPTS:
                    await asyncio.sleep(_ITEM_LIST_RETRY_WAIT_S)
                    continue
                raise RuntimeError(f"Accurate receive-item list failed ({r.status_code})")
            try:
                body = r.json()
            except Exception as e:
                raise RuntimeError("Accurate receive-item list returned invalid JSON") from e
            if body.get("s") is False:
                raise RuntimeError("Accurate receive-item list rejected")
            rows_raw = body.get("d") or []
            if not isinstance(rows_raw, list):
                rows_raw = []
            sp = body.get("sp") or {}
            total = sp.get("rowCount") if sp.get("rowCount") is not None else len(rows_raw)
            page_count = sp.get("pageCount") or max(1, (int(total) + limit - 1) // limit)
            return {
                "rows": [r for r in rows_raw if isinstance(r, dict)],
                "pageCount": int(page_count),
                "total": int(total),
            }
        raise RuntimeError("Accurate receive-item list failed") from last_exc

    async def _fetch_receive_item_details(
        self,
        http: httpx.AsyncClient,
        *,
        token: str,
        host: str,
        session: str,
        list_rows: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        sem = asyncio.Semaphore(_ITEM_DETAIL_CONCURRENCY)
        failed: List[Dict[str, Any]] = []
        out: List[Dict[str, Any]] = []

        async def one(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            rid = row.get("id")
            if rid is None or str(rid).strip() == "":
                failed.append({"id": None, "reason": "missing id"})
                return None
            async with sem:
                last_exc: Optional[BaseException] = None
                for attempt in range(1, _ITEM_LIST_MAX_ATTEMPTS + 1):
                    headers = self._request_headers(token, session)
                    try:
                        r = await http.get(
                            f"{host}/accurate/api/receive-item/detail.do",
                            headers=headers,
                            params={"id": rid},
                        )
                    except _ITEM_LIST_RETRYABLE as e:
                        last_exc = e
                        logger.warning(
                            "Accurate receive-item detail failed endpoint=receive-item/detail.do "
                            "page=- attempt=%s exception=%s",
                            attempt, type(e).__name__,
                        )
                        if attempt < _ITEM_LIST_MAX_ATTEMPTS:
                            await asyncio.sleep(_ITEM_LIST_RETRY_WAIT_S)
                            continue
                        failed.append({"id": str(rid), "reason": type(e).__name__})
                        return None
                    if r.status_code == 404:
                        failed.append({"id": str(rid), "reason": "not_found"})
                        return None
                    if r.is_error:
                        logger.warning(
                            "Accurate receive-item detail failed endpoint=receive-item/detail.do "
                            "attempt=%s status=%s",
                            attempt, r.status_code,
                        )
                        if attempt < _ITEM_LIST_MAX_ATTEMPTS:
                            await asyncio.sleep(_ITEM_LIST_RETRY_WAIT_S)
                            continue
                        failed.append({"id": str(rid), "reason": f"http_{r.status_code}"})
                        return None
                    try:
                        payload = r.json()
                    except Exception:
                        failed.append({"id": str(rid), "reason": "invalid_json"})
                        return None
                    d = _item_detail_body(payload) or (payload.get("d") if isinstance(payload.get("d"), dict) else None)
                    if not isinstance(d, dict):
                        failed.append({"id": str(rid), "reason": "empty_detail"})
                        return None
                    merged = dict(row)
                    merged.update(d)
                    return normalize_receive_item(merged)
                failed.append({"id": str(rid), "reason": type(last_exc).__name__ if last_exc else "failed"})
                return None

        for i in range(0, len(list_rows), _ITEM_DETAIL_CONCURRENCY):
            chunk = list_rows[i:i + _ITEM_DETAIL_CONCURRENCY]
            results = await asyncio.gather(*[one(row) for row in chunk], return_exceptions=False)
            for r in results:
                if r:
                    out.append(r)
        return out, failed

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


def _accurate_display_name(value: Any) -> Optional[str]:
    """Display name from an Accurate string or nested record. Never uses id."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    if isinstance(value, str):
        return _blank_to_none(value)
    if isinstance(value, dict):
        for key in ("name", "nama", "fullName", "userName", "nickName"):
            text = _blank_to_none(value.get(key))
            if text:
                return text
    return None


def _accurate_code_or_name(value: Any) -> Optional[str]:
    """Code or name from Accurate string/object (shipment, payment term, zone)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    if isinstance(value, str):
        return _blank_to_none(value)
    if isinstance(value, dict):
        for key in ("name", "nama", "code", "no", "number"):
            text = _blank_to_none(value.get(key))
            if text:
                return text
    return None


def _join_address_parts(*parts: Any) -> Optional[str]:
    bits = []
    for part in parts:
        text = _blank_to_none(part) if not isinstance(part, (dict, list, bool)) else None
        if text:
            bits.append(text)
    return ", ".join(bits) if bits else None


def _accurate_address_from_customer(cust: Dict[str, Any]) -> Optional[str]:
    ships = cust.get("detailShipAddress")
    if isinstance(ships, list) and ships:
        first = ships[0]
        if isinstance(first, str):
            text = _blank_to_none(first)
            if text:
                return text
        if isinstance(first, dict):
            nested = _join_address_parts(
                first.get("address") or first.get("street") or first.get("shipStreet") or first.get("toAddress"),
                first.get("city") or first.get("shipCity"),
                first.get("province") or first.get("shipProvince"),
                first.get("zipCode") or first.get("shipZipCode"),
            )
            if nested:
                return nested
    ship = _join_address_parts(
        cust.get("shipStreet"), cust.get("shipCity"), cust.get("shipProvince"),
        cust.get("shipZipCode"), cust.get("shipCountry"),
    )
    if ship:
        return ship
    return _join_address_parts(
        cust.get("billStreet"), cust.get("billCity"), cust.get("billProvince"),
        cust.get("billZipCode"), cust.get("billCountry"),
    )


def _accurate_so_address(d: Dict[str, Any]) -> Optional[str]:
    """Prefer SO shipping address (OpenAPI `toAddress`), then nested ship-to, then customer."""
    text = _blank_to_none(d.get("toAddress")) if not isinstance(d.get("toAddress"), (dict, list)) else None
    if text:
        return text
    if isinstance(d.get("toAddress"), dict):
        nested = _join_address_parts(
            d["toAddress"].get("address") or d["toAddress"].get("street"),
            d["toAddress"].get("city"),
            d["toAddress"].get("province"),
            d["toAddress"].get("zipCode"),
        )
        if nested:
            return nested
    for key in ("shipToAddress", "shipTo", "shippingAddress"):
        nested = d.get(key)
        if isinstance(nested, str):
            text = _blank_to_none(nested)
            if text:
                return text
        if isinstance(nested, dict):
            addr = _join_address_parts(
                nested.get("address") or nested.get("street") or nested.get("toAddress"),
                nested.get("city"),
                nested.get("province") or nested.get("state"),
                nested.get("zipCode") or nested.get("zip"),
            )
            if addr:
                return addr
    text = _blank_to_none(d.get("shipAddress")) if not isinstance(d.get("shipAddress"), (dict, list)) else None
    if text:
        return text
    cust = d.get("customer") if isinstance(d.get("customer"), dict) else {}
    return _accurate_address_from_customer(cust) if cust else None


def _first_from_salesman_list(value: Any) -> Optional[str]:
    """Prefer expanded salesmanList objects. Skip identity numbers without a name."""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = _accurate_display_name(item)
                if name:
                    return name
            elif isinstance(item, str) and " " in item.strip():
                return item.strip()
        return None
    if isinstance(value, dict):
        return _accurate_display_name(value)
    if isinstance(value, str) and " " in value.strip():
        return value.strip()
    return None


def _accurate_salesman(d: Dict[str, Any], lines_raw: List[Any]) -> Optional[str]:
    """Tenaga penjual. OpenAPI SO save puts salesman on detailItem.salesmanListNumber."""
    for key in ("salesman", "salesmanName"):
        name = _accurate_display_name(d.get(key))
        if name:
            return name
    for li in lines_raw:
        if not isinstance(li, dict):
            continue
        for key in ("salesmanList", "salesman", "salesmanListNumber"):
            name = _first_from_salesman_list(li.get(key))
            if name:
                return name
    return None


def _accurate_created_by(d: Dict[str, Any]) -> Optional[str]:
    """User yang membuat transaksi — never salesman, printer, or numeric IDs.

    Live GET only sends createdBy as an id (e.g. 1248273). printUserName is the
    print operator, not the creator. Official OpenAPI has no user-by-id lookup
    (/api/userinfo.do is the current token user only), so an id without a name
    stays unresolved (UI: Perlu verifikasi).
    """
    for key in ("userName", "createUserName", "createdByUserName"):
        name = _accurate_person_name(d.get(key))
        if name:
            return name
    for key in ("user", "createUser", "createdByUser"):
        name = _accurate_person_name(d.get(key))
        if name:
            return name
    created = d.get("createdBy")
    if isinstance(created, dict):
        return _accurate_person_name(created)
    return None


def _accurate_person_name(value: Any) -> Optional[str]:
    """Display name only. Digit-only Accurate ids are not names."""
    name = _accurate_display_name(value)
    if not name or name.isdigit():
        return None
    return name


def _accurate_record_id(value: Any) -> Optional[str]:
    """Numeric Accurate record id as string. Never treats a name as an id."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = value.get("id")
        if value is None or isinstance(value, bool):
            return None
    if isinstance(value, float):
        if value != value or value != int(value):
            return None
        value = int(value)
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    s = str(value).strip()
    if not s or not s.isdigit():
        return None
    return s


def _accurate_created_by_id(d: Dict[str, Any]) -> Optional[str]:
    """createdBy user id only. Never userPrinted / salesman."""
    for key in ("createdBy", "user", "createUser", "createdByUser"):
        rid = _accurate_record_id(d.get(key))
        if rid:
            return rid
    return None


def _accurate_outlet_name(d: Dict[str, Any]) -> Optional[str]:
    """SO branch name if Accurate already expanded it. Never shipment/warehouse."""
    name = _blank_to_none(d.get("branchName"))
    if name:
        return name
    branch = d.get("branch") if isinstance(d.get("branch"), dict) else {}
    return _accurate_display_name(branch)


def _accurate_branch_id(d: Dict[str, Any]) -> Optional[str]:
    """Transaction branchId. Header only — not warehouse or line selling-price."""
    rid = _accurate_record_id(d.get("branchId"))
    if rid:
        return rid
    branch = d.get("branch") if isinstance(d.get("branch"), dict) else {}
    return _accurate_record_id(branch)


def _ship_pic_name(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    return _blank_to_none(obj.get("picName"))


def _accurate_recipient(d: Dict[str, Any]) -> Optional[str]:
    """Live-proven: customer.shipAddress.picName, then matching shipAddressList."""
    cust = d.get("customer") if isinstance(d.get("customer"), dict) else {}
    name = _ship_pic_name(cust.get("shipAddress"))
    if name:
        return name
    wanted = d.get("shipAddressId")
    if wanted in (None, ""):
        wanted = cust.get("shipAddressId")
    ship = cust.get("shipAddress")
    if wanted in (None, "") and isinstance(ship, dict):
        wanted = ship.get("id")
    lst = cust.get("shipAddressList")
    if isinstance(lst, list) and wanted not in (None, ""):
        for item in lst:
            if isinstance(item, dict) and str(item.get("id")) == str(wanted):
                name = _ship_pic_name(item)
                if name:
                    return name
    for ship_key in ("shipToAddress", "shipTo"):
        nested = d.get(ship_key)
        if isinstance(nested, dict):
            recipient = _blank_to_none(nested.get("attention")) or (
                _blank_to_none(nested.get("name")) if not nested.get("address") else None
            )
            if recipient:
                return recipient
    return None


def _accurate_expedition(d: Dict[str, Any]) -> Optional[str]:
    """OpenAPI shipmentName: record pengiriman (JNE, DHL, OUTLET, …)."""
    for key in ("shipment", "shipmentName"):
        name = _accurate_code_or_name(d.get(key))
        if name:
            return name
    return _accurate_code_or_name(d.get("expedition"))


def _classification1_name(obj: Dict[str, Any]) -> Optional[str]:
    """Only Accurate dataClassification1. Other classification slots are ignored."""
    name = _accurate_code_or_name(obj.get("dataClassification1"))
    if name:
        return name
    return _blank_to_none(obj.get("dataClassification1Name"))


def _accurate_char_field3(obj: Dict[str, Any]) -> Optional[str]:
    """Live-proven zona: searchCharField3.name then charField3."""
    nested = obj.get("searchCharField3")
    if isinstance(nested, dict):
        name = _blank_to_none(nested.get("name"))
        if name:
            return name
    else:
        name = _accurate_code_or_name(nested)
        if name:
            return name
    raw = obj.get("charField3")
    if isinstance(raw, dict):
        return _accurate_code_or_name(raw)
    if isinstance(raw, (list, bool)):
        return None
    return _blank_to_none(raw)


def _accurate_delivery_zone(d: Dict[str, Any], lines_raw: List[Any]) -> Optional[str]:
    """Zona kirim. Not shipment (ekspedisi). Live Custom Character Field 3 first."""
    name = _accurate_char_field3(d)
    if name:
        return name
    for li in lines_raw:
        if isinstance(li, dict):
            name = _accurate_char_field3(li)
            if name:
                return name
    for key in ("shipVia", "deliveryZone", "shippingZone"):
        name = _accurate_code_or_name(d.get(key))
        if name:
            return name
    name = _classification1_name(d)
    if name:
        return name
    for li in lines_raw:
        if isinstance(li, dict):
            name = _classification1_name(li)
            if name:
                return name
    return None


def _parse_accurate_date(val: Any) -> Optional[str]:
    """Return YYYY-MM-DD. Accurate OpenAPI examples use dd/MM/yyyy (31/03/2016)."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    s = str(val).strip()
    if not s:
        return None
    s = s.replace("T", " ").split(" ")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def so_preview_dates(so: Dict[str, Any], *, now: Optional[datetime] = None) -> Tuple[str, str]:
    """(order_date, estimated_delivery) ISO strings for Import PO preview.

    Uses Accurate transDate / shipDate when present. The +3 day default is only
    applied when Accurate did not send a shipment date.
    """
    clock = now or datetime.now(timezone.utc)
    offset = int(so.get("order_date_offset_days") or 0)
    order = _parse_accurate_date(so.get("order_date") or so.get("trans_date"))
    order_iso = order or (clock + timedelta(days=offset)).isoformat()
    ship = _parse_accurate_date(
        so.get("estimated_ship_date") or so.get("estimated_delivery") or so.get("ship_date")
    )
    if ship:
        return order_iso, ship
    return order_iso, (clock + timedelta(days=offset + 3)).isoformat()


def summarize_accurate_payload_shape(obj: Any, prefix: str = "", depth: int = 0) -> List[str]:
    """Key/type tree for tests. Never includes Authorization/token/secret values."""
    lines: List[str] = []
    if depth > 6:
        return lines
    if not isinstance(obj, dict):
        return [f"{prefix or '.'}: {type(obj).__name__}"]
    secret_parts = ("token", "secret", "authorization", "password", "signature", "bearer")
    for key, val in obj.items():
        path = f"{prefix}{key}" if prefix else str(key)
        if any(p in str(key).lower() for p in secret_parts):
            lines.append(f"{path}: <redacted>")
            continue
        if isinstance(val, dict):
            lines.append(f"{path}: object")
            lines.extend(summarize_accurate_payload_shape(val, path + ".", depth + 1))
        elif isinstance(val, list):
            lines.append(f"{path}: list[{len(val)}]")
            if val:
                lines.extend(summarize_accurate_payload_shape(val[0], path + "[0].", depth + 1))
        else:
            lines.append(f"{path}: {type(val).__name__}")
    return lines


def _normalize_accurate_so(d: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce Accurate's Sales Order detail payload into our internal shape.

    Field names follow official OpenAPI `/api/sales-order/save.do` plus GET
    detail expansions (`*.name`, `userName`, `salesmanList`). Unknown fields
    stay None so the UI shows 'Perlu verifikasi' rather than guessing.
    """
    so_notes = _extract_so_notes_text(d)
    so_colors = _detect_colors(so_notes)
    lines_raw = d.get("detailItem") or d.get("detailItems") or d.get("items") or []
    if not isinstance(lines_raw, list):
        lines_raw = []
    lines: List[Dict[str, Any]] = []
    for li in lines_raw:
        if not isinstance(li, dict):
            continue
        item = li.get("item") or {}
        if not isinstance(item, dict):
            item = {}
        # Color: structured line field, then line notes, then header description.
        explicit_color = item.get("color") or li.get("color") or li.get("itemColor") or item.get("itemColor") or None
        line_note_text = " ".join(
            str(li.get(k) or "").strip()
            for k in ("detailNotes", "notes", "memo", "description", "itemDesc")
            if li.get(k)
        ).strip()

        color_val: Optional[str] = None
        color_source: Optional[str] = None
        color_candidates: List[str] = []
        needs_verify: bool = False

        if explicit_color and str(explicit_color).strip():
            color_val = str(explicit_color).strip()
            color_source = "item.color"
        else:
            colors_in_item = _detect_colors(line_note_text)
            if len(colors_in_item) == 1:
                color_val = colors_in_item[0]
                color_source = "item.description"
            elif len(colors_in_item) > 1:
                color_candidates = colors_in_item
                needs_verify = True
                color_source = "item.description"
            elif so_colors:
                # Header description fallback. Never overwrites a line-level color.
                if len(so_colors) == 1:
                    color_val = so_colors[0]
                    color_source = "so.description"
                else:
                    color_candidates = so_colors
                    needs_verify = True
                    color_source = "so.description"

        line_notes = (li.get("detailNotes") or li.get("description") or li.get("notes") or None)
        qty_raw = li.get("quantity") if li.get("quantity") not in (None, "") else li.get("qty")
        line: Dict[str, Any] = {
            "accurate_item_id": _extract_accurate_item_id(li, item),
            "item_code": item.get("no") or li.get("itemNo") or None,
            "name": item.get("name") or li.get("itemName") or li.get("detailName") or li.get("description") or "-",
            "quantity": coerce_accurate_quantity(qty_raw),
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
    salesperson = _accurate_salesman(d, lines_raw)
    created_by_name = _accurate_created_by(d)
    trans_date = _parse_accurate_date(d.get("transDate") or d.get("trans_date"))
    ship_date = _parse_accurate_date(d.get("shipDate") or d.get("ship_date"))
    payment = (
        _accurate_code_or_name(d.get("paymentTerm"))
        or _blank_to_none(d.get("paymentTermName"))
        or _accurate_code_or_name(d.get("paymentTermName"))
    )
    recipient = _accurate_recipient(d)
    return {
        "accurate_id": str(d.get("id") or d.get("number") or ""),
        "po_number": str(d.get("number") or d.get("orderNumber") or ""),
        "customer": _accurate_display_name(d.get("customer")) or _blank_to_none(d.get("customerName")),
        "outlet": _accurate_outlet_name(d),
        "recipient": recipient,
        "address": _accurate_so_address(d),
        "payment_terms": payment,
        "sales": salesperson,
        "created_by_user": created_by_name,
        "expedition": _accurate_expedition(d),
        "delivery_zone": _accurate_delivery_zone(d, lines_raw),
        "seller": salesperson,
        "order_date_offset_days": 0,
        "order_date": trans_date,
        "trans_date": trans_date,
        "ship_date": ship_date,
        "estimated_ship_date": ship_date,
        "estimated_delivery": ship_date,
        "line_items": lines,
        "so_notes_raw": so_notes or None,
        _SO_INTERNAL_CREATED_BY_ID: _accurate_created_by_id(d),
        _SO_INTERNAL_BRANCH_ID: _accurate_branch_id(d),
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


# Penerimaan Barang — official Accurate OpenAPI (account.accurate.id/open-api/json.do)
ACCURATE_RECEIVE_ITEM_LIST_PATH = "/accurate/api/receive-item/list.do"
ACCURATE_RECEIVE_ITEM_DETAIL_PATH = "/accurate/api/receive-item/detail.do"
ACCURATE_RECEIVE_ITEM_ENDPOINT_AVAILABLE = True
ACCURATE_RECEIVE_ITEM_ENDPOINT_EVIDENCE = (
    "Official Accurate OpenAPI GET /api/receive-item/list.do and "
    "GET /api/receive-item/detail.do (tags /api/receive-item, "
    "summary Penerimaan Barang, scope receive_item_view). "
    "Source: https://account.accurate.id/open-api/json.do"
)
# approvalStatus REJECTED is documented on list.do. Dedicated void/cancelled
# field is not documented on receive-item — do not invent one.
RECEIVE_ITEM_INVALID_APPROVAL = frozenset({"REJECTED"})

_MOCK_RECEIVE_ITEMS: List[Dict[str, Any]] = [
    {
        "id": 91001,
        "number": "R1",
        "transDate": "25/08/2026",
        "approvalStatus": "APPROVED",
        "detailItem": [
            {"id": 1, "quantity": 2000, "item": {"id": 101, "no": "PC-12"}, "itemUnitName": "pcs"},
        ],
    },
    {
        "id": 91002,
        "number": "R2",
        "transDate": "25/08/2026",
        "approvalStatus": "APPROVED",
        "detailItem": [
            {"id": 2, "quantity": 1500, "item": {"id": 101, "no": "PC-12"}, "itemUnitName": "pcs"},
        ],
    },
    {
        "id": 91003,
        "number": "R3",
        "transDate": "25/08/2026",
        "approvalStatus": "APPROVED",
        "detailItem": [
            {"id": 3, "quantity": 4000, "item": {"id": 202, "no": "LB-M"}, "itemUnitName": "pcs"},
        ],
    },
]


def receive_item_is_valid(raw: Dict[str, Any]) -> bool:
    status = str(raw.get("approvalStatus") or "").strip().upper()
    if status in RECEIVE_ITEM_INVALID_APPROVAL:
        return False
    return True


def normalize_receive_item(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize list/detail Penerimaan Barang. Quantity-only; never unitPrice."""
    if not isinstance(raw, dict):
        return None
    rid = raw.get("id")
    if rid is None or str(rid).strip() == "":
        return None
    lines_raw = raw.get("detailItem") or raw.get("detailItems") or []
    if not isinstance(lines_raw, list):
        lines_raw = []
    lines = []
    for idx, li in enumerate(lines_raw):
        if not isinstance(li, dict):
            continue
        item = li.get("item") if isinstance(li.get("item"), dict) else {}
        aid = _extract_accurate_item_id(li, item)
        if not aid:
            continue
        line_id = li.get("id")
        if line_id is None or str(line_id).strip() == "":
            line_id = f"{rid}:{idx}"
        qty = li.get("quantity")
        try:
            n = float(qty)
        except (TypeError, ValueError):
            n = 0
        if n == int(n):
            n = int(n)
        unit = _blank_to_none(li.get("itemUnitName")) or _extract_item_unit(item) or ""
        lines.append({
            "accurate_line_id": str(line_id),
            "accurate_item_id": aid,
            "received_qty": n,
            "unit": unit,
        })
    return {
        "accurate_receipt_id": str(rid),
        "receipt_number": str(raw.get("number") or "").strip(),
        "receipt_date": str(raw.get("transDate") or "").strip(),
        "approval_status": str(raw.get("approvalStatus") or "").strip(),
        "valid": receive_item_is_valid(raw),
        "lines": lines,
    }


# Pengiriman Pesanan / delivery shipment is NOT in the existing Accurate client.
# Do not invent live URLs. Local mock catalog only — same identity fields as SO lines.
ACCURATE_DELIVERY_ENDPOINT_AVAILABLE = False
ACCURATE_DELIVERY_ENDPOINT_REASON = (
    "Tidak ada endpoint Pengiriman Pesanan di services/accurate.py, tests, "
    "atau dokumentasi repository (hanya sales-order/list.do, sales-order/detail.do, "
    "item/list.do, item/detail.do). Live fetch tidak diimplementasikan."
)

_MOCK_DELIVERY_NOTES: List[Dict[str, Any]] = [
    {
        "accurate_shipment_id": "ACC-DO-2001-1",
        "accurate_so_id": "ACC-SO-2001",
        "po_number": "PO-2001",
        "number": "SJ-2001-01",
        "notes": "Mock Pengiriman Pesanan parsial 1",
        "lines": [
            {"accurate_item_id": "104", "quantity": 3000},
            {"accurate_item_id": "105", "quantity": 1000},
        ],
    },
    {
        "accurate_shipment_id": "ACC-DO-2001-2",
        "accurate_so_id": "ACC-SO-2001",
        "po_number": "PO-2001",
        "number": "SJ-2001-02",
        "notes": "Mock Pengiriman Pesanan parsial 2",
        "lines": [
            {"accurate_item_id": "104", "quantity": 4000},
        ],
    },
    {
        "accurate_shipment_id": "ACC-DO-1050-1",
        "accurate_so_id": "ACC-SO-1050",
        "po_number": "PO-1050",
        "number": "SJ-1050-01",
        "notes": "Mock SJ Lunchbox + Jumbo",
        "lines": [
            {"accurate_item_id": "202", "quantity": 300},
            {"accurate_item_id": "203", "quantity": 100},
        ],
    },
]


def list_mock_delivery_notes(
    *,
    accurate_so_id: Optional[str] = None,
    po_number: Optional[str] = None,
    accurate_shipment_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows = list(_MOCK_DELIVERY_NOTES)
    so = str(accurate_so_id or "").strip()
    po = str(po_number or "").strip()
    sid = str(accurate_shipment_id or "").strip()
    if sid:
        rows = [r for r in rows if str(r.get("accurate_shipment_id") or "") == sid]
    if so:
        rows = [r for r in rows if str(r.get("accurate_so_id") or "") == so]
    elif po:
        rows = [r for r in rows if str(r.get("po_number") or "").upper() == po.upper()]
    return [dict(r) for r in rows]


def get_mock_delivery_note(
    *,
    accurate_shipment_id: str,
    accurate_so_id: Optional[str] = None,
    po_number: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    sid = str(accurate_shipment_id or "").strip()
    if not sid:
        return None
    rows = list_mock_delivery_notes(accurate_shipment_id=sid)
    if not rows:
        return None
    note = rows[0]
    so = str(accurate_so_id or "").strip()
    po = str(po_number or "").strip()
    if so and str(note.get("accurate_so_id") or "") not in ("", so):
        return None
    if po and str(note.get("po_number") or "").upper() not in ("", po.upper()):
        return None
    return dict(note)


async def fetch_delivery_notes(
    client: AccurateClient,
    *,
    accurate_so_id: Optional[str] = None,
    po_number: Optional[str] = None,
) -> Dict[str, Any]:
    """Return mock Pengiriman Pesanan. Live Accurate is never called here."""
    if client.is_live:
        return {
            "ok": False,
            "blocked": True,
            "mode": "live",
            "reason": ACCURATE_DELIVERY_ENDPOINT_REASON,
            "notes": [],
        }
    notes = list_mock_delivery_notes(accurate_so_id=accurate_so_id, po_number=po_number)
    if not notes and (accurate_so_id or po_number):
        notes = list_mock_delivery_notes()
        notes = [
            n for n in notes
            if str(n.get("accurate_so_id") or "") == str(accurate_so_id or "")
            or str(n.get("po_number") or "").upper() == str(po_number or "").upper()
        ]
    return {
        "ok": True,
        "blocked": False,
        "mode": "mock",
        "reason": None,
        "notes": notes,
    }


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
