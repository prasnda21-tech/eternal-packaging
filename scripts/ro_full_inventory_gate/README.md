# Hardened RO Full Inventory Gate

Replaces the FAIL-rated `VPS_RO_FULL_INVENTORY_GATE_27df9496.sh` with fail-closed RO credentials, paginated scans, resume checkpoints, and honest FULL/PARTIAL coverage.

## Files
- `VPS_RO_FULL_INVENTORY_GATE_HARDENED.sh` — Owner entrypoint
- `ro_inventory_audit.py` — audit engine
- `uat_synthetic_harness.py` — local synthetic UAT (not for production)
- `SECURITY_REVIEW.md` — Phase 3 verdict

## Local UAT
Requires local mongod with RO user (harness assumes `127.0.0.1:27019` users from agent setup). Then:

```bash
python3 scripts/ro_full_inventory_gate/uat_synthetic_harness.py
```

## Production
See `SECURITY_REVIEW.md` → `EXACT_OWNER_COMMAND`. Do not use app RW `.env` `MONGO_URL`.
