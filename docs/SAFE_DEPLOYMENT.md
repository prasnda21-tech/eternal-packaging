# Safe 24/7 Deployment — Eternal Packaging

## Status

| Item | Value |
|------|-------|
| Tooling entrypoint | `scripts/safe-deploy/deploy.sh` |
| Remote wrapper | `scripts/safe-deploy/remote.sh` |
| Heavy snapshot in deploy path | **REMOVED / QUARANTINED** |
| Database auto-rollback | **NO** (forbidden) |
| Due-date application deploy | **NOT part of this change** |

## Why this exists

Production outage audit (2026-09-29):

- Primary incident classification: abrupt VPS/hypervisor/provider reboot (MEDIUM confidence).
- Due-date application code was **not** deployed and was **not** the cause.
- Independently, the old pre-deploy snapshot used `find().to_list(None)` + full JSON hash and **confirmed OOM** (~4.7–6.0 GiB) on 2026-09-28.
- Production has ~7.8 GiB RAM and **no swap**.

Do not conflate “snapshot OOM” with “reboot initiator”. Both matter; only the former is proven for the snapshot scripts.

## Invariants

1. **OLD LIVE keeps serving** during fetch/build/prepare.
2. Deploy targets an **exact `TARGET_SHA`** (never blind `git pull`).
3. Frontend builds **outside** `/var/www/eternal-packaging`.
4. Cutover is short: frontend publish → backend checkout+restart → health.
5. Health must be `mongo=true` and `mode=operational`.
6. Failure before cutover → **ABORT**, live untouched.
7. Failure after cutover → **CODE + frontend rollback only** (never Mongo restore).
8. No full regression suite on production.
9. No full-collection hashing / `to_list(None)` in preflight.
10. Production frontend builds must print `FRONTEND API TARGET` and **fail** if that target is `localhost`, `127.0.0.1`, or `0.0.0.0`. A Cloud Agent `REACT_APP_BACKEND_URL=http://localhost:8000` must not be inherited. The proven production origin is `https://produksi.eternalpackaging.id`. Local/dev mode (`SAFE_DEPLOY_MODE=local`) may use loopback. Built bundles that contain `http://localhost:8000` or `http://127.0.0.1:8000` are rejected before publish.

## Production layout (current)

```
App checkout:     /opt/eternal-packaging
Backend service:  eternal-packaging-backend (WorkingDirectory=.../backend)
Nginx root:       /var/www/eternal-packaging   (directory today; not a symlink)
Releases:         /var/www/eternal-packaging-releases/<SHA>
Previous FE:      /var/www/eternal-packaging-previous
Lock:             /var/lock/eternal-packaging-deploy.lock
History:          /var/log/eternal-packaging-deploy/
```

Frontend publish:

- If webroot is a **symlink** → atomic `ln -sfn` + `mv -T`.
- If webroot is a **directory** (current) → near-atomic rename swap; no nginx config change required.

## Commands

### Read-only preflight on VPS

```bash
cd /opt/eternal-packaging
bash scripts/safe-deploy/preflight.sh
```

### Deploy exact SHA on VPS

```bash
cd /opt/eternal-packaging
TARGET_SHA=<40-char-sha> bash scripts/safe-deploy/deploy.sh
```

### From Cursor/CI (SSH wrapper)

```bash
# read-only
./scripts/safe-deploy/remote.sh preflight

# real deploy (requires --execute)
TARGET_SHA=<sha> ./scripts/safe-deploy/remote.sh deploy --execute
```

### Emergency code rollback

```bash
ROLLBACK_SHA=<previous> bash scripts/safe-deploy/rollback.sh
```

### Offline dry-run tests

```bash
./scripts/safe-deploy/tests/dry-run-tests.sh
```

## Lightweight data preflight

`lib/preflight_data.py` collects only:

- collection **counts**
- latest movement / HPP / invoice **identifiers**
- targeted SKU **100442** valuation/stock fields
- invoice due_date schema counts

It intentionally does **not** require global count equality after deploy: users may create transactions during a 24/7 cutover.

## External frontend build (preferred)

If CI/Cloud Agent can build the frontend:

```bash
FRONTEND_ARTIFACT_DIR=/path/to/build TARGET_SHA=<sha> bash scripts/safe-deploy/deploy.sh
```

This avoids competing with Mongo/backend for RAM on the VPS.

## Quarantined legacy snapshots

See `scripts/safe-deploy/legacy/README.md`.

- `due_edit_pre_snapshot.DO_NOT_USE.py`
- `due_edit_light_snap.DO_NOT_USE.py`

**DO NOT USE IN PRODUCTION DEPLOY.**

## Recommended swap (REPORT ONLY — not applied)

- Host RAM ~7.8 GiB, Swap 0B.
- Suggest 2–4 GiB swapfile, `vm.swappiness=10`, only after explicit approval.
- Swap is a cushion; it does **not** make full-collection hashing acceptable.

## Due-date candidate note

Reviewed candidate `de7dbc3…` is **not** deployable directly onto current LIVE `2dbb533…`.
It requires semantic reconciliation onto latest LIVE after this tooling is proven.
Do not deploy due-date in the same task as tooling.

## Partial cutover recovery

| Failure mode | Action |
|--------------|--------|
| Fail before cutover | No action; OLD LIVE already serving |
| Frontend published, backend health fail | Script rolls back code SHA + previous frontend |
| SSH drop before cutover | OLD LIVE untouched |
| SSH drop after checkout/restart | SSH back; run `rollback.sh` with previous SHA if health bad; check `deploy-*.json` history |
