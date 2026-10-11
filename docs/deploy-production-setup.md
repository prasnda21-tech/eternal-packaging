# Production deploy setup (GitHub Actions → VPS)

Scope: workflows, SSH gate, isolated release runner, Cursor trigger helper, least-privilege bootstrap docs, synthetic tests. Does **not** mutate ERP business source, MongoDB, or run production deploy by itself.

## VPS path contract (aligned to production evidence)

| Path | Role |
|------|------|
| `/opt/eternal-packaging-runtime/` | Dedicated runtime root (leaf created by root bootstrap; owned by `eternaladmin`). **Never chown `/opt`.** |
| `/opt/eternal-packaging-runtime/releases/` | Isolated releases + `baseline-active` + `mirror.git` + backups |
| `/opt/eternal-packaging-runtime/current` | Symlink to active release/baseline tree (replaceable by deploy user) |
| `/opt/eternal-packaging-runtime/frontend-prev` | Frontend rollback backup (not a `/var/www` sibling) |
| `/var/www/eternal-packaging` | Live webroot (leaf owned by `eternaladmin`; **never chown `/var/www`**) |
| `/opt/eternal-packaging` | Legacy dirty overlay — left untouched; secrets via EnvironmentFile only |
| `/opt/eternal-packaging/backend/.env` | Existing EnvironmentFile (kept) |
| `/etc/eternal-packaging-google-places.env` | Existing EnvironmentFile (kept) |
| Stage WD (pre-cutover) | `/home/eternaladmin/eternal-packaging-phase3-stage/backend` (retarget away from this) |
| Legacy ExecStart venv | `/opt/eternal-packaging/backend/.venv/...` (do not copy; prepare fresh release venv) |

## Separation of concerns

| Concern | Mechanism |
|---------|-----------|
| Trigger GitHub Actions | Credential with **Actions write**. `cursor[bot]` read ≠ write. |
| Human deployment control (enforced) | Repository variable `DEPLOY_ALLOWED_ACTORS` (fail-closed). |
| Human approval (preferred platform) | Environment `production` **Required reviewers** only if/when available. |
| Technical shared secret | `deploy_shared_secret` ↔ `DEPLOY_SHARED_SECRET` (secondary). **Not** human approval. |
| SSH to VPS | Environment secrets `DEPLOY_SSH_*` only inside Actions jobs. |
| Deploy mutual exclusion | `flock` on `/tmp/eternal-packaging-deploy.lock` (runner + terminal interoperable; no unlink). |

## Runtime bootstrap checklist (source/docs only — do not run from this agent)

Order matters — **preparation before activation**:

### Phase A — preparation (no live service retarget / no deploy)

1. As root: `ETERNAL_BOOTSTRAP_DEPLOY_USER=eternaladmin bash scripts/deploy/bootstrap-permissions.sh` (dry-run), then `--apply`.
   - Creates `/opt/eternal-packaging-runtime/{releases,frontend-prev}` owned by `eternaladmin`.
   - Narrow sudoers: `systemctl restart|is-active|show` for the unit only.
   - Does **not** chown/chmod `/opt` or `/var/www`.
2. As `eternaladmin`: create bare mirror `git clone --mirror <repo> /opt/eternal-packaging-runtime/releases/mirror.git`
3. As `eternaladmin`: `prepare-verified-baseline.sh --apply`
   - Freezes backend **without** copying live `.venv`
   - Snapshots live FE (selective-merge inclusive) into baseline
   - Creates **fresh** isolated venv + validates uvicorn/python (no prod DB)
   - Writes full integrity manifest (no 5000-file truncation) + runtime provenance
   - Refuses overwrite of existing baseline unless `--force-recreate` and CURRENT is not pointing at it

### Phase B — activation (after verified baseline exists)

4. Install/adapt unit from `scripts/deploy/systemd/eternal-packaging-backend.service`:
   - `User=eternaladmin` / `Group=eternaladmin`
   - `WorkingDirectory=/opt/eternal-packaging-runtime/current/backend`
   - `ExecStart=/opt/eternal-packaging-runtime/current/backend/.venv/bin/uvicorn ...`
   - Keep `EnvironmentFile=-/opt/eternal-packaging/backend/.env`
   - Keep `EnvironmentFile=-/etc/eternal-packaging-google-places.env`
5. Point `current` at baseline tree **or** leave unset until first deploy.
6. `install-gate.sh --activate` and confirm probe.
7. Set Environment secrets + `DEPLOY_ALLOWED_ACTORS`.

Keep dirty `/opt/eternal-packaging` overlay untouched. Pure SHA does **not** automatically preserve selective-merge FE — first cutover needs `ack_replace_live_frontend`.

## Runner behaviour (high level)

- Shared `flock` lock held through rollback; existing lock file without holder does not block; lock file is not unlinked.
- Preflight: runtime root writable, FE leaf writable, mirror, disk, systemd WD/ExecStart match expected **runtime** paths.
- Materialize via `git archive` then **fresh venv** + dependency install; validate executable before switching `current`.
- Baseline rollback runtime must pass the same validation (not marker-only).
- FE backup under runtime root; publish/restore in-place (assets then atomic `index.html`); never rename webroot under root-owned `/var/www`.
- Health + MainPID/cwd checks after restart.
- No business migrations/backfills.

## Cursor trigger helper

```bash
bash scripts/deploy/trigger_github_actions.sh plan probe
bash scripts/deploy/trigger_github_actions.sh plan deploy --sha <40-hex> --confirm-sha <40-hex>
# execute only with write credential + Owner order:
bash scripts/deploy/trigger_github_actions.sh execute probe --execute
bash scripts/deploy/trigger_github_actions.sh execute deploy \
  --sha <40-hex> --confirm-sha <40-hex> --ack-replace-live-frontend --execute
```

Shared secret travels env → JSON stdin (`gh api --input -`), never argv/logs.

## Probe-only workflow

`.github/workflows/deploy-probe.yml` — literal remote `probe` only. PASS ≠ runtime readiness.

## GitHub Environment Secrets / Variables

Secrets: `DEPLOY_SHARED_SECRET` (alias `DEPLOY_APPROVAL_TOKEN`), `DEPLOY_HOST`, `DEPLOY_USER` (=`eternaladmin` gate user), `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, optional `DEPLOY_SSH_PORT`.

Variable: `DEPLOY_ALLOWED_ACTORS` (comma-separated logins).

## Preflight VPS (read-only; no secrets)

```bash
RUNTIME=/opt/eternal-packaging-runtime
SVC=eternal-packaging-backend.service
stat -c '%U:%G %a %n' /opt /var/www "$RUNTIME" "$RUNTIME/releases" /var/www/eternal-packaging 2>/dev/null || true
systemctl show -p WorkingDirectory -p ExecStart -p MainPID -p ActiveState --value "$SVC"
test -d "$RUNTIME/releases" && test -w "$RUNTIME/releases" && echo RELEASES_WRITABLE
test -d /var/www/eternal-packaging && test -w /var/www/eternal-packaging && echo FE_LEAF_WRITABLE
# parents must remain root-owned (evidence); do not "fix" by chowning them
BASE="$RUNTIME/releases/baseline-active"
test -f "$BASE/.baseline-verified" && grep -E '^(kind|immutable|content_digests|frontend_src|file_count|runtime_)' "$BASE/.baseline-verified"
(cd "$BASE" && sha256sum -c .baseline-integrity >/dev/null && echo BASELINE_DIGEST_OK)
test -x "$BASE/tree/backend/.venv/bin/uvicorn" && echo BASELINE_RUNTIME_OK
```

## Synthetic verification

```bash
bash scripts/tests/test_deploy_infrastructure_synthetic.sh
```

## Explicit non-goals

- No production deploy from this document alone
- No Mongo migration execution
- No E2E claim without Owner-run cutover
- No `sudo NOPASSWD:ALL` / arbitrary root script execution
- No chown of `/opt` or `/var/www` parents
