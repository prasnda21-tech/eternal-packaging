"""Shared helpers for Sales Activity."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from services.sales_activity.clock import iso_now, now_utc, parse_dt
from services.sales_activity.constants import BLANK_MARKERS, TIMER_EARLIEST, TIMER_FIXED, TIMER_INACTIVITY

_WS = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D+")


def new_id() -> str:
    return str(uuid.uuid4())


def actor_id(user: dict) -> str:
    return str(user.get("user_id") or user.get("sub") or user.get("id") or "").strip()


def actor_name(user: dict) -> str:
    return str(user.get("name") or user.get("email") or "").strip()


def normalize_text(value: Any) -> str:
    text = _WS.sub(" ", str(value or "").strip().lower())
    return text


def is_blank(value: Any) -> bool:
    return normalize_text(value) in BLANK_MARKERS


def strong_text(value: Any) -> Optional[str]:
    if is_blank(value):
        return None
    text = str(value).strip()
    return text or None


def normalize_whatsapp(value: Any) -> Optional[str]:
    if is_blank(value):
        return None
    digits = _NON_DIGIT.sub("", str(value))
    if len(digits) < 8:
        return None
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    if digits.startswith("8") and len(digits) >= 9:
        digits = "62" + digits
    return digits


def public_doc(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


def days_between(start: Optional[datetime], end: Optional[datetime] = None) -> Optional[int]:
    left = parse_dt(start) if not isinstance(start, datetime) else start
    right = parse_dt(end) if end is not None and not isinstance(end, datetime) else (end or now_utc())
    if left is None or right is None:
        return None
    return max(0, int((right - left).total_seconds() // 86400))


def compute_windows(started_at, timer_anchor_at, rule: dict) -> dict:
    started = parse_dt(started_at) or now_utc()
    anchor = parse_dt(timer_anchor_at) or started
    duration = timedelta(days=int(rule.get("ownership_duration_days") or 30))
    warn_exp = timedelta(days=int(rule.get("warning_before_expiration_days") or 0))
    inact_exp = timedelta(days=int(rule.get("inactivity_expiration_days") or 30))
    inact_warn = timedelta(days=int(rule.get("inactivity_warning_days") or 0))
    fixed_eligible = started + duration
    fixed_warning = fixed_eligible - warn_exp
    inactivity_eligible = anchor + inact_exp
    inactivity_warning = inactivity_eligible - inact_warn
    mode = str(rule.get("timer_mode") or TIMER_EARLIEST)
    if mode == TIMER_FIXED:
        eligible, warning = fixed_eligible, fixed_warning
    elif mode == TIMER_INACTIVITY:
        eligible, warning = inactivity_eligible, inactivity_warning
    else:
        eligible = min(fixed_eligible, inactivity_eligible)
        warning = min(fixed_warning, inactivity_warning)
    return {
        "ownership_started_at": started.isoformat(),
        "timer_anchor_at": anchor.isoformat(),
        "eligible_at": eligible.isoformat(),
        "warning_at": warning.isoformat(),
        "inactivity_eligible_at": inactivity_eligible.isoformat(),
        "fixed_eligible_at": fixed_eligible.isoformat(),
    }


def require_reason(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        from fastapi import HTTPException
        raise HTTPException(400, "Alasan wajib diisi")
    return text[:500]


def stamp_created(doc: dict) -> dict:
    now = iso_now()
    doc.setdefault("created_at", now)
    doc["updated_at"] = now
    return doc
