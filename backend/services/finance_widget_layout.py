"""Per-user Dashboard Keuangan layout. UI metadata only.

Identity comes from the authenticated session. The document never stores
permissions, balances, or journal figures. Saves use a version compare-and-swap.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

from services.sales_user_mapping import session_user_id

COLLECTION = "finance_dashboard_layouts"
DASHBOARD_KEY = "keuangan"
COLUMNS = 12
MAX_Y = 48
MAX_READY = 9
MAX_PRESERVED = 8

PERM_CASH = "dashboard.finance.view"
PERM_PROFIT = "profit_loss.view"
PERM_PURCHASES = "purchase_invoices.view"
PERM_RECON = "cash_bank.reconcile"
PERM_SALES = "dashboard.sales.view"

READY_PERMISSION = {
    "cash": PERM_CASH,
    "expense": PERM_CASH,
    "profit": PERM_PROFIT,
    "purchases": PERM_PURCHASES,
    "recon": PERM_RECON,
    "sales": PERM_SALES,
    "products": PERM_SALES,
    "sellers": PERM_SALES,
    "trend": PERM_SALES,
}

PERIOD_WIDGETS = frozenset({
    "cash", "expense", "profit", "purchases", "sales", "products", "sellers", "trend",
})
VISUAL_WIDGETS = frozenset({"expense", "products", "sellers", "trend"})
PERIOD_MODES = frozenset({"global", "today", "month", "year", "custom"})
VISUALS = frozenset({"bars", "list"})

# min_w, max_w, min_h, max_h, default_w, default_h
SIZES: Dict[str, Tuple[int, int, int, int, int, int]] = {
    "cash": (3, 8, 2, 6, 3, 2),
    "sales": (3, 8, 2, 6, 3, 2),
    "purchases": (3, 8, 2, 6, 3, 2),
    "profit": (3, 8, 2, 6, 3, 2),
    "expense": (3, 8, 2, 6, 4, 3),
    "trend": (4, 12, 3, 8, 4, 3),
    "products": (4, 12, 3, 8, 4, 3),
    "sellers": (4, 12, 3, 8, 6, 3),
    "recon": (4, 12, 3, 8, 6, 3),
}

DEFAULT_ORDER = (
    "cash", "sales", "purchases", "profit", "expense", "trend", "products", "sellers", "recon",
)

_INDEXED: set = set()


class LayoutError(Exception):
    def __init__(self, status_code: int, code: str, detail: str, version: Optional[int] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.version = version


def finance_widget_allowed(widget_id: str, permissions: Iterable[str], spec: Optional[dict] = None) -> bool:
    ready = READY_PERMISSION if spec is None else spec["ready"]
    needed = ready.get(widget_id)
    return bool(needed and needed in set(permissions or []))


def _int_field(value: Any, field: str) -> int:
    if type(value) is not int:
        raise LayoutError(400, "LAYOUT_INVALID", f"{field} harus bilangan bulat")
    return value


def _overlap(a: dict, b: dict) -> bool:
    return not (
        a["x"] + a["w"] <= b["x"]
        or b["x"] + b["w"] <= a["x"]
        or a["y"] + a["h"] <= b["y"]
        or b["y"] + b["h"] <= a["y"]
    )


def _iso_day(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise LayoutError(400, "LAYOUT_INVALID", f"{field} tidak valid") from exc
    return text


def clean_settings(widget_id: str, raw: Any, spec: Optional[dict] = None) -> dict:
    if raw in (None, ""):
        raw = {}
    if not isinstance(raw, dict):
        raise LayoutError(400, "LAYOUT_INVALID", "Pengaturan widget tidak valid")
    unknown = set(raw) - {"title", "period_mode", "start_date", "end_date", "visual"}
    if unknown:
        raise LayoutError(400, "LAYOUT_INVALID", "Pengaturan widget berisi field yang tidak didukung")
    title = str(raw.get("title") or "").strip()
    if len(title) > 60:
        raise LayoutError(400, "LAYOUT_INVALID", "Judul widget terlalu panjang")
    out: Dict[str, Any] = {}
    if title:
        out["title"] = title
    mode = raw.get("period_mode")
    period_widgets = PERIOD_WIDGETS if spec is None else spec["period"]
    visual_widgets = VISUAL_WIDGETS if spec is None else spec["visual"]
    if widget_id in period_widgets:
        mode = mode or "global"
        if mode not in PERIOD_MODES:
            raise LayoutError(400, "LAYOUT_INVALID", "Periode widget tidak valid")
        out["period_mode"] = mode
        if mode == "custom":
            start = _iso_day(raw.get("start_date"), "Tanggal awal")
            end = _iso_day(raw.get("end_date"), "Tanggal akhir")
            if start > end:
                raise LayoutError(400, "LAYOUT_INVALID", "Tanggal akhir sebelum tanggal awal")
            out["start_date"] = start
            out["end_date"] = end
    elif mode not in (None, "", "global"):
        raise LayoutError(400, "LAYOUT_INVALID", "Widget ini tidak mendukung filter periode")
    visual = raw.get("visual")
    if visual not in (None, ""):
        if widget_id not in visual_widgets or visual not in VISUALS:
            raise LayoutError(400, "LAYOUT_INVALID", "Tipe visual tidak didukung")
        out["visual"] = visual
    return out


def validate_geometry(widget_id: str, item: dict, spec: Optional[dict] = None) -> dict:
    sizes = SIZES if spec is None else spec["sizes"]
    spec = sizes.get(widget_id)
    if not spec:
        raise LayoutError(400, "LAYOUT_WIDGET_UNKNOWN", "Widget tidak dikenal")
    min_w, max_w, min_h, max_h, _dw, _dh = spec
    x = _int_field(item.get("x"), "x")
    y = _int_field(item.get("y"), "y")
    w = _int_field(item.get("w"), "w")
    h = _int_field(item.get("h"), "h")
    if w < min_w or w > max_w or h < min_h or h > max_h:
        raise LayoutError(400, "LAYOUT_INVALID", "Ukuran widget di luar batas")
    if x < 0 or y < 0 or x + w > COLUMNS or y + h > MAX_Y + 1:
        raise LayoutError(400, "LAYOUT_INVALID", "Posisi widget di luar grid")
    return {"x": x, "y": y, "w": w, "h": h}


def _boxes_overlap(x: int, y: int, w: int, h: int, other: dict) -> bool:
    return _overlap({"x": x, "y": y, "w": w, "h": h}, other)


def first_fit(placed: Sequence[dict], w: int, h: int) -> Dict[str, int]:
    y = 0
    while y <= MAX_Y - h:
        x = 0
        while x + w <= COLUMNS:
            if not any(_boxes_overlap(x, y, w, h, other) for other in placed):
                return {"x": x, "y": y}
            x += 1
        y += 1
    raise LayoutError(400, "LAYOUT_INVALID", "Tata letak tidak muat di grid")


def assert_no_overlap(items: Sequence[dict]) -> None:
    for index, item in enumerate(items):
        for other in items[index + 1:]:
            if _overlap(item, other):
                raise LayoutError(400, "LAYOUT_INVALID", "Widget saling menutupi")


def default_widgets(permissions: Iterable[str], spec: Optional[dict] = None) -> List[dict]:
    placed: List[dict] = []
    order = DEFAULT_ORDER if spec is None else spec["order"]
    sizes = SIZES if spec is None else spec["sizes"]
    period_widgets = PERIOD_WIDGETS if spec is None else spec["period"]
    for widget_id in order:
        if not finance_widget_allowed(widget_id, permissions, spec):
            continue
        _min_w, _max_w, _min_h, _max_h, width, height = sizes[widget_id]
        spot = first_fit(placed, width, height)
        settings = {"period_mode": "global"} if widget_id in period_widgets else {}
        placed.append({
            "widget_id": widget_id,
            "x": spot["x"],
            "y": spot["y"],
            "w": width,
            "h": height,
            "settings": settings,
        })
    return placed


def _public_widget(item: dict) -> dict:
    return {
        "widget_id": item["widget_id"],
        "x": item["x"],
        "y": item["y"],
        "w": item["w"],
        "h": item["h"],
        "settings": dict(item.get("settings") or {}),
    }


def classify_stored(items: Any, permissions: Iterable[str], spec: Optional[dict] = None) -> dict:
    """Split a stored list without writing. Invalid and unknown rows are ignored."""
    visible: List[dict] = []
    withheld: List[dict] = []
    ignored: List[dict] = []
    if not isinstance(items, list):
        return {"widgets": [], "withheld": [], "ignored": [{"widget_id": "", "reason": "invalid"}]}
    for raw in items:
        if not isinstance(raw, dict):
            ignored.append({"widget_id": "", "reason": "invalid"})
            continue
        widget_id = str(raw.get("widget_id") or "").strip()
        ready = READY_PERMISSION if spec is None else spec["ready"]
        if widget_id not in ready:
            ignored.append({"widget_id": widget_id, "reason": "unknown"})
            continue
        try:
            box = validate_geometry(widget_id, raw, spec)
            settings = clean_settings(widget_id, raw.get("settings") or {}, spec)
        except LayoutError:
            ignored.append({"widget_id": widget_id, "reason": "invalid"})
            continue
        row = {"widget_id": widget_id, **box, "settings": settings}
        if finance_widget_allowed(widget_id, permissions, spec):
            visible.append(row)
        else:
            withheld.append({"widget_id": widget_id, "reason": "forbidden"})
    try:
        assert_no_overlap(visible)
    except LayoutError:
        ignored.extend({"widget_id": row["widget_id"], "reason": "overlap"} for row in visible)
        visible = []
    return {"widgets": [_public_widget(row) for row in visible], "withheld": withheld, "ignored": ignored}


def validate_payload(widgets: Any, permissions: Iterable[str], spec: Optional[dict] = None) -> List[dict]:
    if not isinstance(widgets, list):
        raise LayoutError(400, "LAYOUT_INVALID", "Daftar widget tidak valid")
    max_ready = MAX_READY if spec is None else spec["max_ready"]
    ready = READY_PERMISSION if spec is None else spec["ready"]
    if len(widgets) > max_ready:
        raise LayoutError(400, "LAYOUT_INVALID", "Jumlah widget melewati batas")
    cleaned: List[dict] = []
    seen = set()
    for raw in widgets:
        if not isinstance(raw, dict):
            raise LayoutError(400, "LAYOUT_INVALID", "Widget tidak valid")
        widget_id = str(raw.get("widget_id") or "").strip()
        if widget_id not in ready:
            raise LayoutError(400, "LAYOUT_WIDGET_UNKNOWN", "Widget tidak dikenal")
        if widget_id in seen:
            raise LayoutError(400, "LAYOUT_INVALID", "Widget duplikat")
        seen.add(widget_id)
        if not finance_widget_allowed(widget_id, permissions, spec):
            raise LayoutError(403, "LAYOUT_FORBIDDEN", "Widget tidak diizinkan")
        box = validate_geometry(widget_id, raw, spec)
        settings = clean_settings(widget_id, raw.get("settings") or {}, spec)
        cleaned.append({"widget_id": widget_id, **box, "settings": settings})
    assert_no_overlap(cleaned)
    return cleaned


def merge_preserved(payload: Sequence[dict], stored: Any, permissions: Iterable[str], spec: Optional[dict] = None) -> List[dict]:
    """Keep widgets the caller cannot edit so a save does not delete them."""
    ready = READY_PERMISSION if spec is None else spec["ready"]
    sizes = SIZES if spec is None else spec["sizes"]
    max_preserved = MAX_PRESERVED if spec is None else spec["max_preserved"]
    payload_ids = {item["widget_id"] for item in payload}
    preserved_raw: List[dict] = []
    if isinstance(stored, list):
        for raw in stored:
            if not isinstance(raw, dict):
                continue
            widget_id = str(raw.get("widget_id") or "").strip()
            if not widget_id or widget_id in payload_ids:
                continue
            if widget_id in ready and finance_widget_allowed(widget_id, permissions, spec):
                continue
            preserved_raw.append(raw)
    if len(preserved_raw) > max_preserved:
        raise LayoutError(400, "LAYOUT_INVALID", "Tata letak tersimpan melebihi batas")
    placed = [dict(item) for item in payload]
    for raw in preserved_raw:
        widget_id = str(raw.get("widget_id") or "").strip()
        try:
            if widget_id in sizes:
                box = validate_geometry(widget_id, raw, spec)
                settings = clean_settings(widget_id, raw.get("settings") or {}, spec)
                width, height = box["w"], box["h"]
            else:
                settings = {}
                width, height = 4, 3
        except LayoutError:
            settings = {}
            width, height = 4, 3
        spot = first_fit(placed, width, height)
        placed.append({
            "widget_id": widget_id,
            "x": spot["x"],
            "y": spot["y"],
            "w": width,
            "h": height,
            "settings": settings,
        })
    return placed


def _view(version: int, source: str, widgets: List[dict], withheld: List[dict], ignored: List[dict], spec: Optional[dict] = None) -> dict:
    return {
        "dashboard_key": DASHBOARD_KEY if spec is None else spec["key"],
        "version": version,
        "source": source,
        "widgets": widgets,
        "withheld": withheld,
        "ignored": ignored,
    }


async def ensure_layout_index(db) -> None:
    key = id(db)
    if key in _INDEXED:
        return
    await db[COLLECTION].create_index(
        [("user_id", 1), ("dashboard_key", 1)],
        unique=True,
        name="finance_layout_user",
    )
    _INDEXED.add(key)


def _require_user_id(user: Optional[dict]) -> str:
    uid = session_user_id(user)
    if not uid:
        raise LayoutError(401, "LAYOUT_UNAUTHENTICATED", "Sesi tidak memiliki identitas pengguna")
    return uid


def _dashboard_key(spec: Optional[dict]) -> str:
    return DASHBOARD_KEY if spec is None else spec["key"]


async def get_finance_layout(db, user: Optional[dict], permissions: Iterable[str], spec: Optional[dict] = None) -> dict:
    await ensure_layout_index(db)
    uid = _require_user_id(user)
    key = _dashboard_key(spec)
    doc = await db[COLLECTION].find_one({"user_id": uid, "dashboard_key": key}, {"_id": 0})
    if not doc:
        return _view(0, "default", default_widgets(permissions, spec), [], [], spec)
    classified = classify_stored(doc.get("widgets"), permissions, spec)
    return _view(int(doc.get("version") or 0), "saved", classified["widgets"], classified["withheld"], classified["ignored"], spec)


async def save_finance_layout(db, user: Optional[dict], permissions: Iterable[str], version: Any, widgets: Any, spec: Optional[dict] = None) -> dict:
    await ensure_layout_index(db)
    uid = _require_user_id(user)
    key = _dashboard_key(spec)
    if type(version) is not int or version < 0:
        raise LayoutError(400, "LAYOUT_INVALID", "Versi tata letak tidak valid")
    payload = validate_payload(widgets, permissions, spec)
    now = datetime.now(timezone.utc).isoformat()
    current = await db[COLLECTION].find_one({"user_id": uid, "dashboard_key": key}, {"_id": 0})
    if version == 0:
        if current:
            raise LayoutError(409, "LAYOUT_CONFLICT", "Tata letak berubah di tab lain.", int(current.get("version") or 0))
        merged = merge_preserved(payload, [], permissions, spec)
        doc = {
            "id": uuid4().hex,
            "user_id": uid,
            "dashboard_key": key,
            "version": 1,
            "widgets": merged,
            "updated_at": now,
        }
        try:
            await db[COLLECTION].insert_one(doc)
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                fresh = await db[COLLECTION].find_one({"user_id": uid, "dashboard_key": key}, {"_id": 0})
                raise LayoutError(409, "LAYOUT_CONFLICT", "Tata letak berubah di tab lain.", int((fresh or {}).get("version") or 0)) from exc
            raise
        classified = classify_stored(merged, permissions, spec)
        return _view(1, "saved", classified["widgets"], classified["withheld"], classified["ignored"], spec)
    if not current or int(current.get("version") or 0) != version:
        found = int((current or {}).get("version") or 0)
        raise LayoutError(409, "LAYOUT_CONFLICT", "Tata letak berubah di tab lain.", found)
    merged = merge_preserved(payload, current.get("widgets"), permissions, spec)
    result = await db[COLLECTION].update_one(
        {"user_id": uid, "dashboard_key": key, "version": version},
        {"$set": {"widgets": merged, "updated_at": now}, "$inc": {"version": 1}},
    )
    if result.modified_count != 1:
        fresh = await db[COLLECTION].find_one({"user_id": uid, "dashboard_key": key}, {"_id": 0})
        raise LayoutError(409, "LAYOUT_CONFLICT", "Tata letak berubah di tab lain.", int((fresh or {}).get("version") or 0))
    classified = classify_stored(merged, permissions, spec)
    return _view(version + 1, "saved", classified["widgets"], classified["withheld"], classified["ignored"], spec)


async def reset_finance_layout(db, user: Optional[dict], permissions: Iterable[str], spec: Optional[dict] = None) -> dict:
    await ensure_layout_index(db)
    uid = _require_user_id(user)
    await db[COLLECTION].delete_one({"user_id": uid, "dashboard_key": _dashboard_key(spec)})
    return _view(0, "default", default_widgets(permissions, spec), [], [], spec)
