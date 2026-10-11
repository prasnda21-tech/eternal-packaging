# Production deploy setup (GitHub Actions → VPS)

Scope: GitHub Actions workflow, SSH forced-command gate, isolated release runner, and synthetic tests. Does **not** mutate ERP business source, MongoDB, or run production deploy by itself.

## Preconditions (already done / Owner-owned)

- GitHub Environment `production` exists and is limited to branch `main`.
- Dedicated GitHub Actions SSH deploy key exists on the VPS (not the Windows Owner key).
- `authorized_keys` uses fail-closed forced command, for example:

```
restrict,command="/home/DEPLOY_USER/.ssh/eternal-actions-gate" ssh-ed25519 AAAA... eternal-gha-deploy
```

- Gate `probe` already PASSes; other commands are rejected.
- VPS tree `/opt/eternal-packaging` may be dirty — deploys use isolated releases under `/opt/eternal-packaging-releases/<sha>` and never `git reset` / `git pull` that tree.
- Backend unit: `eternal-packaging-backend.service`
- Frontend docroot: `/var/www/eternal-packaging`
- Deploy lock: `/tmp/eternal-packaging-deploy.lock`

## Install / refresh gate + runner on VPS

Copy from an approved main SHA (example paths):

```bash
install -d -m 755 ~/.ssh
install -m 755 scripts/deploy/eternal-actions-gate ~/.ssh/eternal-actions-gate
install -m 755 scripts/deploy/eternal-deploy-runner.sh ~/.ssh/eternal-deploy-runner.sh
install -m 644 scripts/deploy/lib.sh ~/.ssh/lib.sh
```

Ensure `authorized_keys` `command=` points at `~/.ssh/eternal-actions-gate`.

Create writable roots owned by the deploy user (no `sudo NOPASSWD:ALL`):

```bash
sudo install -d -o DEPLOY_USER -g DEPLOY_USER /opt/eternal-packaging-releases
sudo install -d -o DEPLOY_USER -g DEPLOY_USER /var/www/eternal-packaging
# Optional symlink target parent for current release:
sudo touch /opt/eternal-packaging-current 2>/dev/null || true
sudo chown -h DEPLOY_USER:DEPLOY_USER /opt/eternal-packaging-current || \
  sudo install -o DEPLOY_USER -g DEPLOY_USER -d "$(dirname /opt/eternal-packaging-current)"
```

Bootstrap bare mirror once (deploy user):

```bash
git clone --mirror git@github.com:prasnda21-tech/eternal-packaging.git \
  /opt/eternal-packaging-releases/mirror.git
```

Configure systemd so `eternal-packaging-backend.service` `WorkingDirectory` / Exec paths follow `/opt/eternal-packaging-current` (or a unit drop-in), without granting broad sudo.

Optional Owner-only override for explicitly approved migrations:

```bash
# Only when Owner approved migrations for that deploy window.
export ETERNAL_ALLOW_MIGRATIONS=1
```

Prefer setting that in a root-owned env file read only by the deploy user for a single run — never commit secrets.

## GitHub Environment Secrets

Set these on Environment **`production`** (not repository-wide if avoidable):

| Secret | Purpose |
|--------|---------|
| `DEPLOY_HOST` | VPS hostname or IP |
| `DEPLOY_USER` | SSH user whose `authorized_keys` forces `eternal-actions-gate` |
| `DEPLOY_SSH_KEY` | **Private** key for the Actions deploy key only (PEM / OpenSSH). Never the Windows Owner key. |
| `DEPLOY_KNOWN_HOSTS` | Exact `known_hosts` line(s) for the VPS host key (from Owner `ssh-keyscan`). Enables `StrictHostKeyChecking=yes`. |
| `DEPLOY_SSH_PORT` | Optional SSH port (default 22) |

Also configure Environment protection:

1. **Deployment branches**: `main` only (already).
2. **Required reviewers** (preferred): Owner or a second trusted account.
3. If Required Reviewers cannot be enabled, use one of the fallbacks below.

Workflow also requires inputs `commit_sha` and matching `confirm_sha` before preflight succeeds. Deploy secrets are only injected in the `deploy` job after Environment approval.

## Approval when Required Reviewers is unavailable

Use **all** of these compensating controls:

1. Keep Environment `production` restricted to `main`.
2. Enable an Environment **wait timer** (e.g. 5–15 minutes) so a mistaken dispatch can be cancelled.
3. Require `confirm_sha == commit_sha` (workflow-enforced).
4. Limit who can dispatch workflows (org/repo Actions permissions → Owner only).
5. Optional: add a second Environment secret `DEPLOY_APPROVAL_TOKEN` later and a preflight check that an input must match it — only if reviewers remain unavailable; rotate after use.

Do **not** put deploy private keys in the repo, Issues, or chat logs.

## Operator runbook (after this PR is merged)

1. Merge only business-approved changes to `main`; note the immutable 40-char SHA.
2. Confirm CI is green for that SHA.
3. Actions → **Deploy production** → Run workflow on `main`.
4. Inputs: `commit_sha`, `confirm_sha` (same value), `allow_migrations` only if Owner approved migrations.
5. Approve the Environment `production` deployment (or wait out the timer).
6. Watch job logs; SSH command is only `probe` then `deploy <sha>`.
7. On failure the runner rolls back current symlink + frontend previous tree and leaves `/opt/eternal-packaging` untouched.

## Synthetic verification (no production)

```bash
bash scripts/tests/test_deploy_infrastructure_synthetic.sh
```

## Explicit non-goals

- No production deploy from this documentation alone.
- No Mongo migration execution.
- No use/creation/reading of Owner Windows private keys.
- No `sudo NOPASSWD:ALL`.
