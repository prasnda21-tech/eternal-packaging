# B31 PATCH Executor — Owner VPS only

**Default = DRY-RUN. No Cursor Cloud execution.**

Uses the same approved allowlist as the live validator  
SHA256 `e9d5de1c46ecd4121fa97d72e80539d8fb84eb0feaef758fedd809dbabd28417`  
Production SHA `baa24a13393eedf06aea0ddf2f0793d3956955f9`  
Scope: **24 orders / 31 links** (Class-C excluded).

API: `PATCH /api/orders/{order_id}/line-items/addon-parent`  
Mutates only `parent_line_id` / `parent_line_idx`.

## Auth (local VPS file — do not paste secrets)

Same as validator: `~/.config/eternal/b31-live-ro.env`  
For `--execute`, ERP user needs **`production.route.edit`** (or admin).

## Exact commands

From repo tree containing `scripts/` + `docs/repair/`:

### 1) Dry-run (mandatory first)

```bash
python3 scripts/repair/b31_patch_executor.py \
  --allowlist docs/repair/REPAIR_PLAN_DRYRUN.json \
  --table docs/repair/APPROVAL_TABLE_B31.tsv
```

Expected: `mode=DRY-RUN`, `MATCH=YES`, `dryrun_ok` × 24, `RESULT=PASS`.

### 2) Execute (Owner VPS only — after dry-run PASS)

```bash
python3 scripts/repair/b31_patch_executor.py \
  --allowlist docs/repair/REPAIR_PLAN_DRYRUN.json \
  --table docs/repair/APPROVAL_TABLE_B31.tsv \
  --execute \
  --confirm-phrase 'APPROVE ADDON PARENT REPAIR MANIFEST B31 EXACT — SOP-20261030-003'
```

Expected: `patched_ok` × 24, `RESULT=PASS patched_orders=24 patched_links=31`,  
`rollback_book=~/b31-patch-evidence-*/rollback_book.json`.

On any precheck/PATCH/postcheck failure: **STOP** immediately (no further orders).  
Evidence JSON per order includes before/after + rollback `clear` items.

## Safety

- Refuses `--execute` inside Cursor Cloud Agent environments  
- Revalidates every PO live immediately before write  
- Aborts if parent already set, SKU/mid/qty/idx drift  
- Does not modify quantity, delivery, stock, ledger, SOP, users, roles, menus  
