# FINAL HISTORICAL ADD ON REPAIR PREPARATION

**Waktu:** 2026-10-31 ~06:30 Asia/Jakarta  
**Production SHA:** `baa24a13393eedf06aea0ddf2f0793d3956955f9`  
**SOP:** SOP-20261030-003 · YGY-MAIN  
**STOP:** no PATCH / no Mongo write / no deploy executed  

Sources reused: PR #697 manifest, RO21 (`ETERNAL_RO21_COMPLETE_FOR_CURSOR.json`), Stage B `APPROVED_MAPPINGS.json` (method + different 7 POs), M1 parent sim rules, `PATCH /orders/{id}/line-items/addon-parent`.

---

## Counts

| Bucket | Count |
|--------|------:|
| **APPROVED** (Owner signed this exact mapping) | **0** |
| **NEEDS APPROVAL** (class-B structural validation PASS) | **31** |
| **AMBIGUOUS HOLD** (class-C) | **7** |
| Validation FAIL | **0** |

**Unique candidate ≠ approved.** All 31 B rows remain `NEEDS APPROVAL`.

Stage B `owner_approval_status=APPROVED_LINK_PARENT` covers **7 other POs / 10 links** — **0 order_id overlap** with this B31 set. That approval does **not** transfer.

---

## STEP 1 — Class-B approval table (compact)

Full TSV: `docs/repair/APPROVAL_TABLE_B31.tsv`  
Evidence JSON: artifacts `B31_VALIDATION.json`

Per-row structural checks (all 31 PASS): SERVICE mid=`9bfd5938-…`, `parent_linked=0`, exactly one `is_print` parent on PO, proposed parent = that print line, same `order_id`, qty SERVICE==parent ordered.

| PO | SERVICE ID | proposed parent ID | evidence | prior Owner approval | risk |
|----|------------|--------------------|----------|----------------------|------|
| PO/19/10/2026/000008 | `796c1445…#idx=1` | `…#idx=0\|sku=100397` | unique print; nonprint 100243; svc 1000/1000 | **NO** | SYNTHETIC idx line_id |
| PO/2026/09/10/000008 | `556031b2…#idx=1` | `…#idx=0\|sku=100397` | unique print; nonprint 100244 | **NO** | SYNTHETIC idx |
| PO/2026/09/24/000014 | `…#idx=1` | `…#idx=0\|sku=100341` | unique print; nonprint 100247 | **NO** | SYNTHETIC idx |
| PO/2026/09/15/000003 | `e2978ceb…#idx=1` | `…#idx=0\|sku=100003` | unique print; nonprint 100123; **100003** | **NO** | SYNTHETIC; MULTI_SERVICE n=2 |
| PO/2026/09/15/000003 | `e2978ceb…#idx=2` | `…#idx=0\|sku=100003` | same PO second warna → same print | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/24/10/2026/000012 | `c8538899…#idx=1` | `…#idx=0\|sku=100003` | unique print; nonprint 100460; **100003** | **NO** | SYNTHETIC |
| PO/23/10/2026/000007 | `…#idx=1` | `…#idx=0\|sku=100233` | unique print; nonprint 100244 | **NO** | SYNTHETIC |
| PO/2026/09/29/000002 | `…#idx=1` | `…#idx=0\|sku=100233` | unique print; nonprint 100244 | **NO** | SYNTHETIC |
| PO/12/10/2026/000006 | `d1ee245a…#idx=1` | `…#idx=0\|sku=100003` | unique print; nonprint 100460; **100003** | **NO** | SYNTHETIC |
| PO/16/10/2026/000007 | `…#idx=1` | `…#idx=0\|sku=100168` | unique print; nonprint 100166 | **NO** | SYNTHETIC |
| PO/13/10/2026/000005 | `af07a4a6…#idx=1` | `…#idx=0\|sku=100003` | unique print; nonprint 100123; **100003** | **NO** | SYNTHETIC |
| PO/08/10/2026/000004 | `ffa41f61…#idx=2` | `…#idx=0\|sku=100003` | unique print; nonprint 100123; **100003** | **NO** | SYNTHETIC |
| PO/2026/09/21/000002 | `…#idx=1` | `…#idx=0\|sku=100226` | unique print; nonprint 100244\|100167 | **NO** | SYNTHETIC |
| PO/20/10/2026/000028 | `0b1b6099…#idx=1` | `…#idx=0\|sku=100003` | unique print; nonprint 100460; **100003** | **NO** | SYNTHETIC |
| PO/2026/09/10/000010 | `…#idx=1` | `…#idx=0\|sku=100341` | unique print; nonprint 100129 | **NO** | SYNTHETIC |
| PO/2026/09/23/000004 | `…#idx=0` | `…#idx=0\|sku=100051` | unique print only | **NO** | SYNTHETIC |
| PO/2026/09/07/000045 | `…#idx=1` | `…#idx=0\|sku=100341` | unique print; nonprint 100244 | **NO** | SYNTHETIC |
| PO/05/10/2026/000014 | `…#idx=1` | `…#idx=0\|sku=100031` | unique print only | **NO** | SYNTHETIC |
| PO/23/10/2026/000013 | `…#idx=1` | `…#idx=0\|sku=100333` | unique print only (TRACE known) | **NO** | SYNTHETIC |
| PO/13/10/2026/000014 | `2462c80a…#idx=1` | `…#idx=0\|sku=100003` | **100003**; nonprint 100123 | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/13/10/2026/000014 | `2462c80a…#idx=2` | `…#idx=0\|sku=100003` | second warna | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/05/10/2026/000034 | `aa22f8c2…#idx=1` | `…#idx=0\|sku=100003` | **100003**; nonprint 100123 | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/05/10/2026/000034 | `aa22f8c2…#idx=2` | `…#idx=0\|sku=100003` | second warna | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/2026/09/25/000011 | `458699be…#idx=1` | `…#idx=0\|sku=100003` | **100003** | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/2026/09/25/000011 | `458699be…#idx=2` | `…#idx=0\|sku=100003` | second warna | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/2026/09/21/000031 | `78e741d0…#idx=1` | `…#idx=0\|sku=100003` | **100003** | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/2026/09/21/000031 | `78e741d0…#idx=2` | `…#idx=0\|sku=100003` | second warna | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/2026/09/09/000017 | `ba5f34d7…#idx=1` | `…#idx=0\|sku=100003` | **100003** | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/2026/09/09/000017 | `ba5f34d7…#idx=2` | `…#idx=0\|sku=100003` | second warna | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/2026/09/09/000018 | `c02464c9…#idx=1` | `…#idx=0\|sku=100003` | **100003** | **NO** | SYNTHETIC; MULTI_SERVICE |
| PO/2026/09/09/000018 | `c02464c9…#idx=2` | `…#idx=0\|sku=100003` | second warna | **NO** | SYNTHETIC; MULTI_SERVICE |

Exact UUIDs / full evidence: see TSV + `REPAIR_PLAN_DRYRUN.json` (24 allowlisted `order_id`s).

### Class-C HOLD (7) — do not PATCH

| PO | class | print parents |
|----|-------|---------------|
| PO/2026/09/24/000006 | C_NO_PRINT_PARENT | (none) |
| PO/03/10/2026/000017 | C_NO_PRINT_PARENT | (none) |
| PO/2026/09/07/000028 | C_MULTI_PRINT_AMBIGUOUS | 100031\|100051 |
| PO/26/10/2026/000014 | C_MULTI_PRINT_AMBIGUOUS | 100147\|100388 (2 SERVICE lines) |
| PO/2026/09/14/000020 | C_MULTI_PRINT_AMBIGUOUS | 100147\|100388 (2 SERVICE lines) |

---

## STEP 2 — Controlled repair plan (STOP BEFORE WRITES)

Artifact: `docs/repair/REPAIR_PLAN_DRYRUN.json` (`write_authorized: false`)

### Allowlist
- **24** `order_id`s / **31** SERVICE links only (B set)
- C order_ids explicitly excluded
- Production SHA must still be `baa24a13…`
- Forbidden: qty, delivery, stock ledger, R2, SOP/Freeze, users/roles, #692

### Method (existing API — no new script required for write)
```
PATCH /api/orders/{order_id}/line-items/addon-parent
perm: production.route.edit
body: { "items": [ { "line_idx": <SERVICE>, "parent_line_idx": <print parent> } ] }
```
- Mutates **only** `parent_line_id` / `parent_line_idx` (API syncs dual-store)
- Prefer **`parent_line_idx`** until live durable `line_id` confirmed (all RO21 ids are synthetic `idx:N`)

### Before-value checks (per SERVICE line — fail closed)
1. Document `order_id` ∈ allowlist  
2. `line_items[service_idx].master_item_id == 9bfd5938-…`  
3. `parent_line_id` empty **and** `parent_line_idx` null/absent  
4. Print parent at proposed idx still `is_print` / expected SKU mid  
5. If any before-check fails → **STOP that order** (no partial invent)

### Dry-run
1. Load allowlist JSON; confirm `write_authorized=false`  
2. For each order: read-only fetch order → evaluate before-checks → print intended PATCH payload  
3. Zero Mongo writes; exit non-zero on any fail  

### Idempotency
- Re-run after success: before-check expects empty parent → after first success, either skip (already linked to approved parent) or STOP if linked to **different** parent  
- Do not clear unrelated lines  

### Rollback
```
PATCH .../addon-parent
{ "items": [ { "line_idx": <SERVICE>, "clear": true } ] }
```
Restores previous empty link state for allowlisted SERVICE lines only. Audit action already recorded as `production.addon_parent.update`.

### Independent post-repair validation (100003)
1. RO: 12×100003 POs — each SERVICE `parent_linked` to 100003 print line  
2. `completion_unproven[100003] == false` on YGY  
3. Opname/SSI: `qty_belum_terkirim` **numeric** (not null); never coerce old null→0  
4. Confirm no new stock/delivery ledger movements from this repair  

---

## Smallest safe RO check — 100004 + remaining UNVERIFIED

**Goal:** diagnose only; no write.

Minimal Owner paste (single SSH RO script or mongosh projection):

1. Resolve master for item_code `100004` → `master_id`, `item_type`  
2. YGY open/non-cancelled orders where any line owns that mid  
3. Per order emit: `po_number`, `order_id`, each print line `(idx, line_id, mid, item_code, is_print, qty, qty_completed)`, each SERVICE/process line `(idx, mid, parent_line_id, parent_line_idx, ordered, completed)`, flags `n_unlinked_service`, `n_print_parents`, `completion_unproven` contribution  
4. Same probe for any other SOP-003 codes still showing Belum terverifikasi (from export null Belum column) — **codes list from Owner export only**, not invent  

Reuse shape of RO21 extract columns; do **not** re-run full catalog audits.

Live Mongo/SSH: **unavailable to this agent** → Owner must run this RO.

---

## EXACT OWNER APPROVAL REQUIRED

Phrase (gates write):

> **APPROVE ADDON PARENT REPAIR MANIFEST B31 EXACT — SOP-20261030-003**

Must name: exact SHA `baa24a13…`, exact allowlist file hash / PR #697 HEAD, and whether to approve **all 31** or a subset (e.g. `affects_100003=1` only = 19 links / 12 POs).

---

## SAFE EXECUTION METHOD

1. Owner runs dry-run against production read (before-checks)  
2. Owner sends approval phrase + scope (all 31 vs 100003-only)  
3. Authenticated user with `production.route.edit` applies allowlisted `PATCH addon-parent` **one order at a time** (or batched items per order only)  
4. After each order: verify parent fields; on fail → rollback `clear` for that order  
5. End: 100003 post-validation checklist  
6. Class-C remains HOLD  

**REPAIR READY (plan package): YES**  
**REPAIR EXECUTE AUTHORIZED: NO** (APPROVED=0; waiting Owner phrase)
