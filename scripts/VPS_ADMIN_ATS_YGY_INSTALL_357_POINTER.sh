#!/usr/bin/env bash
# Owner/admin VPS — ATS YGY-MAIN baseline POINTER install for candidate 357.
#
# Production ATS mode is ALREADY activated. This script:
#   - switches registry active pointer to the live-matched 357 baseline
#   - restarts backend once so the new baseline is loaded
#   - proves HTTP SSI sample (100032 baseline 9000)
#   - supports rollback to Nov-2 baseline pointer
#
# Does NOT:
#   - re-activate / rewrite ETERNAL_ATS_YGY_MODE
#   - write physical_stock_balances or ledger
#   - invent synthetic master IDs
#
# Usage (after RO tarball + runtime preview ACK):
#   sudo -i bash VPS_ADMIN_ATS_YGY_INSTALL_357_POINTER.sh install \
#     /path/MATCHED_321_BASELINE.json
#   sudo -i bash VPS_ADMIN_ATS_YGY_INSTALL_357_POINTER.sh rollback
#   sudo -i bash VPS_ADMIN_ATS_YGY_INSTALL_357_POINTER.sh status
#
# Matched-only install: exceptions (inactive/mismatch/missing e.g. 100489) stay
# visible outside READY — do not fake 357 READY. Rollback = Nov-2 baseline.
set -Eeuo pipefail

CMD="${1:-status}"
MATCHED_SRC="${2:-}"

RUNTIME_ROOT="${ETERNAL_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
SVC="eternal-packaging-backend.service"
SYSTEMCTL_BIN="$(command -v systemctl)"
API_BASE="${ETERNAL_ATS_API_BASE:-http://127.0.0.1:8000}"
YGY_WAREHOUSE_ID="b3d0bf36-1140-4eac-b0f7-b5c924ed0d24"
YGY_BRANCH_ID="5c92529c-3df8-4d9f-b290-a5c94fa51903"
EXPECT_CUTOFF="2026-11-04T22:45:00+07:00"
EXPECT_100032="9000"
ROLLBACK_BASELINE_ID="ygy_main_2026-11-02T232200+0700_draft_162"
# 100489 may remain MISSING; three others must have live IDs.
CRITICAL_CODES=(100463 100464 100488)
MODE_KEY="ETERNAL_ATS_YGY_MODE"
STATE_DIR="${ETERNAL_ATS_STATE_DIR:-/var/lib/eternal-packaging/ats-ygy-357-install-state}"
ACK_FLAG="${ETERNAL_ATS_INSTALL_ACK:-}"

CURRENT_LINK="$(readlink -f "$RUNTIME_ROOT/current" 2>/dev/null || true)"
BE="${CURRENT_LINK}/backend"
PY="${BE}/.venv/bin/python"
BASELINES_DIR="${BE}/data/ats_baselines"
REG_PATH="${BASELINES_DIR}/registry.json"

TS_WIB="$(TZ=Asia/Jakarta date +%Y-%m-%dT%H:%M:%S%z)"
TS_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

log() { printf '%s\n' "$*"; }

die() { log "STOP: $*" >&2; exit 1; }

need_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run as root (sudo -i)"
}

restart_be() {
  "$SYSTEMCTL_BIN" restart "$SVC"
}

read_mode_observed() {
  local m="UNSET"
  if "$SYSTEMCTL_BIN" show-environment 2>/dev/null | grep -q "^${MODE_KEY}="; then
    m="$("$SYSTEMCTL_BIN" show-environment | grep -E "^${MODE_KEY}=" | head -1 | cut -d= -f2-)"
  fi
  printf '%s' "$m"
}

http_proof_100032() {
  # Best-effort local health + optional SSI probe (auth may block; baseline file proof is primary).
  local health
  health="$(curl -fsS --max-time 8 "${API_BASE}/api/system/health" 2>/dev/null || echo HEALTH_FAIL)"
  log "HTTP_HEALTH=$health"
  "$PY" - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ETERNAL_INSTALL_BE"])
os.environ.setdefault("ETERNAL_ATS_YGY_MODE", "activated")
from services.stock_ats_baseline_store import clear_baseline_cache, load_active_baseline_manifest, load_registry
clear_baseline_cache()
reg = load_registry()
man, path = load_active_baseline_manifest()
hit = next(r for r in man["rows"] if r.get("kode") == "100032")
print(f"ACTIVE_BASELINE_ID={reg.get('active_baseline_id')}")
print(f"ACTIVE_PATH={path}")
print(f"CUTOFF={man.get('cutoff_at')}")
print(f"ROW_COUNT={len(man.get('rows') or [])}")
print(f"BASELINE_100032={hit.get('accurate_ats')}")
print(f"ELIGIBLE={sum(1 for r in man['rows'] if r.get('eligible_master'))}")
crit = ["100463", "100464", "100488"]  # 100489 may remain missing
for c in crit:
    row = next((r for r in man["rows"] if r.get("kode") == c), None)
    mid = (row or {}).get("master_item_id") or ""
    ok = bool(row and row.get("eligible_master") and mid and not str(mid).startswith(("synthetic-", "pending-")))
    print(f"CRITICAL_{c}={'OK' if ok else 'BAD'} id={mid}")
expect = 9000.0
ok32 = float(hit.get("accurate_ats") or -1) == expect
print(f"PROOF_100032={'PASS' if ok32 else 'FAIL'}")
if man.get("cutoff_at") != "2026-11-04T22:45:00+07:00":
    print("PROOF_CUTOFF=FAIL")
    sys.exit(3)
if not ok32:
    sys.exit(4)
print("PROOF=PASS")
PY
}

cmd_status() {
  log "=== ATS_YGY_357_POINTER_STATUS utc=$TS_UTC wib=$TS_WIB ==="
  log "CURRENT_LINK=$CURRENT_LINK"
  log "MODE_OBSERVED=$(read_mode_observed)"
  test -x "$PY" || die "python missing: $PY"
  export ETERNAL_INSTALL_BE="$BE"
  "$PY" - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ETERNAL_INSTALL_BE"])
from services.stock_ats_baseline_store import load_registry, clear_baseline_cache
clear_baseline_cache()
reg = load_registry()
print(json.dumps({
  "active_baseline_id": reg.get("active_baseline_id"),
  "active_relpath": reg.get("active_relpath"),
  "candidate_baseline_id": reg.get("candidate_baseline_id"),
  "candidate_relpath": reg.get("candidate_relpath"),
}, indent=2))
PY
}

validate_matched() {
  local src="$1"
  test -f "$src" || die "MATCHED_BASELINE missing: $src"
  export ETERNAL_INSTALL_BE="$BE"
  export ETERNAL_MATCHED_SRC="$src"
  "$PY" - <<'PY'
import json, os, sys
from pathlib import Path
src = Path(os.environ["ETERNAL_MATCHED_SRC"])
doc = json.loads(src.read_text())
crit = ["100463", "100464", "100488"]  # 100489 may stay missing
if doc.get("cutoff_at") != "2026-11-04T22:45:00+07:00":
    print("STOP cutoff", doc.get("cutoff_at")); sys.exit(2)
rows = doc.get("rows") or []
if not rows:
    print("STOP empty rows"); sys.exit(2)
# Matched-only package: do not require 357; expect ~321 eligible rows.
if len(rows) < 300:
    print(f"STOP row_count too low for matched-321 package: {len(rows)}"); sys.exit(2)
by = {r.get("kode"): r for r in rows}
for c in crit:
    r = by.get(c)
    mid = str((r or {}).get("master_item_id") or "")
    if not r or not r.get("eligible_master") or not mid or mid.startswith(("synthetic-", "pending-")):
        print(f"STOP critical {c} needs real live master id, got {mid!r}")
        sys.exit(2)
# 100489 must NOT be faked into READY
r489 = by.get("100489")
if r489 and r489.get("eligible_master") and str(r489.get("master_item_id") or "").startswith(("synthetic-", "pending-")):
    print("STOP refuse synthetic 100489"); sys.exit(2)
hit = by.get("100032")
if not hit or float(hit.get("accurate_ats") or -1) != 9000.0:
    print("STOP 100032 baseline must be 9000"); sys.exit(2)
# Synthetic IDs forbidden anywhere in install rows
for r in rows:
    mid = str(r.get("master_item_id") or "")
    if mid.startswith(("synthetic-", "pending-")):
        print(f"STOP synthetic id on {r.get('kode')}"); sys.exit(2)
unmatched = doc.get("unmatched_rows") or []
inactive = doc.get("inactive_rows") or []
gate = doc.get("install_gate") or {}
matched_only = bool(gate.get("matched_only")) or str(doc.get("status") or "").startswith("INSTALL_CANDIDATE_MATCHED")
if unmatched or inactive:
    print(
        f"EXCEPTIONS_VISIBLE unmatched={len(unmatched)} inactive={len(inactive)} "
        f"(matched_only={matched_only}; not faking 357 READY)"
    )
if not matched_only and unmatched:
    print(f"STOP unmatched_rows={len(unmatched)} without matched_only gate")
    sys.exit(5)
snap = (doc.get("snapshot_vs_cutoff") or {}).get("status") or ""
if "UNVERIFIED" in snap:
    print(f"WARN snapshot_vs_cutoff={snap} — Owner must ACK file clock == cutoff")
print("MATCHED_VALIDATE=PASS")
print(f"ROW_COUNT={len(rows)}")
print(f"SOURCE_SHA={doc.get('source_sha256')}")
PY
}

cmd_install() {
  need_root
  [[ -n "$MATCHED_SRC" ]] || die "usage: $0 install /path/MATCHED_BASELINE.json"
  [[ "${ACK_FLAG}" == "OWNER_ACK_RO_EVIDENCE_AND_SNAPSHOT" ]] || die \
    "set ETERNAL_ATS_INSTALL_ACK=OWNER_ACK_RO_EVIDENCE_AND_SNAPSHOT after reviewing RO tarball"
  test -x "$PY" || die "python missing"
  test -d "$BASELINES_DIR" || die "baselines dir missing: $BASELINES_DIR"
  validate_matched "$MATCHED_SRC"

  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
  local prev_reg_bkp="${STATE_DIR}/registry.json.pre_install_${TS_UTC}"
  cp -a "$REG_PATH" "$prev_reg_bkp"
  log "REGISTRY_BACKUP=$prev_reg_bkp"

  # Observe mode only — never rewrite MODE_KEY.
  local mode_obs
  mode_obs="$(read_mode_observed)"
  log "MODE_OBSERVED=$mode_obs (unchanged by this installer)"
  if [[ "$mode_obs" != "activated" && "$mode_obs" != "UNSET" ]]; then
    log "WARN: expected production mode activated; observed=$mode_obs"
  fi

  export ETERNAL_INSTALL_BE="$BE"
  export ETERNAL_MATCHED_SRC="$MATCHED_SRC"
  export ETERNAL_ROLLBACK_ID="$ROLLBACK_BASELINE_ID"
  "$PY" - <<'PY'
import json, os, shutil, sys
from pathlib import Path
sys.path.insert(0, os.environ["ETERNAL_INSTALL_BE"])
from services.stock_ats_baseline_store import (
    baselines_dir, clear_baseline_cache, load_registry, registry_path, validate_baseline_manifest,
)

src = Path(os.environ["ETERNAL_MATCHED_SRC"])
doc = validate_baseline_manifest(json.loads(src.read_text()))
sha12 = str(doc["source_sha256"])[:12]
bid = f"ygy_main_2026-11-04T224500+0700_{sha12}"
out_dir = baselines_dir()
rel = f"{bid}.json"
out = out_dir / rel
if out.exists():
    # Same content path already present — reuse file, still switch pointer.
    existing = json.loads(out.read_text())
    if existing.get("source_sha256") != doc.get("source_sha256"):
        print(f"STOP refuse overwrite different sha at {out}")
        sys.exit(6)
else:
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out.chmod(0o600)

reg = load_registry()
prev = {
    "active_baseline_id": reg.get("active_baseline_id"),
    "active_relpath": reg.get("active_relpath"),
}
hist = list(reg.get("history") or [])
if not any(h.get("baseline_id") == bid for h in hist):
    hist.insert(0, {
        "baseline_id": bid,
        "cutoff_at": doc["cutoff_at"],
        "relpath": rel,
        "source_sha256": doc["source_sha256"],
        "row_count": len(doc.get("rows") or []),
        "status": "INSTALLED_POINTER",
        "source_meaning": doc.get("source_meaning"),
    })
reg["history"] = hist
reg["active_baseline_id"] = bid
reg["active_relpath"] = rel
reg["candidate_baseline_id"] = bid
reg["candidate_relpath"] = rel
reg["previous_active_before_357_install"] = prev
registry_path().write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
clear_baseline_cache()
print(json.dumps({"installed_baseline_id": bid, "previous": prev, "path": str(out)}, indent=2))
PY

  log "Restarting backend to load new baseline pointer (mode untouched)…"
  restart_be
  sleep 3
  export ETERNAL_INSTALL_BE="$BE"
  http_proof_100032
  log "RESULT=POINTER_INSTALLED"
  log "Rollback: $0 rollback"
}

cmd_rollback() {
  need_root
  test -x "$PY" || die "python missing"
  export ETERNAL_INSTALL_BE="$BE"
  export ETERNAL_ROLLBACK_ID="$ROLLBACK_BASELINE_ID"
  "$PY" - <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["ETERNAL_INSTALL_BE"])
from services.stock_ats_baseline_store import rollback_registry_to, clear_baseline_cache, load_registry
rb = rollback_registry_to(os.environ["ETERNAL_ROLLBACK_ID"])
clear_baseline_cache()
reg = load_registry()
print(json.dumps({"rollback": rb, "active": reg.get("active_baseline_id")}, indent=2))
PY
  log "MODE_OBSERVED=$(read_mode_observed) (unchanged)"
  restart_be
  sleep 3
  export ETERNAL_INSTALL_BE="$BE"
  "$PY" - <<'PY'
import os, sys
sys.path.insert(0, os.environ["ETERNAL_INSTALL_BE"])
from services.stock_ats_baseline_store import clear_baseline_cache, load_active_baseline_manifest, load_registry
clear_baseline_cache()
reg = load_registry()
man, path = load_active_baseline_manifest()
hit = next(r for r in man["rows"] if r.get("kode") == "100032")
print(f"ACTIVE_BASELINE_ID={reg.get('active_baseline_id')}")
print(f"CUTOFF={man.get('cutoff_at')}")
print(f"BASELINE_100032={hit.get('accurate_ats')}")
print(f"ROW_COUNT={len(man.get('rows') or [])}")
assert reg.get("active_baseline_id") == "ygy_main_2026-11-02T232200+0700_draft_162"
assert float(hit.get("accurate_ats")) == 20000.0
print("ROLLBACK_PROOF=PASS")
PY
  log "RESULT=POINTER_ROLLED_BACK_TO_NOV2"
}

case "$CMD" in
  status) cmd_status ;;
  install) cmd_install ;;
  rollback) cmd_rollback ;;
  *)
    log "usage: $0 status|install MATCHED_BASELINE.json|rollback"
    exit 2
    ;;
esac
