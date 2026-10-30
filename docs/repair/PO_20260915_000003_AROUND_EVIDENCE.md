# PO/2026/09/15/000003 (AROUND) — Owner live evidence vs B31

**Waktu:** 2026-10-31 ~06:45 Asia/Jakarta  
**Mode:** READ-ONLY analysis + regression test — **no writes**  
**order_id:** `e2978ceb-1934-4c7b-8800-9b2c39482e37`  
**B31:** 2 class-B rows (SERVICE idx 1 & 2 → print parent idx 0 / SKU 100003)

## Owner live UI

| Line | Role | Qty (Owner) |
|------|------|------------:|
| 12 oz PET Datar 11 gr SJP | physical print cup | 5.000 |
| Warna Sablon | process Add On | 5.000 |
| Warna Sablon | process Add On | 5.000 |
| LID | separate physical | 5.000 |
| **Shipment UI total** | | **20.000** |

Business: two Add Ons are cup printing processes — cup physical once; LID separate.

## Is 20.000 double-counting physical delivery?

| Layer | Behavior | Verdict |
|-------|----------|---------|
| Shipment / PO UI total | `shipment_qty_total` / line sum = Σ **all** line qtys (includes SERVICE) | **Display aggregate** → 20.000 |
| Packing / SJ physical target | `packing_required_total` excludes process-only SERVICE | **10.000** (cup+lid) |
| Belum delivery credit (#696) | Native physical lines only; unlinked SERVICE **skipped** (no parent invent) | Cup **5.000** + lid **5.000** per SJ — **not** 15.000 cup |
| Stock ledger / R2 | Not mutated by UI total; attribution is line-based | UI total alone ≠ ledger double post |

**Conclusion:** 20.000 is a **UI/header aggregate**, not proof that physical cup stock is delivered 3×. Physical engines already exclude process-only SERVICE from packing and (post-#696) from Belum delivery credit.

Out of scope: redesigning shipment UI total (Owner forbade unrelated shipment redesign).

## RO21 reconcile (snapshot — not proof of current live PATCH state)

| Fact | RO21 |
|------|------|
| parent_linked on both SERVICE | **0** (empty `parent_line_id`) — **do not infer links persisted** |
| SERVICE mid | `9bfd5938-…` both lines |
| Print parent | 100003 idx 0; LID 100123 non-print idx 3 |
| Ordered (snapshot) | 10.000 per line (two SJ × 5.000 line qty each) |
| Shipment lines each SJ | cup 5k + svc 5k + svc 5k + lid 5k; `proven_qty` 20.000 |

Owner UI qty 5.000 vs RO21 ordered 10.000: **both recorded**; live re-read still required before write. Pattern (cup + 2 SERVICE + lid, UI sum includes jasa) is the same.

## B31 mapping for this PO

```
PATCH items: [{line_idx:1, parent_line_idx:0}, {line_idx:2, parent_line_idx:0}]
```

Still requires live before-check (empty parent). Regression test:
`test_po_20260915_000003_around_shipment_ui_20000_not_physical_double_count`
