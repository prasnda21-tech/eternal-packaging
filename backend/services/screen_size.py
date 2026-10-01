"""Planning metadata: Screen Besar / Screen Kecil.

Operator allocations store their own screen. A print line item keeps a
screen only as a legacy/shared mirror when every allocation on that line
agrees. Not a workflow stage. Not Qty Screen. Not derived from product name.
"""

SCREEN_SIZE_LARGE = "large"
SCREEN_SIZE_SMALL = "small"
SCREEN_SIZE_VALUES = (SCREEN_SIZE_LARGE, SCREEN_SIZE_SMALL)

SCREEN_SIZE_LABELS = {
    SCREEN_SIZE_LARGE: "Screen Besar",
    SCREEN_SIZE_SMALL: "Screen Kecil",
}


def normalize_screen_size(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "":
        return None
    if text in SCREEN_SIZE_VALUES:
        return text
    raise ValueError("screen_size hanya boleh large atau small.")


def line_screen_size(order, line_idx):
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        return None
    items = order.get("line_items") or []
    if idx < 0 or idx >= len(items):
        return None
    try:
        return normalize_screen_size((items[idx] or {}).get("screen_size"))
    except ValueError:
        return None


def allocation_screen_size(split, order=None):
    """Screen stored on one operator allocation, else the legacy line mirror."""
    own = None
    if isinstance(split, dict):
        try:
            own = normalize_screen_size(split.get("screen_size"))
        except ValueError:
            own = None
    if own:
        return own
    if order is None:
        return None
    return line_screen_size(order, split.get("line_idx") if isinstance(split, dict) else None)
