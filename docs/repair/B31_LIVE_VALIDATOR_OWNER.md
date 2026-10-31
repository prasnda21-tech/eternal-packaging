# B31 Live Validator — Owner VPS (read-only)

**PATCH AUTHORIZED: NO** — this tool only GETs orders.

## Prerequisites (local on VPS — do not paste secrets into chat)

```bash
mkdir -p ~/.config/eternal
umask 077
cat > ~/.config/eternal/b31-live-ro.env <<'EOF'
ETERNAL_B31_API_BASE=http://127.0.0.1:REPLACE_WITH_BACKEND_PORT
ETERNAL_B31_EMAIL=REPLACE_WITH_ERP_EMAIL
ETERNAL_B31_PASSWORD=REPLACE_WITH_ERP_PASSWORD
EOF
chmod 600 ~/.config/eternal/b31-live-ro.env
```

Notes:
- User needs `po.view` (or admin). Login uses `POST /api/auth/login`.
- **Do not** put `MONGO_URL` / JWT / app secrets in this file (validator refuses).
- Optional: omit `ETERNAL_B31_API_BASE` if `/etc/eternal-packaging/backend.env` exposes only a safe `PORT`/`BACKEND_PORT` (Mongo/JWT keys are ignored).

Checkout / copy these files onto the VPS (same commit as PR #697):
- `scripts/repair/b31_live_validator.py`
- `docs/repair/REPAIR_PLAN_DRYRUN.json` (SHA256 `e9d5de1c…`)
- `docs/repair/APPROVAL_TABLE_B31.tsv`

## Exact VPS command

From the directory that contains `scripts/` and `docs/`:

```bash
python3 scripts/repair/b31_live_validator.py \
  --allowlist docs/repair/REPAIR_PLAN_DRYRUN.json \
  --table docs/repair/APPROVAL_TABLE_B31.tsv
```

## Expected safe output (PASS)

```
B31_LIVE_VALIDATOR
production_sha_required=baa24a13393eedf06aea0ddf2f0793d3956955f9
patch_authorized=NO
allowlist_sha256=e9d5de1c46ecd4121fa97d72e80539d8fb84eb0feaef758fedd809dbabd28417 MATCH=YES
orders_expected=24 links_expected=31
auth=OK mode=api_readonly
orders_fetched=24 orders_missing=0
links_ok=31 links_mismatch=0 multi_service_orders=7
RESULT=PASS
PATCH AUTHORIZED: NO
```

Exit code `0` = PASS. Exit `2` = FAIL closed (mismatches printed as reason codes only).
