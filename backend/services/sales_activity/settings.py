"""Sales Activity settings stored in app_settings. Versioned prospectively."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services.sales_activity.constants import (
    DORMANT_DESTINATION_POOL,
    FALLBACK_POOL,
    REASSIGN_HYBRID,
    SETTINGS_KEY,
    TIMER_EARLIEST,
    TIMER_MODES,
)

DEFAULT_ACTIVITY_TYPES: List[dict] = [
    {
        "id": "visit",
        "label": "Visit",
        "active": True,
        "counts_as_valid": True,
        "resets_inactivity": True,
        "evidence_required": False,
        "visit_photo_required": False,
        "next_follow_up_required": False,
    },
    {
        "id": "chat",
        "label": "Chat",
        "active": True,
        "counts_as_valid": True,
        "resets_inactivity": True,
        "evidence_required": False,
        "visit_photo_required": False,
        "next_follow_up_required": False,
    },
    {
        "id": "closing",
        "label": "Closing",
        "active": True,
        "counts_as_valid": True,
        "resets_inactivity": True,
        "evidence_required": False,
        "visit_photo_required": False,
        "next_follow_up_required": False,
    },
]


def default_settings() -> dict:
    return {
        "key": SETTINGS_KEY,
        "default_rule": {
            "id": "default",
            "version": 1,
            "enabled": True,
            "ownership_duration_days": 30,
            "warning_before_expiration_days": 14,
            "inactivity_warning_days": 14,
            "inactivity_expiration_days": 30,
            "timer_mode": TIMER_EARLIEST,
            "valid_activity_resets_inactivity": True,
        },
        "reassignment": {
            "mode": REASSIGN_HYBRID,
            "sales_sequence": [],
            "fallback": FALLBACK_POOL,
            "max_rotations": 0,
            "skip_inactive_users": True,
            "end_of_sequence": FALLBACK_POOL,
        },
        "activity_types": copy.deepcopy(DEFAULT_ACTIVITY_TYPES),
        "dormancy": {
            "threshold_days": 90,
            "warning_days": 14,
            "destination": DORMANT_DESTINATION_POOL,
        },
        "manual": {
            "allow_extend": True,
            "allow_pause_resume": True,
            "allow_reassign": True,
            "allow_pool_assign": True,
            "reason_required": True,
        },
        "migration": {
            "imported_automation_default": "PAUSED_MIGRATION",
            "activation_requires_explicit": True,
            "prospect_timer_baseline": "ACTIVATION_TIMESTAMP",
        },
        "override_rules": [],
        "prospect_pic_user_ids": [],
    }


def _as_int(value: Any, field: str, *, min_value: int = 0, max_value: int = 3650) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} harus angka")
    if number < min_value or number > max_value:
        raise HTTPException(400, f"{field} harus antara {min_value} dan {max_value}")
    return number


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "ya"}:
        return True
    if text in {"0", "false", "no", "tidak"}:
        return False
    return default


def _sanitize_rule_body(raw: dict, prefix: str) -> dict:
    duration = _as_int(raw.get("ownership_duration_days", 30), f"{prefix}.ownership_duration_days", min_value=1)
    warn_exp = _as_int(raw.get("warning_before_expiration_days", 14), f"{prefix}.warning_before_expiration_days", min_value=0)
    inact_warn = _as_int(raw.get("inactivity_warning_days", 14), f"{prefix}.inactivity_warning_days", min_value=0)
    inact_exp = _as_int(raw.get("inactivity_expiration_days", 30), f"{prefix}.inactivity_expiration_days", min_value=1)
    if warn_exp >= duration:
        raise HTTPException(400, f"{prefix}.warning_before_expiration_days harus lebih kecil dari ownership_duration_days")
    if inact_warn >= inact_exp:
        raise HTTPException(400, f"{prefix}.inactivity_warning_days harus lebih kecil dari inactivity_expiration_days")
    mode = str(raw.get("timer_mode") or TIMER_EARLIEST).strip().upper()
    if mode not in TIMER_MODES:
        raise HTTPException(400, f"{prefix}.timer_mode harus salah satu dari {list(TIMER_MODES)}")
    return {
        "enabled": _as_bool(raw.get("enabled", True), True),
        "ownership_duration_days": duration,
        "warning_before_expiration_days": warn_exp,
        "inactivity_warning_days": inact_warn,
        "inactivity_expiration_days": inact_exp,
        "timer_mode": mode,
        "valid_activity_resets_inactivity": _as_bool(raw.get("valid_activity_resets_inactivity", True), True),
    }


def _sanitize_activity_types(raw: Any) -> List[dict]:
    if raw is None:
        return copy.deepcopy(DEFAULT_ACTIVITY_TYPES)
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "activity_types tidak boleh kosong")
    seen = set()
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise HTTPException(400, f"activity_types[{i}] tidak valid")
        type_id = str(item.get("id") or "").strip().lower()
        label = str(item.get("label") or "").strip()
        if not type_id or not label:
            raise HTTPException(400, f"activity_types[{i}] membutuhkan id dan label")
        if type_id in seen:
            raise HTTPException(400, f"activity_types id duplikat: {type_id}")
        seen.add(type_id)
        if type_id == "visit" and "visit_photo_required" in item:
            evidence_required = _as_bool(item.get("visit_photo_required"), False)
        else:
            evidence_required = _as_bool(item.get("evidence_required", False), False)
        out.append({
            "id": type_id,
            "label": label[:80],
            "active": _as_bool(item.get("active", True), True),
            "counts_as_valid": _as_bool(item.get("counts_as_valid", True), True),
            "resets_inactivity": _as_bool(item.get("resets_inactivity", True), True),
            "evidence_required": evidence_required,
            "visit_photo_required": evidence_required if type_id == "visit" else False,
            "next_follow_up_required": _as_bool(item.get("next_follow_up_required", False), False),
        })
    return out


def _sanitize_override_rules(raw: Any) -> List[dict]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise HTTPException(400, "override_rules harus berupa list")
    out = []
    priorities = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise HTTPException(400, f"override_rules[{i}] tidak valid")
        rule_id = str(item.get("id") or f"override-{i+1}").strip()
        priority = _as_int(item.get("priority", i + 1), f"override_rules[{i}].priority", min_value=1, max_value=10000)
        match = item.get("match") or {}
        if not isinstance(match, dict):
            raise HTTPException(400, f"override_rules[{i}].match tidak valid")
        body = _sanitize_rule_body(item.get("rule") or item, f"override_rules[{i}]")
        version = _as_int(item.get("version", 1), f"override_rules[{i}].version", min_value=1, max_value=100000)
        out.append({
            "id": rule_id[:64],
            "priority": priority,
            "enabled": _as_bool(item.get("enabled", True), True),
            "version": version,
            "match": {
                "category": str(match.get("category") or "").strip(),
                "lead_source": str(match.get("lead_source") or "").strip(),
            },
            "rule": body,
        })
        priorities.append((priority, rule_id))
    # Deterministic order: priority desc, id asc
    out.sort(key=lambda r: (-int(r["priority"]), str(r["id"])))
    return out


def _rule_fingerprint(settings: dict) -> Tuple:
    rule = settings.get("default_rule") or {}
    reassignment = settings.get("reassignment") or {}
    dormancy = settings.get("dormancy") or {}
    types = tuple(
        (
            t.get("id"),
            t.get("active"),
            t.get("counts_as_valid"),
            t.get("resets_inactivity"),
        )
        for t in (settings.get("activity_types") or [])
    )
    overrides = tuple(
        (o.get("id"), o.get("priority"), o.get("version"), o.get("enabled"), tuple(sorted((o.get("match") or {}).items())))
        for o in (settings.get("override_rules") or [])
    )
    return (
        rule.get("enabled"),
        rule.get("ownership_duration_days"),
        rule.get("warning_before_expiration_days"),
        rule.get("inactivity_warning_days"),
        rule.get("inactivity_expiration_days"),
        rule.get("timer_mode"),
        rule.get("valid_activity_resets_inactivity"),
        tuple(reassignment.get("sales_sequence") or []),
        reassignment.get("skip_inactive_users"),
        reassignment.get("max_rotations"),
        dormancy.get("threshold_days"),
        dormancy.get("warning_days"),
        types,
        overrides,
    )


def merge_settings(stored: Optional[dict]) -> dict:
    base = default_settings()
    if not stored:
        return base
    incoming = dict(stored)
    incoming.pop("_id", None)
    default_rule = dict(base["default_rule"])
    default_rule.update(incoming.get("default_rule") or {})
    default_rule["id"] = "default"
    try:
        default_rule["version"] = max(1, int(default_rule.get("version") or 1))
    except (TypeError, ValueError):
        default_rule["version"] = 1
    reassignment = dict(base["reassignment"])
    reassignment.update(incoming.get("reassignment") or {})
    reassignment["mode"] = REASSIGN_HYBRID
    reassignment["fallback"] = FALLBACK_POOL
    reassignment["end_of_sequence"] = FALLBACK_POOL
    dormancy = dict(base["dormancy"])
    dormancy.update(incoming.get("dormancy") or {})
    dormancy["destination"] = DORMANT_DESTINATION_POOL
    manual = dict(base["manual"])
    manual.update(incoming.get("manual") or {})
    migration = dict(base["migration"])
    migration.update(incoming.get("migration") or {})
    types = incoming.get("activity_types")
    if not isinstance(types, list) or not types:
        types = copy.deepcopy(DEFAULT_ACTIVITY_TYPES)
    decorated = []
    for item in types:
        row = dict(item or {})
        type_id = str(row.get("id") or "").strip().lower()
        ev = bool(row.get("evidence_required"))
        row["visit_photo_required"] = ev if type_id == "visit" else False
        decorated.append(row)
    types = decorated
    overrides = incoming.get("override_rules") if isinstance(incoming.get("override_rules"), list) else []
    pic_ids = incoming.get("prospect_pic_user_ids")
    if not isinstance(pic_ids, list):
        pic_ids = list(base["prospect_pic_user_ids"])
    return {
        "key": SETTINGS_KEY,
        "default_rule": default_rule,
        "reassignment": reassignment,
        "activity_types": types,
        "dormancy": dormancy,
        "manual": manual,
        "migration": migration,
        "override_rules": overrides,
        "prospect_pic_user_ids": [str(x).strip() for x in pic_ids if str(x or "").strip()],
        "updated_at": incoming.get("updated_at"),
        "updated_by": incoming.get("updated_by"),
        "updated_by_name": incoming.get("updated_by_name"),
    }


def _sanitize_user_id_list(
    raw: Any,
    *,
    field: str,
    known_user_ids: Optional[set] = None,
    active_user_ids: Optional[set] = None,
) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(400, f"{field} harus berupa list")
    out: List[str] = []
    seen = set()
    for item in raw:
        uid = str(item or "").strip()
        if not uid:
            continue
        if uid in seen:
            raise HTTPException(400, f"{field} tidak boleh berisi user duplikat")
        if known_user_ids is not None and uid not in known_user_ids:
            raise HTTPException(400, f"User tidak ditemukan: {uid}")
        if active_user_ids is not None and uid not in active_user_ids:
            raise HTTPException(400, f"User tidak aktif tidak dapat menjadi PIC Sales Prospek: {uid}")
        seen.add(uid)
        out.append(uid)
    return out


def sanitize_put(
    payload: dict,
    existing: Optional[dict] = None,
    known_user_ids: Optional[set] = None,
    active_user_ids: Optional[set] = None,
) -> dict:
    current = merge_settings(existing)
    raw = payload or {}
    rule_in = raw.get("default_rule") or {}
    body = _sanitize_rule_body(rule_in, "default_rule")
    version = int(current["default_rule"].get("version") or 1)
    candidate = {
        "default_rule": {
            "id": "default",
            "version": version,
            **body,
        },
        "reassignment": {
            "mode": REASSIGN_HYBRID,
            "sales_sequence": list(raw.get("reassignment", {}).get("sales_sequence") or current["reassignment"]["sales_sequence"]),
            "fallback": FALLBACK_POOL,
            "max_rotations": _as_int(
                (raw.get("reassignment") or {}).get("max_rotations", current["reassignment"].get("max_rotations") or 0),
                "reassignment.max_rotations",
                min_value=0,
                max_value=100,
            ),
            "skip_inactive_users": _as_bool(
                (raw.get("reassignment") or {}).get("skip_inactive_users", current["reassignment"].get("skip_inactive_users", True)),
                True,
            ),
            "end_of_sequence": FALLBACK_POOL,
        },
        "activity_types": _sanitize_activity_types(raw.get("activity_types", current["activity_types"])),
        "dormancy": {
            "threshold_days": _as_int(
                (raw.get("dormancy") or {}).get("threshold_days", current["dormancy"]["threshold_days"]),
                "dormancy.threshold_days",
                min_value=1,
            ),
            "warning_days": _as_int(
                (raw.get("dormancy") or {}).get("warning_days", current["dormancy"]["warning_days"]),
                "dormancy.warning_days",
                min_value=0,
            ),
            "destination": DORMANT_DESTINATION_POOL,
        },
        "manual": {
            "allow_extend": _as_bool((raw.get("manual") or {}).get("allow_extend", current["manual"]["allow_extend"]), True),
            "allow_pause_resume": _as_bool((raw.get("manual") or {}).get("allow_pause_resume", current["manual"]["allow_pause_resume"]), True),
            "allow_reassign": _as_bool((raw.get("manual") or {}).get("allow_reassign", current["manual"]["allow_reassign"]), True),
            "allow_pool_assign": _as_bool((raw.get("manual") or {}).get("allow_pool_assign", current["manual"]["allow_pool_assign"]), True),
            "reason_required": True,
        },
        "migration": {
            "imported_automation_default": "PAUSED_MIGRATION",
            "activation_requires_explicit": True,
            "prospect_timer_baseline": "ACTIVATION_TIMESTAMP",
        },
        "override_rules": _sanitize_override_rules(raw.get("override_rules", current.get("override_rules") or [])),
        "prospect_pic_user_ids": _sanitize_user_id_list(
            raw.get("prospect_pic_user_ids", current.get("prospect_pic_user_ids") or []),
            field="prospect_pic_user_ids",
            known_user_ids=known_user_ids,
            active_user_ids=active_user_ids,
        ),
    }
    if candidate["dormancy"]["warning_days"] >= candidate["dormancy"]["threshold_days"]:
        raise HTTPException(400, "dormancy.warning_days harus lebih kecil dari threshold_days")

    sequence = []
    seen = set()
    for item in candidate["reassignment"]["sales_sequence"]:
        uid = str(item or "").strip()
        if not uid:
            continue
        if uid in seen:
            raise HTTPException(400, "reassignment.sales_sequence tidak boleh berisi user duplikat")
        if known_user_ids is not None and uid not in known_user_ids:
            raise HTTPException(400, f"User sequence tidak ditemukan: {uid}")
        seen.add(uid)
        sequence.append(uid)
    candidate["reassignment"]["sales_sequence"] = sequence

    if _rule_fingerprint(candidate) != _rule_fingerprint(current):
        candidate["default_rule"]["version"] = version + 1
    else:
        candidate["default_rule"]["version"] = version
    return candidate


def rule_snapshot(settings: dict) -> dict:
    merged = merge_settings(settings)
    return {
        "rule_id": merged["default_rule"]["id"],
        "rule_version": merged["default_rule"]["version"],
        "default_rule": copy.deepcopy(merged["default_rule"]),
        "reassignment": copy.deepcopy(merged["reassignment"]),
        "dormancy": copy.deepcopy(merged["dormancy"]),
        "activity_types": copy.deepcopy(merged["activity_types"]),
        "override_rules": copy.deepcopy(merged.get("override_rules") or []),
    }


def select_rule(settings: dict, outlet: Optional[dict] = None) -> dict:
    """Deterministic rule selection: highest priority matching override, else default."""
    merged = merge_settings(settings)
    outlet = outlet or {}
    category = str(outlet.get("category") or "").strip().lower()
    lead_source = str(outlet.get("lead_source") or "").strip().lower()
    matches = []
    for rule in merged.get("override_rules") or []:
        if not rule.get("enabled"):
            continue
        match = rule.get("match") or {}
        want_cat = str(match.get("category") or "").strip().lower()
        want_src = str(match.get("lead_source") or "").strip().lower()
        if want_cat and want_cat != category:
            continue
        if want_src and want_src != lead_source:
            continue
        if not want_cat and not want_src:
            continue
        matches.append(rule)
    if matches:
        matches.sort(key=lambda r: (-int(r.get("priority") or 0), str(r.get("id") or "")))
        chosen = matches[0]
        body = dict(chosen.get("rule") or {})
        return {
            "rule_id": chosen.get("id") or "override",
            "rule_version": int(chosen.get("version") or 1),
            "enabled": bool(body.get("enabled", True)),
            "ownership_duration_days": int(body.get("ownership_duration_days") or 30),
            "warning_before_expiration_days": int(body.get("warning_before_expiration_days") or 14),
            "inactivity_warning_days": int(body.get("inactivity_warning_days") or 14),
            "inactivity_expiration_days": int(body.get("inactivity_expiration_days") or 30),
            "timer_mode": body.get("timer_mode") or TIMER_EARLIEST,
            "valid_activity_resets_inactivity": bool(body.get("valid_activity_resets_inactivity", True)),
            "reassignment": merged["reassignment"],
            "dormancy": merged["dormancy"],
        }
    rule = merged["default_rule"]
    return {
        "rule_id": rule["id"],
        "rule_version": int(rule.get("version") or 1),
        "enabled": bool(rule.get("enabled", True)),
        "ownership_duration_days": int(rule.get("ownership_duration_days") or 30),
        "warning_before_expiration_days": int(rule.get("warning_before_expiration_days") or 14),
        "inactivity_warning_days": int(rule.get("inactivity_warning_days") or 14),
        "inactivity_expiration_days": int(rule.get("inactivity_expiration_days") or 30),
        "timer_mode": rule.get("timer_mode") or TIMER_EARLIEST,
        "valid_activity_resets_inactivity": bool(rule.get("valid_activity_resets_inactivity", True)),
        "reassignment": merged["reassignment"],
        "dormancy": merged["dormancy"],
    }


def activity_type_map(settings: dict) -> Dict[str, dict]:
    merged = merge_settings(settings)
    return {str(t["id"]).lower(): t for t in merged.get("activity_types") or []}


async def load_settings(db) -> dict:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return merge_settings(doc)


async def save_settings(
    db,
    payload: dict,
    actor: dict,
    known_user_ids: Optional[set] = None,
    active_user_ids: Optional[set] = None,
) -> dict:
    existing = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    sanitized = sanitize_put(
        payload,
        existing,
        known_user_ids=known_user_ids,
        active_user_ids=active_user_ids,
    )
    from services.sales_activity.clock import iso_now

    doc = {
        "key": SETTINGS_KEY,
        **sanitized,
        "updated_at": iso_now(),
        "updated_by": actor.get("email"),
        "updated_by_name": actor.get("name"),
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": doc}, upsert=True)
    return merge_settings(doc)
