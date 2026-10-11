# Production deploy setup (GitHub Actions → VPS)

Scope: workflows, SSH gate, isolated release runner, Cursor trigger helper, least-privilege bootstrap docs, synthetic tests. Does **not** mutate ERP business source, MongoDB, or run production deploy by itself.

## Separation of concerns

| Concern | Mechanism |
|---------|-----------|
| Trigger GitHub Actions | Credential with **Actions write** (Owner/`gh` or env secret store). `cursor[bot]` read ≠ write. |
| Human deployment control (enforced) | Repository variable `DEPLOY_ALLOWED_ACTORS` (comma-separated GitHub logins). Fail-closed; shared secret alone cannot authorize. |
| Human approval (preferred platform) | GitHub Environment `production` **Required reviewers** when available for this private repo. **Not claimed available without Owner proof.** |
| Technical shared secret | Input `deploy_shared_secret` ↔ `DEPLOY_SHARED_SECRET` (secondary). **Not** human approval. |
| SSH to VPS | Environment secrets `DEPLOY_SSH_*` only inside Actions jobs. |

### Approval reality for this private repo

- Do **not** remove Environment protection or open deploy while migrating.
- Keep technical shared-secret gate as secondary only.
- Enforced substitute today: set `DEPLOY_ALLOWED_ACTORS` (repo Actions variable) to Owner/operator logins that may dispatch.
- Prefer enabling Required reviewers on Environment `production` if/when the platform UI exposes them for this private repo. Until then, do **not** claim reviewers are available; do **not** bypass with secret-only auth.
- Legacy secret name `DEPLOY_APPROVAL_TOKEN` still accepted as alias for `DEPLOY_SHARED_SECRET` during migration.

## Cursor trigger helper (local)

```bash
# Default: plan only — never POSTs
bash scripts/deploy/trigger_github_actions.sh plan probe
bash scripts/deploy/trigger_github_actions.sh plan deploy \
  --sha <40-hex> --confirm-sha <40-hex>

# Execute ONLY after Owner explicitly orders a named SHA deploy/probe
# and a write-capable credential is configured in the environment secret store:
#   ETERNAL_GH_TOKEN or gh auth with Actions write (never commit tokens)
bash scripts/deploy/trigger_github_actions.sh execute probe --execute
bash scripts/deploy/trigger_github_actions.sh execute deploy \
  --sha <40-hex> --confirm-sha <40-hex> --execute

# First cutover that will replace live webroot (e.g. selective-merge FE) with pure SHA:
bash scripts/deploy/trigger_github_actions.sh execute deploy \
  --sha <40-hex> --confirm-sha <40-hex> --ack-replace-live-frontend --execute
```

CLI confirmation flags do **not** prove human approval. Deploy never auto-selects `main` tip.
Shared secret is passed from env into a JSON body via stdin (`gh api --input -`); it is never placed on `gh`/`jq` argv or written to helper logs.

### Owner setup for Cursor-limited Actions write

1. Prefer official Cursor/GitHub App permission upgrade **if** the product exposes Actions write for this repo (audit showed `cursor[bot]` was read-only).
2. If not available: create a fine-scoped PAT or GitHub App installation with **only** `actions:write` + necessary `contents:read` on `prasnda21-tech/eternal-packaging`.
3. Store it in the **Cursor Cloud environment secret store** as e.g. `ETERNAL_GH_TOKEN` (not in source, CLI history, logs, or chat).
4. Helper/docs read from that env at execute time; never echo the value.
5. Do not use `pull_request_target` as an access shortcut.

## Probe-only workflow

Workflow: `.github/workflows/deploy-probe.yml` (manual, `main` only).

- Environment `production` (secrets + protection).
- SSH: `BatchMode`, `StrictHostKeyChecking=yes`, key file mode `600`, cleanup always.
- Remote command literal: `probe` only.
- No deploy runner, build, restart, migrate, or publish.
- Probe PASS = gate connectivity only — **not** runtime readiness.

## Runtime bootstrap checklist (source/docs only — do not run from this agent)

Order matters — **baseline + runtime dirs before service retarget/activation**:

### Phase A — preparation (no live activation)

1. `bootstrap-permissions.sh --apply` (releases, current parent, webroot ownership; narrow sudoers for systemctl restart/is-active/show only).
2. Create bare mirror: `/opt/eternal-packaging-releases/mirror.git`
3. `prepare-verified-baseline.sh --apply` — immutable backend freeze + **live** frontend snapshot (must include `index.html` and selective-merge FE assets) + `.baseline-integrity` / `.baseline-verified` provenance with `frontend_src=` (not marker-only; not symlink to mutable overlay). Does **not** retarget systemd.

### Phase B — activation (after verified baseline exists)

4. Install/adapt unit config (`scripts/deploy/systemd/eternal-packaging-backend.service`) so:
   - `WorkingDirectory=/opt/eternal-packaging-current/backend`
   - `ExecStart` under that prefix
   - `EnvironmentFile=-/opt/eternal-packaging/backend/.env` (legacy secrets stay put)
   Retarget is config-only and comes **after** baseline exists; deploy preflight BLOCKS if WD still points at the old stage path.
5. Point `/opt/eternal-packaging-current` at baseline tree **or** leave unset until first deploy (runner validates integrity then uses baseline for rollback).
6. `install-gate.sh --activate` and confirm probe.
7. Set Environment secrets + repo variable (below).
8. Set `DEPLOY_ALLOWED_ACTORS`. Prefer Required reviewers on Environment `production` when supported; until proven, treat reviewers as unavailable and rely on the allowlist (secret alone is insufficient).

Keep dirty `/opt/eternal-packaging` overlay untouched. Do not archive `main` over it without an explicit Owner reconciliation decision.
Pure SHA deploy does **not** automatically preserve selective-merge FE; first cutover that publishes FE requires `ack_replace_live_frontend` / `deploy <sha> ack-fe-replace`.

## Runner behaviour (high level)

- Shared lock `/tmp/eternal-packaging-deploy.lock` held through rollback.
- Preflight: mirror, releases perms, disk, tools, systemd WD/ExecStart match expected current paths; verified baseline digests (backend + FE snapshot) before first cutover.
- Build isolated; failure does not activate release.
- Backup verified before activation.
- Frontend: merge hashed assets, atomic `index.html` last (no empty webroot window).
- Health requires `mongo=true` and `mode=operational` **and** process/cwd (MainPID) tied to target release after restart.
- Partial restart failure triggers rollback while lock is held.
- No business migrations/backfills.
- Manual selective-merge artifacts (`.manual-selective-merge`) refused.

## GitHub Environment Secrets

| Secret | Purpose |
|--------|---------|
| `DEPLOY_SHARED_SECRET` | Technical gate (preferred). Alias: legacy `DEPLOY_APPROVAL_TOKEN`. |
| `DEPLOY_HOST` | VPS host |
| `DEPLOY_USER` | SSH user (forced-command gate) |
| `DEPLOY_SSH_KEY` | Actions deploy private key only |
| `DEPLOY_KNOWN_HOSTS` | Host key lines |
| `DEPLOY_SSH_PORT` | Optional (default 22) |

## GitHub repository Variables

| Variable | Purpose |
|----------|---------|
| `DEPLOY_ALLOWED_ACTORS` | Comma-separated GitHub logins allowed to authorize deploy (fail-closed). |

## First deploy plan (overlay-safe)

1. Complete bootstrap checklist above (verified baseline + unit retarget).
2. Choose explicit main SHA with required CI green.
3. Run probe workflow; confirm gate only.
4. Dispatch deploy with SHA/confirm/shared-secret + actor on allowlist (+ Environment reviewer approval if configured). For first cutover replacing live FE: set `ack_replace_live_frontend=true`.
5. On failure: runner rolls back to verified baseline FE + CURRENT; overlay untouched.
6. Do not publish manual frontend candidate as if it were a SHA release. Do not assume pure SHA retains overlay FE.

## Synthetic verification (local mocks only)

```bash
bash scripts/tests/test_deploy_infrastructure_synthetic.sh
```

## Explicit non-goals

- No production deploy from this document alone
- No Mongo migration execution
- No Owner Windows private key usage
- No `sudo NOPASSWD:ALL` / arbitrary root script execution
