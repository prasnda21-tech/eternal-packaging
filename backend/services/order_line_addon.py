"""Explicit Barang Utama ↔ Add On (process) line relations.

Schema (backward-compatible, opt-in on line_items):
- line_id: stable identity for the PO line (preferred)
- parent_line_id: Add On process line points at parent line_id
- parent_line_idx: optional integer fallback when line_id absent

Rules:
- Never invent parent links from item names.
- Scope is per order_id + per parent line — never global by master item,
  and never a whole-PO gate that blocks unrelated sibling lines.
- SERVICE / excluded masters are process work — not physical stock demand.
- FULLY_COMPLETED: parent + every linked required process has operator
  qty_completed covering its ordered qty on THIS PO. Physical siap qty =
  parent ordered qty (not sum of process lines).
- Partial / ambiguous: fail-closed (no READY partial via min()).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

STATUS_FULLY_COMPLETED = "FULLY_COMPLETED"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_UNVERIFIED = "UNVERIFIED"


def line_identity(line: Optional[dict], idx: int) -> str:
    if not isinstance(line, dict):
        return f"idx:{idx}"
    for key in ("line_id", "id"):
        raw = str(line.get(key) or "").strip()
        if raw:
            return raw
    return f"idx:{idx}"


def line_cancelled(line: Optional[dict]) -> bool:
    if not isinstance(line, dict):
        return True
    return bool(line.get("cancelled") or line.get("is_cancelled"))


def resolve_parent_idx(
    line: dict,
    lines: Sequence[dict],
    *,
    line_ids: Optional[Dict[str, int]] = None,
) -> Optional[int]:
    """Return parent line index when an explicit relation is present and valid."""
    if not isinstance(line, dict):
        return None
    ids = line_ids
    if ids is None:
        ids = {
            line_identity(row, i): i
            for i, row in enumerate(lines)
            if isinstance(row, dict)
        }
    parent_id = str(line.get("parent_line_id") or "").strip()
    if parent_id and parent_id in ids:
        return ids[parent_id]
    raw_idx = line.get("parent_line_idx")
    if raw_idx is None or str(raw_idx).strip() == "":
        return None
    try:
        idx = int(raw_idx)
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= len(lines):
        return None
    if not isinstance(lines[idx], dict):
        return None
    return idx


def build_line_id_index(lines: Sequence[dict]) -> Dict[str, int]:
    index: Dict[str, int] = {}
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        identity = line_identity(row, i)
        # First wins — duplicate line_id is ambiguous; later resolve fails closed.
        index.setdefault(identity, i)
    return index


def duplicate_line_id_error(lines: Sequence[dict]) -> Optional[str]:
    """Reject ambiguous duplicate line_id values (excluding synthetic idx:N)."""
    seen: Dict[str, int] = {}
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        lid = str(row.get("line_id") or row.get("id") or "").strip()
        if not lid:
            continue
        if lid in seen:
            return f"line_id duplikat: '{lid}' (baris {seen[lid] + 1} dan {i + 1})."
        seen[lid] = i
    return None


def process_children_for_parent(
    parent_idx: int,
    lines: Sequence[dict],
    *,
    process_master_ids: Set[str],
    master_ids: Sequence[str],
    is_print_line,
) -> Tuple[List[int], bool]:
    """Return (linked child idxs, has_unlinked_process_on_order).

    has_unlinked_process_on_order is True when any active process print line
    lacks a valid parent relation (ambiguous for stock attribution).
    """
    ids = build_line_id_index(lines)
    linked: List[int] = []
    unlinked_process = False
    for idx, line in enumerate(lines):
        if not isinstance(line, dict) or line_cancelled(line):
            continue
        mid = master_ids[idx] if idx < len(master_ids) else ""
        if not mid or mid not in process_master_ids:
            continue
        if not is_print_line(line):
            continue
        parent = resolve_parent_idx(line, lines, line_ids=ids)
        if parent is None:
            unlinked_process = True
            continue
        if parent == parent_idx:
            linked.append(idx)
    return linked, unlinked_process


def _qty(value: Any) -> float:
    try:
        if value is None or str(value).strip() == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def line_operator_completed(order: dict, line_idx: int) -> float:
    """Sum operator qty_completed for one line_idx (ignores unassigned splits)."""
    total = 0.0
    for split in order.get("operator_splits") or []:
        if not isinstance(split, dict):
            continue
        raw = split.get("line_idx")
        if raw is None or str(raw).strip() == "":
            continue
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            continue
        if idx == int(line_idx):
            total += max(_qty(split.get("qty_completed")), 0.0)
    return total


@dataclass(frozen=True)
class ProcessGroupCompletion:
    """Per-PO + per-parent process group completion for packing / stock / SJ."""

    status: str
    parent_idx: int
    parent_ordered: float
    parent_completed: float
    physical_ready_qty: float
    child_idxs: Tuple[int, ...]
    reason: str = ""

    @property
    def fully_completed(self) -> bool:
        return self.status == STATUS_FULLY_COMPLETED


def evaluate_process_group(
    order: dict,
    parent_idx: int,
    *,
    process_master_ids: Optional[Set[str]] = None,
    master_ids: Optional[Sequence[str]] = None,
    is_print_line=None,
    completed_by_idx: Optional[Dict[int, float]] = None,
    require_linked_children: bool = False,
) -> ProcessGroupCompletion:
    """Evaluate one barang induk + its linked process Add Ons on THIS order.

    FULLY_COMPLETED when parent and every linked required process have
    operator confirmation covering ordered qty. Physical siap = parent qty only.

    Partial never invents siap via min(). Ambiguous relation → UNVERIFIED.
    """
    from quantity_rules import is_print_line as _default_is_print
    from quantity_rules import is_process_only_service_line, is_service_process_line

    is_print = is_print_line or _default_is_print
    lines = list(order.get("line_items") or [])
    if parent_idx < 0 or parent_idx >= len(lines) or not isinstance(lines[parent_idx], dict):
        return ProcessGroupCompletion(
            status=STATUS_UNVERIFIED,
            parent_idx=parent_idx,
            parent_ordered=0.0,
            parent_completed=0.0,
            physical_ready_qty=0.0,
            child_idxs=(),
            reason="Line induk tidak valid.",
        )
    parent = lines[parent_idx]
    if line_cancelled(parent):
        return ProcessGroupCompletion(
            status=STATUS_INCOMPLETE,
            parent_idx=parent_idx,
            parent_ordered=0.0,
            parent_completed=0.0,
            physical_ready_qty=0.0,
            child_idxs=(),
            reason="Line induk dibatalkan.",
        )

    ordered = max(_qty(parent.get("quantity")), 0.0)
    if completed_by_idx is not None and parent_idx in completed_by_idx:
        parent_done = min(max(_qty(completed_by_idx.get(parent_idx)), 0.0), ordered)
    else:
        parent_done = min(line_operator_completed(order, parent_idx), ordered)

    # Resolve linked process children (process-only SERVICE).
    mids = list(master_ids) if master_ids is not None else [
        str((li or {}).get("master_item_id") or "").strip() if isinstance(li, dict) else ""
        for li in lines
    ]
    process_ids = set(process_master_ids or set())
    # Process-only SERVICE lines are Add Ons; physical_fulfillment SERVICE is not.
    for i, li in enumerate(lines):
        if isinstance(li, dict) and is_process_only_service_line(li):
            mid = mids[i] if i < len(mids) else ""
            if mid:
                process_ids.add(mid)
        elif isinstance(li, dict) and is_service_process_line(li):
            continue

    children, unlinked = process_children_for_parent(
        parent_idx,
        lines,
        process_master_ids=process_ids,
        master_ids=mids,
        is_print_line=is_print,
    )

    # Duplicate line_id on this PO → ambiguous identities.
    if duplicate_line_id_error(lines):
        return ProcessGroupCompletion(
            status=STATUS_UNVERIFIED,
            parent_idx=parent_idx,
            parent_ordered=ordered,
            parent_completed=parent_done,
            physical_ready_qty=0.0,
            child_idxs=tuple(children),
            reason="line_id duplikat — relasi ambigu.",
        )

    if require_linked_children and not children and unlinked:
        return ProcessGroupCompletion(
            status=STATUS_UNVERIFIED,
            parent_idx=parent_idx,
            parent_ordered=ordered,
            parent_completed=parent_done,
            physical_ready_qty=0.0,
            child_idxs=(),
            reason="Ada proses Add On tanpa parent_line_id yang valid.",
        )

    if not children:
        # Inventory-only parent on this group: own operator progress.
        if parent_done + 1e-9 >= ordered and ordered > 0:
            return ProcessGroupCompletion(
                status=STATUS_FULLY_COMPLETED,
                parent_idx=parent_idx,
                parent_ordered=ordered,
                parent_completed=parent_done,
                physical_ready_qty=ordered,
                child_idxs=(),
            )
        return ProcessGroupCompletion(
            status=STATUS_INCOMPLETE,
            parent_idx=parent_idx,
            parent_ordered=ordered,
            parent_completed=parent_done,
            physical_ready_qty=0.0,
            child_idxs=(),
            reason="Progres operator induk belum mencapai target.",
        )

    # Linked process group: every child + parent must hit ordered target.
    child_pairs: List[Tuple[int, float, float]] = []
    all_done = parent_done + 1e-9 >= ordered and ordered > 0
    for cidx in children:
        child = lines[cidx]
        c_ordered = max(_qty(child.get("quantity")), 0.0)
        if completed_by_idx is not None and cidx in completed_by_idx:
            c_done = min(max(_qty(completed_by_idx.get(cidx)), 0.0), c_ordered)
        else:
            c_done = min(line_operator_completed(order, cidx), c_ordered)
        child_pairs.append((cidx, c_ordered, c_done))
        if c_done + 1e-9 < c_ordered or c_ordered <= 0:
            all_done = False

    if all_done:
        return ProcessGroupCompletion(
            status=STATUS_FULLY_COMPLETED,
            parent_idx=parent_idx,
            parent_ordered=ordered,
            parent_completed=parent_done,
            physical_ready_qty=ordered,
            child_idxs=tuple(children),
            reason="Semua proses wajib pada kelompok induk ini selesai.",
        )

    return ProcessGroupCompletion(
        status=STATUS_INCOMPLETE,
        parent_idx=parent_idx,
        parent_ordered=ordered,
        parent_completed=parent_done,
        physical_ready_qty=0.0,  # fail-closed: no partial siap via min()
        child_idxs=tuple(children),
        reason="Masih ada proses Add On wajib yang belum selesai pada kelompok ini.",
    )


def _process_master_ids_for_order(
    order: dict,
    *,
    process_master_ids: Optional[Set[str]] = None,
) -> Tuple[List[str], Set[str]]:
    from quantity_rules import is_process_only_service_line, is_service_process_line

    lines = list(order.get("line_items") or [])
    mids = [
        str((li or {}).get("master_item_id") or "").strip() if isinstance(li, dict) else ""
        for li in lines
    ]
    process_ids = set(process_master_ids or set())
    for i, li in enumerate(lines):
        # SERVICE with physical_fulfillment is customer-goods fulfillment, not a
        # process Add On that must link to an inventory induk.
        if isinstance(li, dict) and is_service_process_line(li) and not is_process_only_service_line(li):
            continue
        if isinstance(li, dict) and is_process_only_service_line(li):
            mid = mids[i] if i < len(mids) else ""
            if mid:
                process_ids.add(mid)
    return mids, process_ids


def parent_has_linked_process_children(
    order: dict,
    parent_idx: int,
    *,
    process_master_ids: Optional[Set[str]] = None,
) -> bool:
    from quantity_rules import is_print_line

    lines = list(order.get("line_items") or [])
    mids, process_ids = _process_master_ids_for_order(
        order, process_master_ids=process_master_ids,
    )
    children, _ = process_children_for_parent(
        parent_idx,
        lines,
        process_master_ids=process_ids,
        master_ids=mids,
        is_print_line=is_print_line,
    )
    return bool(children)


def order_has_unlinked_process_addons(
    order: dict,
    *,
    process_master_ids: Optional[Set[str]] = None,
) -> bool:
    """True when an active SERVICE/process print line lacks a valid parent link.

    Ambiguous Add On work must not silently unlock packing/siap for inventory
    lines. Never invents which induk the orphan belongs to.
    """
    from quantity_rules import is_print_line

    lines = list(order.get("line_items") or [])
    if not lines:
        return False
    mids, process_ids = _process_master_ids_for_order(
        order, process_master_ids=process_master_ids,
    )
    if not process_ids:
        return False
    # Any parent index reports the same unlinked flag for the whole PO.
    _, unlinked = process_children_for_parent(
        0,
        lines,
        process_master_ids=process_ids,
        master_ids=mids,
        is_print_line=is_print_line,
    )
    return bool(unlinked)


def conservative_full_process_ready(
    *,
    parent_ordered: float,
    parent_completed: float,
    child_ordered_completed: Iterable[Tuple[float, float]],
) -> float:
    """Diagnostic upper bound only — packing/stock use evaluate_process_group."""
    ready = min(max(parent_ordered, 0.0), max(parent_completed, 0.0))
    for ordered, completed in child_ordered_completed:
        child_ready = min(max(ordered, 0.0), max(completed, 0.0))
        ready = min(ready, child_ready)
    return max(ready, 0.0)


def ensure_line_identity_fields(line: dict, idx: int) -> dict:
    """Return a shallow copy with line_id filled when missing (non-mutating helper)."""
    row = dict(line or {})
    if not str(row.get("line_id") or "").strip():
        row["line_id"] = line_identity(row, idx)
    return row


def sync_parent_line_relations(lines: Sequence[dict]) -> None:
    """Persist dual parent keys from an already-resolved explicit relation.

    Mutates in place. For every Add On whose parent resolves via parent_line_id
    or parent_line_idx:
    - parent_line_idx is set to the resolved index
    - parent_line_id is set to the induk's stored line_id when present

    Never invents parents from name/qty similarity. Lines that do not resolve
    are left untouched (legacy ambiguous stays fail-closed / UNVERIFIED).
    """
    if not lines:
        return
    ids = build_line_id_index(lines)
    for i, line in enumerate(lines):
        if not isinstance(line, dict) or line_cancelled(line):
            continue
        parent = resolve_parent_idx(line, lines, line_ids=ids)
        if parent is None:
            continue
        parent_row = lines[parent]
        if not isinstance(parent_row, dict):
            continue
        line["parent_line_idx"] = int(parent)
        parent_lid = str(parent_row.get("line_id") or parent_row.get("id") or "").strip()
        if parent_lid:
            line["parent_line_id"] = parent_lid


def _line_item_type(line: Optional[dict]) -> str:
    if not isinstance(line, dict):
        return ""
    return str(line.get("item_type") or "").strip().upper()


def validate_parent_relation(
    line: dict,
    lines: Sequence[dict],
    *,
    idx: int,
) -> Optional[str]:
    """Return error message if parent relation is present but invalid; else None."""
    if not isinstance(line, dict):
        return "Line tidak valid."
    has_parent = bool(
        str(line.get("parent_line_id") or "").strip()
        or (line.get("parent_line_idx") is not None and str(line.get("parent_line_idx")).strip() != "")
    )
    if not has_parent:
        return None
    parent = resolve_parent_idx(line, lines)
    if parent is None:
        return "parent_line_id / parent_line_idx tidak merujuk ke line induk yang valid."
    if parent == idx:
        return "Add On tidak boleh merujuk dirinya sendiri sebagai induk."
    if line_cancelled(lines[parent]):
        return "Barang induk tidak aktif / dibatalkan."
    child_type = _line_item_type(line)
    parent_type = _line_item_type(lines[parent])
    if child_type == "SERVICE" and parent_type == "SERVICE":
        return "Induk Add On SERVICE tidak valid — pilih barang fisik (INVENTORY)."
    if parent_type == "SERVICE":
        return "Barang induk tidak boleh bertipe SERVICE."
    seen = {idx}
    cur: Optional[int] = parent
    while cur is not None:
        if cur in seen:
            return "Relasi induk Add On membentuk cycle."
        seen.add(cur)
        parent_line = lines[cur] if 0 <= cur < len(lines) else None
        if not isinstance(parent_line, dict):
            break
        nxt = resolve_parent_idx(parent_line, lines)
        cur = nxt
    return None


def validate_lines_parent_graph(lines: Sequence[dict]) -> Optional[str]:
    """Validate duplicate ids and every present parent relation on the PO."""
    dup = duplicate_line_id_error(lines)
    if dup:
        return dup
    for i, row in enumerate(lines):
        if not isinstance(row, dict) or line_cancelled(row):
            continue
        err = validate_parent_relation(row, lines, idx=i)
        if err:
            return f"Baris #{i + 1}: {err}"
    return None


def validate_service_print_requires_parent(lines: Sequence[dict]) -> Optional[str]:
    """Create-path rule: SERVICE + Cetak must name a valid non-SERVICE induk.

    Does not invent parents. Missing/invalid/self/SERVICE parent → error.
    Non-print SERVICE and INVENTORY lines are unchanged. Historical reads never
    call this; only create (and optional future edit) enforce it.
    """
    from quantity_rules import is_print_line

    graph_err = validate_lines_parent_graph(lines)
    if graph_err:
        return graph_err
    for i, row in enumerate(lines):
        if not isinstance(row, dict) or line_cancelled(row):
            continue
        if _line_item_type(row) != "SERVICE":
            continue
        if not is_print_line(row):
            continue
        parent = resolve_parent_idx(row, lines)
        if parent is None:
            return (
                f"Baris #{i + 1}: Jasa Cetak (Add On warna) wajib memilih barang "
                "induk non-SERVICE dalam PO yang sama (parent_line_id)."
            )
        parent_type = _line_item_type(lines[parent])
        if parent_type == "SERVICE":
            return (
                f"Baris #{i + 1}: barang induk Add On tidak boleh bertipe SERVICE."
            )
    return None
