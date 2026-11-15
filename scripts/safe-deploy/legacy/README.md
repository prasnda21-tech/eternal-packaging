# LEGACY HEAVY SNAPSHOT — DO NOT USE IN PRODUCTION DEPLOY

These scripts are archived for incident forensics only.

| File | Risk |
|------|------|
| `due_edit_pre_snapshot.DO_NOT_USE.py` | Full-collection `to_list(None)` + JSON normalize/sort/SHA. Confirmed OOM ~4.7–6.0 GiB RSS on production. |
| `due_edit_light_snap.DO_NOT_USE.py` | Still uses `to_list(None)` for multiple collections and large in-memory fingerprint lists. Not safe. |

**Never invoke these from a production deploy path.**

Use instead:

- `scripts/safe-deploy/lib/preflight_data.py` — counts, latest IDs, targeted SKU checks only
- `scripts/safe-deploy/deploy.sh` — 24/7-safe cutover flow

## Incident context (do not conflate)

- Heavy snapshot independently caused confirmed OOM (2026-09-28 ~21:45 WIB).
- The 2026-09-29 ~01:33 WIB outage primary classification was abrupt VPS/hypervisor reboot (MEDIUM confidence); snapshot was **not** proven as the reboot initiator.
