# Belum terverifikasi residual — post B31 (SOP-20261031-001)

Production SHA required: `baa24a13393eedf06aea0ddf2f0793d3956955f9`
B31 status: **PASS** (24 PO / 31 links). Do not re-apply B31.
Agent production writes: **NO**.

## Phase 1 — Inventory (Owner VPS, read-only)

```bash
cd /opt/eternal-packaging-runtime/releases/current   # or repo checkout
python3 scripts/repair/sop_belum_unverified_enumerator.py \
  --session SOP-20261031-001 \
  --include-known-residual-orders \
  --out ~/sop-belum-enum-$(date -u +%Y%m%dT%H%M%SZ).json
```

Uses `~/.config/eternal/b31-live-ro.env`. Prints `examined` / `unverified` and per-SKU reason codes.

## Phase 2 — Classification (evidence-backed)

| SKU | Class | Reason | Residual docs | Deterministic? |
|-----|-------|--------|---------------|----------------|
| 100003 | — | Cleared by B31 | (none outside B31) | Done |
| 100031 | A→B | Unlinked SERVICE on multi-print PO; sticky `completion_unproven` | `PO/2026/09/07/000028` (`2de4d6b5…`) | **B32** qty-unique → parent **100051** (svc 500 = print 500) |
| 100051 | A→B | Sibling blast same PO | same | B32 |
| 100147 | A→B | Two multi-print POs | `PO/26/10/2026/000014`, `PO/2026/09/14/000020` | B32 (2000→100147, 1000→100388) |
| 100388 | A→B | Sibling of 100147 | same | B32 |
| 100004 | G | Not in RO21 `completion_unproven`; historical belum32 = delivery delta +1000, `unproven_event_count=0` | **Needs live causal** via enumerator | Stop — Owner RO |
| (others) | G | Only if enumerator finds more | TBD | Stop if ambiguous |

Still **HOLD** (no print parent — do not invent):

| PO | order_id | class |
|----|----------|-------|
| PO/2026/09/24/000006 | `bdd08fd1…` | C_NO_PRINT_PARENT |
| PO/03/10/2026/000017 | `b2988cad…` | C_NO_PRINT_PARENT |

These do **not** taint 100031/100004 print Belum by themselves (no print parent on those POs). Owner decision: link to a real print line if one should exist, or leave as non-physical process-only documentation.

## Phase 3–4 — B32 minimal fix (dry-run default)

Artifacts:

- `docs/repair/REPAIR_PLAN_B32_QTY_UNIQUE.json` (sha256 pinned in validator)
- `docs/repair/APPROVAL_TABLE_B32.tsv`
- `scripts/repair/b32_live_validator.py`
- `scripts/repair/b32_patch_executor.py`

```bash
# 1) Live before-checks (no write)
python3 scripts/repair/b32_live_validator.py \
  --allowlist docs/repair/REPAIR_PLAN_B32_QTY_UNIQUE.json \
  --table docs/repair/APPROVAL_TABLE_B32.tsv

# 2) Dry-run (default)
python3 scripts/repair/b32_patch_executor.py \
  --allowlist docs/repair/REPAIR_PLAN_B32_QTY_UNIQUE.json \
  --table docs/repair/APPROVAL_TABLE_B32.tsv

# 3) Execute ONLY after Owner phrase (VPS, not Cursor Cloud)
python3 scripts/repair/b32_patch_executor.py \
  --allowlist docs/repair/REPAIR_PLAN_B32_QTY_UNIQUE.json \
  --table docs/repair/APPROVAL_TABLE_B32.tsv \
  --execute \
  --confirm-phrase 'APPROVE ADDON PARENT REPAIR MANIFEST B32 QTY-UNIQUE — SOP-20261031-001'
```

Mutates **only** `parent_line_id` / `parent_line_idx`. No qty, delivery, ledger, or SOP writes.

## Code fix in PR (local)

Operational Opname GET now **recomputes** Qty Pesanan / Belum live (presentation only) so parent repairs appear on active `SOP-20261031-001` without cancelling the session. Physical counts are not rewritten.

## Owner decisions still required

1. **Approve B32 phrase** (5 links) — or reject and choose alternate parents.
2. **100004**: run enumerator; if class G with live unlinked/orphan PO list, decide parent/repair; if only delivery delta under R2, Belum should already be numeric — escalate as calculation defect with evidence.
3. **C_NO_PRINT** (2 POs): business choice — inventing a print parent is forbidden.
