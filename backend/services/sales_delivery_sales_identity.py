"""Sales identity compatibility for Surat Jalan creation.

Single-PO: Sales follows the associated PO snapshot (Stage C.1).
Multi-PO: all source POs must share one unambiguous Sales identity
before a combined SJ may be created. Fail closed. No fuzzy matching.

This module is intentionally independent of multi-PO allocation engines so it
can be wired into `validate_orders_merge_compatible` / multi-source create
when PR #710 lands without rewriting quantity logic.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from fastapi import HTTPException

SALES_MERGE_REJECT_PREFIX = "Penggabungan PO ditolak:"
_SALES_MASTER_COLLECTION = "sales_master"


def po_sales_snapshot(order: Optional[Mapping]) -> str:
    """Authoritative Sales label from one PO document snapshot."""
    if not isinstance(order, Mapping):
        return ""
    return str(
        order.get("sales")
        or order.get("seller")
        or order.get("sales_rep")
        or order.get("sales_name")
        or ""
    ).strip()


def po_sales_master_id(order: Optional[Mapping]) -> str:
    if not isinstance(order, Mapping):
        return ""
    return str(order.get("sales_master_id") or "").strip()


def _po_ref(order: Mapping) -> str:
    return str(order.get("po_number") or order.get("id") or "?").strip() or "?"


def _reject(detail: str) -> None:
    msg = detail if detail.startswith(SALES_MERGE_REJECT_PREFIX) else f"{SALES_MERGE_REJECT_PREFIX} {detail}"
    raise HTTPException(400, msg)


def validate_orders_sales_identity_compatible(
    orders: Sequence[Mapping],
    *,
    proven_master_labels: Optional[Mapping[str, str]] = None,
) -> None:
    """Fail-closed Sales identity check for the final authoritative source PO set.

    Rules:
    - len <= 1: no multi-PO check (single-PO create uses PO snapshot alone).
    - All nonempty same sales_master_id: OK.
    - Different sales_master_id: reject.
    - All legacy (no id): exact nonempty Sales labels must match.
    - Mixed id / no-id: reject unless proven_master_labels proves exact
      equivalence (master id → official sales_name equals the legacy label).
    - Any blank identity on a multi-PO set: reject.
    - Never invents sales_master_id. Never picks the first PO silently.
    """
    rows: List[Mapping] = [o for o in orders if isinstance(o, Mapping)]
    if len(rows) <= 1:
        return

    proven = {
        str(k).strip(): str(v).strip()
        for k, v in dict(proven_master_labels or {}).items()
        if str(k).strip() and str(v).strip()
    }

    ids = [po_sales_master_id(o) for o in rows]
    labels = [po_sales_snapshot(o) for o in rows]
    has_id = [bool(mid) for mid in ids]

    if all(has_id):
        unique_ids = set(ids)
        if len(unique_ids) != 1:
            refs = ", ".join(f"{_po_ref(o)}={mid}" for o, mid in zip(rows, ids))
            _reject(
                "sales_master_id harus sama pada semua PO sumber. "
                f"Ditemukan berbeda: {refs}. Tidak memilih Sales dari PO pertama."
            )
        return

    if not any(has_id):
        if any(not lab for lab in labels):
            blanks = [_po_ref(o) for o, lab in zip(rows, labels) if not lab]
            _reject(
                "identitas Sales tidak boleh kosong pada penggabungan PO. "
                f"PO tanpa Sales: {', '.join(blanks)}."
            )
        unique_labels = set(labels)
        if len(unique_labels) != 1:
            refs = ", ".join(f"{_po_ref(o)}='{lab}'" for o, lab in zip(rows, labels))
            _reject(
                "label Sales historis harus sama persis (bukan fuzzy). "
                f"Ditemukan berbeda: {refs}."
            )
        return

    # Mixed: some have sales_master_id, some only legacy labels.
    present_ids = {mid for mid in ids if mid}
    if len(present_ids) != 1:
        _reject(
            "campuran sales_master_id tidak kompatibel. "
            "Semua PO harus memakai satu sales_master_id yang sama, "
            "atau seluruhnya legacy tanpa id dengan label Sales sama persis."
        )
    master_id = next(iter(present_ids))
    official = proven.get(master_id, "")
    if not official:
        _reject(
            "campuran PO ber-sales_master_id dan PO legacy ditolak. "
            "Tidak ada relasi otoritatif (sales_master.sales_name) yang membuktikan "
            "kesetaraan label. Lengkapi sales_master_id pada semua PO, atau "
            "samakan identitas lewat Master Sales sebelum menggabung."
        )
    for order, mid, lab in zip(rows, ids, labels):
        if mid:
            continue
        if not lab:
            _reject(
                f"PO '{_po_ref(order)}' tidak punya identitas Sales (id/label kosong)."
            )
        if lab != official:
            _reject(
                f"PO legacy '{_po_ref(order)}' label Sales '{lab}' tidak sama persis "
                f"dengan sales_name Master '{official}' (id={master_id}). "
                "Pencocokan fuzzy tidak diizinkan."
            )


async def load_proven_sales_master_labels(
    db: Any,
    orders: Sequence[Mapping],
) -> Dict[str, str]:
    """Load exact sales_name for sales_master ids present on the given POs.

    Used only as authoritative proof for mixed id/legacy compatibility.
    Does not invent ids. Inactive masters remain valid proof for historical labels.
    """
    ids = sorted({po_sales_master_id(o) for o in orders if po_sales_master_id(o)})
    if not ids:
        return {}
    out: Dict[str, str] = {}
    cursor = db[_SALES_MASTER_COLLECTION].find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "sales_name": 1},
    )
    async for doc in cursor:
        if not isinstance(doc, dict):
            continue
        sid = str(doc.get("id") or "").strip()
        name = str(doc.get("sales_name") or "").strip()
        if sid and name:
            out[sid] = name
    return out


def resolve_sj_sales_from_orders(
    orders: Sequence[Mapping],
    *,
    proven_master_labels: Optional[Mapping[str, str]] = None,
) -> Tuple[str, Optional[str]]:
    """After validation, derive SJ sales snapshot from the shared identity.

    Returns (sales_label, sales_master_id|None).
    Does not read client payload. Does not invent ids.
    """
    rows: List[Mapping] = [o for o in orders if isinstance(o, Mapping)]
    if not rows:
        return "", None
    if len(rows) == 1:
        mid = po_sales_master_id(rows[0]) or None
        return po_sales_snapshot(rows[0]), mid

    validate_orders_sales_identity_compatible(
        rows, proven_master_labels=proven_master_labels
    )
    ids = [po_sales_master_id(o) for o in rows]
    labels = [po_sales_snapshot(o) for o in rows]
    present_ids = [mid for mid in ids if mid]
    if present_ids:
        master_id = present_ids[0]
        proven = {
            str(k).strip(): str(v).strip()
            for k, v in dict(proven_master_labels or {}).items()
            if str(k).strip() and str(v).strip()
        }
        # Prefer official master label when available; else any nonempty PO snapshot
        # with that id (all ids already validated equal).
        label = proven.get(master_id) or next((lab for lab in labels if lab), "")
        if not label:
            # All rows with this id — use first snapshot field even if empty after validate
            # (all-id path allows empty labels).
            label = po_sales_snapshot(rows[0])
        return label, master_id
    # Legacy-only: validated identical nonempty labels
    return labels[0], None
