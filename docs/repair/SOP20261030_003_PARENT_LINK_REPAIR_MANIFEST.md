# Parent-link repair manifest — Belum terverifikasi residual after #696

**Generated:** 2026-10-31 ~06:10 Asia/Jakarta  
**Production code SHA (deployed):** `baa24a13393eedf06aea0ddf2f0793d3956955f9` (#696)  
**SOP context:** SOP-20261030-003 · YGY-MAIN · cutoff 2026-10-31 05:56 WIB  
**Mode:** Manifest only — **no production writes** from this document  
**Source snapshot:** `ETERNAL_RO21_COMPLETE_FOR_CURSOR.json` (RO21; not a live re-scan of SOP-003)

Companion TSV: `SOP20261030_003_PARENT_LINK_REPAIR_MANIFEST.tsv`

## What #696 did / did not fix

| Class | Meaning | #696 |
|-------|---------|------|
| **A** | Valid physical SJ credit incorrectly rejected (unlinked SERVICE sibling blast) | **Fixed** — skip SERVICE line only |
| **B** | Missing historical SERVICE `parent_line_id` / `parent_line_idx` | **Not fixed by code** — fail-closed `completion_unproven` until explicit link |
| **C** | Ambiguous multi-print-parent relationships | **Not fixed** — Owner must choose parent; no guess |

**Qty Siap Belum Terkirim = null ("Belum terverifikasi")** after #696 when:

`completion_unproven && completed > 0`  
(and/or delivery `unproven` when R2 authority is off).

With R2 on (typical Opname path), delivery provenance is `r2_ledger` — **completion_unproven alone** keeps Belum null. Sticky OR across all YGY orders for that master: **one** tainted PO keeps the SKU UNVERIFIED.

## 100003 (proven)

- Master `511e1d5d-90b1-4af8-b482-5b90abaada76`
- RO21: `completion_unproven=True`, `ui_unverified=True`
- **12 POs / 19 SERVICE lines** with SERVICE mid `9bfd5938-6868-4d1b-bf9b-6212225134a2`, parent empty
- Every such PO: **exactly one print parent = 100003**; sibling lid `100123` or `100460` is **non-print** (not a process-parent candidate)
- Classification: **B** with unique print-parent evidence (RO21 `n_physical_print_parents` over-counted lids — uniqueness recomputed via `is_print`)
- Proposed repair: set each SERVICE line’s `parent_line_id` / `parent_line_idx` → the 100003 print line on that PO
- `auto_write=0` — Owner ACK required; do not invent from name alone in code

## 100004

- **Not in RO21 21-master extract**
- No per-PO SERVICE/parent rows in workspace for this SKU
- Live production Mongo **not available** to this agent (local `MONGO_URL` has empty collections; no `DEPLOY_SSH_*`)
- **Cannot claim** live root cause for 100004 from fixtures alone → **BLOCKED** pending Owner RO extract for master `0105d7b1-9780-4052-bfa7-a08bc0f3f05b` (or current mid) on YGY

## Manifest summary (38 unlinked SERVICE rows in RO21)

| Class | Count | Action |
|-------|------:|--------|
| B_UNIQUE_PRINT_PARENT | 31 | Owner ACK → `PATCH` addon-parent (or equivalent) per row |
| C_MULTI_PRINT_AMBIGUOUS / C_NO_PRINT_PARENT | 7 | Owner chooses parent; no auto mapping |
| Rows affecting 100003 | 19 | Priority for SOP Belum on 100003 |

## Approved execution path (existing product — no new script)

1. Owner confirms each B-row proposed parent (print line).
2. Resolve live `line_id` if RO21 shows synthetic `idx:N`.
3. Use existing addon-parent API / Stage B explicit `parent_line_id` path.
4. Re-snapshot Opname / SSI — Belum must become **numeric** (never coerce prior null to 0).
5. Rollback = restore previous `parent_line_id` / `parent_line_idx` from order backup.

## Out of scope

- No Mongo data repair executed here  
- No SOP-20261030-002 / Freeze mutation  
- PR #692 HOLD  
- No stock quantity adjustments  
- No redeploy of #696  
