"""Planning metadata: Screen Besar / Screen Kecil on a print line item.

Not a workflow stage. Not Qty Screen. Not derived from product name.
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
