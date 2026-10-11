# Production deploy setup (GitHub Actions → VPS)

Scope: GitHub Actions workflow, SSH forced-command gate, isolated release runner, least-privilege bootstrap, and synthetic tests. Does **not** mutate ERP business source, MongoDB, or run production deploy by itself.

## Preconditions (Owner-owned)

- GitHub Environment `production` exists and is limited to branch `main`.
- Dedicated GitHub Actions SSH deploy key exists on the VPS (not the Windows Owner key).
- `authorized_keys` uses fail-closed forced command.
- VPS tree `/opt/eternal-packaging` may be dirty — deploys use isolated releases and never `git reset` / `git pull` that tree.
- Backend unit: `eternal-packaging-backend.service`
- Frontend docroot: `/var/www/eternal-packaging`
- Deploy lock: `/tmp/eternal-packaging-deploy.lock`

## Gate probe compatibility + verified install

Workflow accepts probe stdout matching any of:

- `ETERNAL_GATE_OK probe=pass` (canonical repo gate, required after install-gate activation)
- `PASS probe` (legacy-compatible)
- exact `OK`
- `probe: PASS` / `probe: ok`

**Do not enable / dispatch the workflow against production until one of these is true:**

A. Existing VPS gate already returns an accepted probe form **and** Owner has confirmed it, **or**
B. Owner runs the verified install below and sees `GATE_ACTIVATE_OK`.

```bash
# From an approved checkout of scripts/deploy (on VPS, deploy user):
./install-gate.sh --source /path/to/scripts/deploy
# Review GATE_VERIFY_OK, then:
./install-gate.sh --source /path/to/scripts/deploy --activate
```

`install-gate.sh` stages files, runs `probe`, requires the canonical `ETERNAL_GATE_OK` line, and only then atomically replaces `~/.ssh/eternal-actions-gate`.

Ensure `authorized_keys` `command=` points at that path.

## Least-privilege bootstrap (`/opt`, web root, systemd restart)

Dry-run (safe):

```bash
sudo ./bootstrap-permissions.sh
```

Apply (root only):

```bash
sudo ETERNAL_BOOTSTRAP_DEPLOY_USER=DEPLOY_USER ./bootstrap-permissions.sh --apply
```

This creates/adjusts:

- `/opt/eternal-packaging-releases` owned by deploy user
- parent of `/opt/eternal-packaging-current` so the deploy user can atomically replace the symlink
- `/var/www/eternal-packaging` owned by deploy user, group `www-data`
- `/etc/sudoers.d/eternal-packaging-deploy` allowing **only**:
  - `systemctl restart eternal-packaging-backend.service`
  - `systemctl is-active eternal-packaging-backend.service`
  - `systemctl show eternal-packaging-backend.service`

**No `NOPASSWD:ALL`.** Dirty `/opt/eternal-packaging` contents are not rewritten.

## Systemd unit (release symlink + intact env)

Install/adapt the unit from `scripts/deploy/systemd/eternal-packaging-backend.service`:

- `WorkingDirectory=/opt/eternal-packaging-current/backend`
- `ExecStart=.../opt/eternal-packaging-current/backend/...`
- `EnvironmentFile=-/opt/eternal-packaging/backend/.env` (existing secrets stay in the legacy tree)
- Optional: `EnvironmentFile=-/etc/eternal-packaging/backend.env`

First isolated cutover captures a baseline of the previously active backend root + frontend snapshot so rollback can restore them if the first deploy fails.

## Deploy order / health / rollback

1. Materialize isolated release under `/opt/eternal-packaging-releases/<sha>`
2. Build frontend into the release (unpublished)
3. Point `/opt/eternal-packaging-current` at the release
4. Restart backend only if backend paths changed
5. Health must return JSON with `mongo=true` and `mode=operational`
6. Publish frontend to `/var/www/eternal-packaging` only after health PASS
7. On failure: restore frontend (if publish started) and restore previous/current baseline backend symlink + restart

## GitHub Environment Secrets

Set on Environment **`production`**:

| Secret | Purpose |
|--------|---------|
| `DEPLOY_APPROVAL_TOKEN` | **Required** separate authorization secret; workflow input `approval_token` must match |
| `DEPLOY_HOST` | VPS hostname or IP |
| `DEPLOY_USER` | SSH user with forced-command gate |
| `DEPLOY_SSH_KEY` | Actions deploy private key only (never Windows Owner key) |
| `DEPLOY_KNOWN_HOSTS` | `known_hosts` line(s) for `StrictHostKeyChecking=yes` |
| `DEPLOY_SSH_PORT` | Optional (default 22) |

## Authorization (confirm_sha + wait timer are not enough)

Production deploy requires **all** of:

1. Environment `production` protection (Required Reviewers preferred; wait timer as extra brake)
2. Separate secret `DEPLOY_APPROVAL_TOKEN` matched by workflow input `approval_token` (job `authorize`)
3. `confirm_sha == commit_sha`
4. Explicit CI verification via `scripts/deploy/verify_ci_for_sha.sh`:
   - successful `reconciliation-validation.yml` run for the SHA
   - required check-runs success:
     - `Backend focused tests`
     - `Backend stock compatibility (replica set)`
     - `Frontend tests and build`

Deploy SSH secrets are only used in the `deploy` job after `authorize` succeeds.

If Required Reviewers cannot be enabled: keep `DEPLOY_APPROVAL_TOKEN` (rotate regularly), Environment branch filter `main`, wait timer, and Owner-only workflow dispatch permissions.

## Operator runbook (after this PR is merged — not automatic)

1. Bootstrap permissions + install systemd unit pointing at current symlink + EnvironmentFile legacy `.env`
2. `install-gate.sh --activate` and confirm probe
3. Set Environment secrets including `DEPLOY_APPROVAL_TOKEN`
4. Dispatch **Deploy production** on `main` with `commit_sha`, `confirm_sha`, `approval_token`
5. Approve Environment deployment; authorize job validates token; deploy job SSHs `probe` then `deploy <sha>`

## Synthetic verification (no production / no VPS mutation)

```bash
bash scripts/tests/test_deploy_infrastructure_synthetic.sh
```

## Explicit non-goals

- No production deploy from this documentation alone
- No Mongo migration execution
- No use/creation/reading of Owner Windows private keys
- No `sudo NOPASSWD:ALL`
