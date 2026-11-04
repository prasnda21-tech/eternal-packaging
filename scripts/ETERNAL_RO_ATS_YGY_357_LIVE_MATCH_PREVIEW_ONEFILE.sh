#!/usr/bin/env bash
# ETERNAL — READ-ONLY ONLY. No insert/update/delete/adjust/import/deploy/posting/restart/activation/pointer switch.
# One-file YGY-MAIN ATS 357 live master match + post-cutoff preview.
# Self-contained for live SHA 9ff5a0cd (no PR #730 runtime imports).
# Cutoff Owner: 2026-11-04T22:45:00+07:00 → UTC DERIVED 2026-11-04T15:45:00Z
# Critical codes (real live IDs only): 100463 100464 100488 100489
#
# Download + run on VPS (eternaladmin; needs backend .venv + proven backend.env):
#   scp scripts/ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh eternaladmin@HOST:/tmp/
#   ssh eternaladmin@HOST 'bash /tmp/ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh'
set -euo pipefail

R="${ETERNAL_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
# Proven VPS env (Owner activate evidence): NOT the legacy /opt/.../backend/.env path.
ENV_PRIMARY_DECLARED="${ETERNAL_BACKEND_ENV:-/etc/eternal-packaging/backend.env}"
WORK_DIR="${ETERNAL_RO_WORK_DIR:-/tmp/eternal_ro_ats_ygy_357_pkg_$$}"
OUT_DIR="${ETERNAL_RO_OUT_DIR:-/tmp/eternal_ro_ats_ygy_357_$(date -u +%Y%m%dT%H%M%SZ)}"
HOST_NAME="$(hostname 2>/dev/null || echo unknown)"
RUN_USER="$(id -un 2>/dev/null || echo unknown)"

echo "=== RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE_BEGIN utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) wib=$(TZ=Asia/Jakarta date +%Y-%m-%dT%H:%M:%S%z) host=$HOST_NAME user=$RUN_USER ==="
echo "WORK_DIR=$WORK_DIR"
echo "OUT_DIR=$OUT_DIR"

mkdir -p "$WORK_DIR" "$OUT_DIR"
cleanup_work() { rm -rf "$WORK_DIR"; }
trap cleanup_work EXIT

# Resolve proven backend.env (follow symlink like activate script).
ENV_DECLARED="$ENV_PRIMARY_DECLARED"
if [[ -L "$ENV_DECLARED" ]]; then
  ENV_FILE="$(readlink -f "$ENV_DECLARED" 2>/dev/null || true)"
else
  ENV_FILE="$ENV_DECLARED"
fi
if [[ -z "${ENV_FILE:-}" || ! -r "$ENV_FILE" ]]; then
  echo "STOP_ENV declared=$ENV_DECLARED effective=${ENV_FILE:-MISSING} (expected readable /etc/eternal-packaging/backend.env)"
  echo "HINT: do not use legacy /opt/eternal-packaging/backend/.env — that path caused prior RO failures"
  exit 1
fi
echo "ENV_DECLARED=$ENV_DECLARED ENV_EFFECTIVE=$ENV_FILE"

CURRENT_LINK="$(readlink -f "$R/current" 2>/dev/null || true)"
if [[ -z "${CURRENT_LINK:-}" || ! -d "$CURRENT_LINK" ]]; then
  echo "STOP_CURRENT_LINK missing under $R/current"
  exit 1
fi
BE="$CURRENT_LINK/backend"
PY="$BE/.venv/bin/python"
test -x "$PY" || { echo "STOP_PY missing $PY"; exit 1; }

ACTIVE_SHA="$(basename "$CURRENT_LINK")"
ACTIVE_SHA_FILE=""
PREVIOUS_ID=""
if [[ -f "$R/releases/active-release.txt" ]]; then
  ACTIVE_SHA_FILE="$(grep -E '^sha=' "$R/releases/active-release.txt" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
  PREVIOUS_ID="$(grep -E '^previous_id=' "$R/releases/active-release.txt" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
fi

ATS_MODE="UNSET"
if command -v systemctl >/dev/null 2>&1; then
  ATS_MODE="$(systemctl show-environment 2>/dev/null | grep -E '^ETERNAL_ATS_YGY_MODE=' | head -1 | cut -d= -f2- || true)"
  [[ -n "${ATS_MODE:-}" ]] || ATS_MODE="UNSET"
fi

HEALTH_JSON=""
if command -v curl >/dev/null 2>&1; then
  HEALTH_JSON="$(curl -fsS --max-time 8 http://127.0.0.1:8000/api/system/health 2>/dev/null || echo HEALTH_CURL_FAIL)"
fi

cat > "$WORK_DIR/CANDIDATES_357.json" <<'__ETERNAL_ATS_YGY_357_JSON_V1__'
[
  {
    "kode": "100272",
    "nama": "10 oz Oval 8 gr BSM",
    "satuan": "PCS",
    "qty": "15900",
    "jenis_item": "CUP"
  },
  {
    "kode": "100089",
    "nama": "12 oz Datar 5 gr Starindo",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100032",
    "nama": "12 oz Datar 7 gr Starindo",
    "satuan": "PCS",
    "qty": "9000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100001",
    "nama": "12 oz Datar 8 gr SA",
    "satuan": "PCS",
    "qty": "-4000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100057",
    "nama": "12 oz Datar Merak (Retail)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100263",
    "nama": "12 oz Datar RJM",
    "satuan": "PCS",
    "qty": "1900",
    "jenis_item": "CUP"
  },
  {
    "kode": "100079",
    "nama": "12 oz Inject Bening KIIP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100147",
    "nama": "12 oz Inject Bening Wingoh",
    "satuan": "PCS",
    "qty": "4400",
    "jenis_item": "CUP"
  },
  {
    "kode": "100127",
    "nama": "12 oz Inject Datar Bening NJI",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100321",
    "nama": "12 oz Inject Datar Bening Victory",
    "satuan": "PCS",
    "qty": "999",
    "jenis_item": "CUP"
  },
  {
    "kode": "100273",
    "nama": "12 oz Inject Datar Frosted Hitam Doff",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100284",
    "nama": "12 oz Inject Datar Frosted Thinwall/Mister",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100331",
    "nama": "12 oz Inject Datar Frosted Victory",
    "satuan": "PCS",
    "qty": "4999",
    "jenis_item": "CUP"
  },
  {
    "kode": "100259",
    "nama": "12 oz Inject Datar Frosted Wingoh",
    "satuan": "PCS",
    "qty": "10000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100264",
    "nama": "12 oz Oval 7 gr Small Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100031",
    "nama": "12 oz Oval 7 gr Starindo",
    "satuan": "PCS",
    "qty": "16600",
    "jenis_item": "CUP"
  },
  {
    "kode": "100049",
    "nama": "12 oz Oval 8 gr SA",
    "satuan": "PCS",
    "qty": "2200",
    "jenis_item": "CUP"
  },
  {
    "kode": "100056",
    "nama": "12 oz Oval Inject Bening",
    "satuan": "PCS",
    "qty": "500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100341",
    "nama": "12 oz Oval Inject Bening Klir",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100359",
    "nama": "12 oz Oval Inject Bening Super",
    "satuan": "PCS",
    "qty": "3500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100170",
    "nama": "12 oz Oval PET PLP",
    "satuan": "PCS",
    "qty": "-1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100184",
    "nama": "12 oz Oval PET TAM",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100292",
    "nama": "12 oz Oval RYC TAM",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100136",
    "nama": "12 oz Papper Cup Cokelat Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100083",
    "nama": "12 oz Papper Cup Cold Putih Starindo",
    "satuan": "PCS",
    "qty": "400",
    "jenis_item": "CUP"
  },
  {
    "kode": "100194",
    "nama": "12 oz Papper Cup Hot Putih BSM",
    "satuan": "PCS",
    "qty": "-500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100067",
    "nama": "12 oz Papper Cup Hot Putih Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100003",
    "nama": "12 oz PET Datar 11 gr SJP",
    "satuan": "PCS",
    "qty": "-7801",
    "jenis_item": "CUP"
  },
  {
    "kode": "100146",
    "nama": "12 oz PET Datar 11 gr TAM",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100080",
    "nama": "14 oz Datar 5 gr Starindo",
    "satuan": "PCS",
    "qty": "7000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100015",
    "nama": "14 oz Datar 7 gr Starindo",
    "satuan": "PCS",
    "qty": "5500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100209",
    "nama": "14 oz Datar Big 7 gr Starindo",
    "satuan": "PCS",
    "qty": "3500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100058",
    "nama": "14 oz Datar Merak (Retail)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100217",
    "nama": "14 oz Inject Datar Bening Klir",
    "satuan": "PCS",
    "qty": "6000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100241",
    "nama": "14 oz Inject Datar Bening Super",
    "satuan": "PCS",
    "qty": "-1500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100061",
    "nama": "14 oz Inject Datar Bening Victory",
    "satuan": "PCS",
    "qty": "5000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100328",
    "nama": "14 oz Inject Datar Frosted Hitam Victory",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100293",
    "nama": "14 oz Inject Datar Frosted Hitam Wingoh",
    "satuan": "PCS",
    "qty": "9475",
    "jenis_item": "CUP"
  },
  {
    "kode": "100233",
    "nama": "14 oz Inject Datar Frosted Klir",
    "satuan": "PCS",
    "qty": "7000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100319",
    "nama": "14 oz Inject Datar Frosted Super",
    "satuan": "PCS",
    "qty": "15000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100137",
    "nama": "14 oz Inject Datar Frosted Victory",
    "satuan": "PCS",
    "qty": "8500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100332",
    "nama": "14 oz Inject Datar Frosted Wingoh",
    "satuan": "PCS",
    "qty": "3000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100030",
    "nama": "14 oz Oval 8 gr BSM",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100010",
    "nama": "14 oz Oval 8 gr Starindo",
    "satuan": "PCS",
    "qty": "3650",
    "jenis_item": "CUP"
  },
  {
    "kode": "100193",
    "nama": "14 oz Oval PET PLP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100006",
    "nama": "14 oz PET Datar 11 gr PLP",
    "satuan": "PCS",
    "qty": "21500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100019",
    "nama": "14 oz PET Oval 11 gr SJP",
    "satuan": "PCS",
    "qty": "2000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100033",
    "nama": "16 oz Datar 5 gr Starindo",
    "satuan": "PCS",
    "qty": "12000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100016",
    "nama": "16 oz Datar 7 gr Starindo",
    "satuan": "PCS",
    "qty": "1100",
    "jenis_item": "CUP"
  },
  {
    "kode": "100002",
    "nama": "16 oz Datar 8 gr SA",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100059",
    "nama": "16 oz Datar Merak (Retail)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100151",
    "nama": "16 oz Inject Datar Bening GSS",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100226",
    "nama": "16 oz Inject Datar Bening Klir",
    "satuan": "PCS",
    "qty": "7150",
    "jenis_item": "CUP"
  },
  {
    "kode": "100242",
    "nama": "16 oz Inject Datar Bening Super",
    "satuan": "PCS",
    "qty": "6000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100220",
    "nama": "16 oz Inject Datar Bening Thinwal",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100076",
    "nama": "16 oz Inject Datar Bening Victory",
    "satuan": "PCS",
    "qty": "4000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100215",
    "nama": "16 oz Inject Datar Bening Wingoh",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100165",
    "nama": "16 oz Inject Datar Frosted Victory",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100333",
    "nama": "16 oz Inject Datar Frosted Wingoh",
    "satuan": "PCS",
    "qty": "10250",
    "jenis_item": "CUP"
  },
  {
    "kode": "100339",
    "nama": "16 oz Inject Datar Silver",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100132",
    "nama": "16 oz Oval 8 gr Merak",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100050",
    "nama": "16 oz Oval 8 gr SA",
    "satuan": "PCS",
    "qty": "1500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100011",
    "nama": "16 oz Oval 8 gr Starindo",
    "satuan": "PCS",
    "qty": "2997",
    "jenis_item": "CUP"
  },
  {
    "kode": "100133",
    "nama": "16 oz Oval Tip Top",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100084",
    "nama": "16 oz Papper Cup Cold Putih Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100004",
    "nama": "16 oz PET Datar 11 gr SJP",
    "satuan": "PCS",
    "qty": "2000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100150",
    "nama": "16 oz PET Datar 11 gr TAM",
    "satuan": "PCS",
    "qty": "500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100107",
    "nama": "18 oz Datar 7 gr Merak",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100218",
    "nama": "18 oz Datar 7 gr Slim Merak",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100347",
    "nama": "18 oz Datar 8 gr Merak",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100040",
    "nama": "18 oz Datar 8 gr Starindo",
    "satuan": "PCS",
    "qty": "2500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100162",
    "nama": "18 oz Datar Slim Tip Top",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100231",
    "nama": "18 oz Oval 7 gr Merak",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100012",
    "nama": "18 oz Oval 8 gr Starindo",
    "satuan": "PCS",
    "qty": "2000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100007",
    "nama": "18 oz PET Datar 11 gr PLP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100078",
    "nama": "22 OZ BSM 8 GR",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100202",
    "nama": "22 oz Datar 7 gr Indoku",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100094",
    "nama": "22 oz Datar 7 gr Merak",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100329",
    "nama": "22 oz Datar 8 Gr Aori",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100287",
    "nama": "22 oz Datar 8 gr Atlas",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100085",
    "nama": "22 oz Datar 8 gr Euro",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100324",
    "nama": "22 oz Datar 8 Gr Fresh One",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100201",
    "nama": "22 oz Datar 8 gr Indoku",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100239",
    "nama": "22 oz Datar 8 gr Kappe",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100140",
    "nama": "22 oz Datar 8 gr Merak",
    "satuan": "PCS",
    "qty": "14000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100188",
    "nama": "22 oz Datar 8 gr Regal",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100124",
    "nama": "22 oz Datar 8 gr SA",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100086",
    "nama": "22 oz Datar 8 gr Starindo",
    "satuan": "PCS",
    "qty": "5000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100149",
    "nama": "22 oz Datar 8 gr Unggul",
    "satuan": "PCS",
    "qty": "2000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100126",
    "nama": "22 oz Datar 8,5 gr TipTop",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100171",
    "nama": "22 oz Datar 9 gr Merak",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100125",
    "nama": "22 oz Datar 9 gr SA",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100047",
    "nama": "22 oz Datar 9,5 gr Starindo",
    "satuan": "PCS",
    "qty": "950",
    "jenis_item": "CUP"
  },
  {
    "kode": "100153",
    "nama": "22 oz Datar Rib 8 gr TAM",
    "satuan": "PCS",
    "qty": "109000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100121",
    "nama": "22 oz Datar Slim Tip Top",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100071",
    "nama": "22 OZ EURO",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100066",
    "nama": "22 oz HOK Slim",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100112",
    "nama": "22 oz Oval 8 gr Merak",
    "satuan": "PCS",
    "qty": "3000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100073",
    "nama": "22 oz Oval 9.5 gr Starindo",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100128",
    "nama": "22 oz Oval Tiptop",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100181",
    "nama": "22 oz Papper Cup Cold Putih Starindo",
    "satuan": "PCS",
    "qty": "900",
    "jenis_item": "CUP"
  },
  {
    "kode": "100008",
    "nama": "22 oz PET Datar 11 gr PLP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100063",
    "nama": "22 oz PET Datar 11 gr SJP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100228",
    "nama": "24 oz Inject Datar Bening KLIR",
    "satuan": "PCS",
    "qty": "2500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100246",
    "nama": "24 oz Inject Datar Bening Super",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100104",
    "nama": "24 oz Inject Datar Bening Victory",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100222",
    "nama": "8 oz Oval PET PLP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100098",
    "nama": "8 oz PET 6.2 gr Starindo",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100163",
    "nama": "8 oz PET Cup Pudding PLP",
    "satuan": "PCS",
    "qty": "3000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100299",
    "nama": "Bowl Thinwall 450 ml + LID Victory",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100298",
    "nama": "Bowl Thinwall 650 ml + LID Victory",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100336",
    "nama": "Lid Bening Papper Bowl 350/500 ml Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100338",
    "nama": "Lid Bening Papper Bowl 650 ml Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100077",
    "nama": "PAPPER BOWL + LID 1000 ML PRINTING",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100054",
    "nama": "Papper Bowl 500 ML",
    "satuan": "PCS",
    "qty": "2000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100335",
    "nama": "Papper Bowl 500 ml Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100041",
    "nama": "Papper Bowl 650 ML",
    "satuan": "PCS",
    "qty": "2100",
    "jenis_item": "CUP"
  },
  {
    "kode": "100337",
    "nama": "Papper Bowl 650 ml Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100069",
    "nama": "Papper Bowl 720 ML",
    "satuan": "PCS",
    "qty": "300",
    "jenis_item": "CUP"
  },
  {
    "kode": "100072",
    "nama": "Papper Bowl 800 ML",
    "satuan": "PCS",
    "qty": "300",
    "jenis_item": "CUP"
  },
  {
    "kode": "100199",
    "nama": "Papper Bowl Coklat/Craft 650 ML",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100034",
    "nama": "Papper Cup Coklat/Craft 8 oz Starindo",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100082",
    "nama": "Papper Cup Cold 10 oz Putih",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100232",
    "nama": "Papper Cup Double Wall 12 oz Putih",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100051",
    "nama": "Papper Cup Double Wall 8 oz Coklat/Craft",
    "satuan": "PCS",
    "qty": "4500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100168",
    "nama": "Papper Cup Double Wall 8 oz Putih",
    "satuan": "PCS",
    "qty": "8644",
    "jenis_item": "CUP"
  },
  {
    "kode": "100021",
    "nama": "Papper Cup Hitam 8 oz Starindo",
    "satuan": "PCS",
    "qty": "2994",
    "jenis_item": "CUP"
  },
  {
    "kode": "100152",
    "nama": "Papper Cup Hot Hijau 8 oz",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100060",
    "nama": "Papper Cup Putih 4.5 oz",
    "satuan": "PCS",
    "qty": "-500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100081",
    "nama": "Papper Cup Putih 6.5 oz",
    "satuan": "PCS",
    "qty": "500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100036",
    "nama": "Papper Cup Putih 8 oz Starindo",
    "satuan": "PCS",
    "qty": "-550",
    "jenis_item": "CUP"
  },
  {
    "kode": "100367",
    "nama": "Botol Pet Kalle Chubby 100 ML",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100369",
    "nama": "12 oz Datar 3,8 gr  Bintang",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100370",
    "nama": "12 oz Datar 3,8 gr Hok",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100371",
    "nama": "12 oz Datar 3,8 gr EraCup",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100372",
    "nama": "12 oz Datar 3,8 gr  Starlite",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100373",
    "nama": "12 oz Datar 3,8 gr O-Cup",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100375",
    "nama": "22 oz Datar 7 gr Starlite",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100377",
    "nama": "16 oz Datar 9 gr SA Putih",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100384",
    "nama": "16 oz Datar 5 gr Aori",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100374",
    "nama": "12 oz Inject Oval Bening Victory",
    "satuan": "PCS",
    "qty": "-3500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100385",
    "nama": "12 oz Datar 7 gr Polycup",
    "satuan": "PCS",
    "qty": "150",
    "jenis_item": "CUP"
  },
  {
    "kode": "100386",
    "nama": "16 oz Datar 7 gr Polycup",
    "satuan": "PCS",
    "qty": "47",
    "jenis_item": "CUP"
  },
  {
    "kode": "100388",
    "nama": "12 oz Inject Datar Frosted Hitam Wingoh",
    "satuan": "PCS",
    "qty": "3500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100398",
    "nama": "10 oz Datar 4 gr TipTop",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100397",
    "nama": "12 oz Inject Datar Bening Klir",
    "satuan": "PCS",
    "qty": "-3000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100395",
    "nama": "PP Bowl 650 ML Putih TipTop",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100382",
    "nama": "12 oz Datar 7 gr GoCup",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100399",
    "nama": "18 oz Datar 7 gr GoCup",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100400",
    "nama": "16 oz Datar 5 gr GoCup",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100401",
    "nama": "Papper Bowl 1000 ML Putih",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100404",
    "nama": "12 oz Datar Slim 7 gr Polycup",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100405",
    "nama": "12 oz Oval 7 gr Polycup",
    "satuan": "PCS",
    "qty": "100",
    "jenis_item": "CUP"
  },
  {
    "kode": "100406",
    "nama": "14 oz Datar 7 gr Polycup",
    "satuan": "PCS",
    "qty": "97",
    "jenis_item": "CUP"
  },
  {
    "kode": "100407",
    "nama": "14 oz Oval 7 gr Polycup",
    "satuan": "PCS",
    "qty": "100",
    "jenis_item": "CUP"
  },
  {
    "kode": "100408",
    "nama": "16 oz Datar 8 gr Polycup",
    "satuan": "PCS",
    "qty": "50",
    "jenis_item": "CUP"
  },
  {
    "kode": "100412",
    "nama": "16 oz Oval 8 gr Polycup",
    "satuan": "PCS",
    "qty": "50",
    "jenis_item": "CUP"
  },
  {
    "kode": "100409",
    "nama": "18 oz Datar Slim 9 gr Polycup",
    "satuan": "PCS",
    "qty": "50",
    "jenis_item": "CUP"
  },
  {
    "kode": "100410",
    "nama": "22 oz Datar 9 gr Polycup",
    "satuan": "PCS",
    "qty": "50",
    "jenis_item": "CUP"
  },
  {
    "kode": "100411",
    "nama": "22 oz Oval 9 gr Polycup",
    "satuan": "PCS",
    "qty": "50",
    "jenis_item": "CUP"
  },
  {
    "kode": "100414",
    "nama": "Papper Bowl 650 ML Craft Wmp",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100422",
    "nama": "22 oz Datar 8 gr Berlian",
    "satuan": "PCS",
    "qty": "500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100427",
    "nama": "22 oz Datar 7 gr Berlian",
    "satuan": "PCS",
    "qty": "5500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100429",
    "nama": "Bowl Thinwall 500 ml + LID Victory",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100432",
    "nama": "12 oz Inject Datar Frosted Klir",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "CUP"
  },
  {
    "kode": "100433",
    "nama": "16 oz Inject Datar Frosted Klir",
    "satuan": "PCS",
    "qty": "500",
    "jenis_item": "CUP"
  },
  {
    "kode": "100230",
    "nama": "LID 12 oz Double Wall Hitam",
    "satuan": "PCS",
    "qty": "2000",
    "jenis_item": "LID"
  },
  {
    "kode": "100167",
    "nama": "LID 8 oz Double Wall White",
    "satuan": "PCS",
    "qty": "250",
    "jenis_item": "LID"
  },
  {
    "kode": "100055",
    "nama": "LID Bening Papper Bowl 500 ML",
    "satuan": "PCS",
    "qty": "2500",
    "jenis_item": "LID"
  },
  {
    "kode": "100042",
    "nama": "LID Bening Papper Bowl 650 ML Wmp",
    "satuan": "PCS",
    "qty": "1100",
    "jenis_item": "LID"
  },
  {
    "kode": "100070",
    "nama": "LID Bening Papper Bowl 720 ML",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100205",
    "nama": "LID Bening Papper Bowl 800 ML",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100062",
    "nama": "LID Cembung DB Starindo",
    "satuan": "PCS",
    "qty": "2000",
    "jenis_item": "LID"
  },
  {
    "kode": "100216",
    "nama": "LID Cembung PET 8 oz 6.2 gr Starindo",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID"
  },
  {
    "kode": "100275",
    "nama": "LID Connect Frosted Biru Tua",
    "satuan": "PCS",
    "qty": "5000",
    "jenis_item": "LID"
  },
  {
    "kode": "100274",
    "nama": "LID Connect Frosted Hitam Doff",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100286",
    "nama": "LID Connect Frosted Hitam Victory",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID"
  },
  {
    "kode": "100131",
    "nama": "LID Connect Inject Bening GSS",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100244",
    "nama": "LID Connect Inject Bening Klir",
    "satuan": "PCS",
    "qty": "-7651",
    "jenis_item": "LID"
  },
  {
    "kode": "100243",
    "nama": "LID Connect Inject Bening Super",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100115",
    "nama": "LID Connect Inject Bening Victory",
    "satuan": "PCS",
    "qty": "3500",
    "jenis_item": "LID"
  },
  {
    "kode": "100148",
    "nama": "LID Connect Inject Bening Wingoh",
    "satuan": "PCS",
    "qty": "-3500",
    "jenis_item": "LID"
  },
  {
    "kode": "100334",
    "nama": "LID Connect Inject Frosted Wingoh",
    "satuan": "PCS",
    "qty": "9000",
    "jenis_item": "LID"
  },
  {
    "kode": "100129",
    "nama": "LID Connect Inject Frosted/Dop Victory",
    "satuan": "PCS",
    "qty": "849",
    "jenis_item": "LID"
  },
  {
    "kode": "100116",
    "nama": "LID Connect Inject Hitam",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID"
  },
  {
    "kode": "100389",
    "nama": "LID Connect Inject Hitam Frosted Wingoh",
    "satuan": "PCS",
    "qty": "9000",
    "jenis_item": "LID"
  },
  {
    "kode": "100394",
    "nama": "LID Connect Inject Hitam Glosy Klir",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100393",
    "nama": "LID Connect Inject Hitam Glosy Super",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100304",
    "nama": "LID Connect Inject Merah",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID"
  },
  {
    "kode": "100027",
    "nama": "LID Datar DB Starindo",
    "satuan": "PCS",
    "qty": "7000",
    "jenis_item": "LID"
  },
  {
    "kode": "100099",
    "nama": "LID Datar DK Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100164",
    "nama": "LID Flat Non Cross PLP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100178",
    "nama": "LID Flat PP TAM",
    "satuan": "PCS",
    "qty": "2000",
    "jenis_item": "LID"
  },
  {
    "kode": "100166",
    "nama": "Lid Hitam Paper 9 oz Starindo",
    "satuan": "PCS",
    "qty": "4494",
    "jenis_item": "LID"
  },
  {
    "kode": "100130",
    "nama": "LID Hitam Papper Cup 12 oz",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100195",
    "nama": "LID Hitam Papper Cup 12 oz BSM",
    "satuan": "PCS",
    "qty": "-2000",
    "jenis_item": "LID"
  },
  {
    "kode": "100198",
    "nama": "LID Hitam Papper Cup 12 oz TPP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100026",
    "nama": "LID Hitam Papper Cup 8 oz STR",
    "satuan": "PCS",
    "qty": "2494",
    "jenis_item": "LID"
  },
  {
    "kode": "100390",
    "nama": "LID Inject Bening Dome Wingoh",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID"
  },
  {
    "kode": "100330",
    "nama": "LID Inject Flip Bening Klir",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100247",
    "nama": "LID Inject Flip Bening Victory",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100260",
    "nama": "LID Inject Flip Bening Wingoh",
    "satuan": "PCS",
    "qty": "-200",
    "jenis_item": "LID"
  },
  {
    "kode": "100288",
    "nama": "LID Inject Flip Hitam Victory",
    "satuan": "PCS",
    "qty": "8500",
    "jenis_item": "LID"
  },
  {
    "kode": "100277",
    "nama": "LID Inject Flip Hitam Wingoh",
    "satuan": "PCS",
    "qty": "6500",
    "jenis_item": "LID"
  },
  {
    "kode": "100342",
    "nama": "LID Inject Flip Merah Wingoh",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100093",
    "nama": "LID Inject Rigid",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100117",
    "nama": "LID Inject Stopper Hitam",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100119",
    "nama": "LID Inject Stopper Merah",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100118",
    "nama": "LID Inject Stopper Putih",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100092",
    "nama": "LID Papper Cup 6.5 oz",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100035",
    "nama": "LID PET DOME PLP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100203",
    "nama": "LID PET Dome Starindo",
    "satuan": "PCS",
    "qty": "700",
    "jenis_item": "LID"
  },
  {
    "kode": "100025",
    "nama": "LID PET FLAT PLP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100087",
    "nama": "LID PET FLAT Starindo",
    "satuan": "PCS",
    "qty": "6000",
    "jenis_item": "LID"
  },
  {
    "kode": "100322",
    "nama": "LID PET FLAT TAM",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100271",
    "nama": "LID PET Strawless New PLP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100023",
    "nama": "LID PET Strawless PLP",
    "satuan": "PCS",
    "qty": "6400",
    "jenis_item": "LID"
  },
  {
    "kode": "100123",
    "nama": "LID PET Strawless Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100240",
    "nama": "LID PET Strawless TPP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100396",
    "nama": "LID PP Bowl Bening TipTop",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID"
  },
  {
    "kode": "100037",
    "nama": "LID PP Strawless Starindo",
    "satuan": "PCS",
    "qty": "2900",
    "jenis_item": "LID"
  },
  {
    "kode": "100196",
    "nama": "LID Putih Papper Cup 12 oz BSM",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100204",
    "nama": "LID Putih Papper Cup 12 oz STR",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100048",
    "nama": "LID Putih Papper Cup 8 oz STR",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID"
  },
  {
    "kode": "100068",
    "nama": "LID Putih Papper Cup Cold 12 oz Starindo",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100145",
    "nama": "LID Putih Papper Cup Cold 16/22 oz Starindo",
    "satuan": "ROLL",
    "qty": "5000",
    "jenis_item": "LID"
  },
  {
    "kode": "100100",
    "nama": "LID SEALER 1300",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100380",
    "nama": "Lid Sealer Bos Motif ThankYou 750",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100379",
    "nama": "LID SEALER MOTIF PANDA 1200",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100368",
    "nama": "Lid/Tutup Botol Pet Kalle Putih",
    "satuan": "ROLL",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100402",
    "nama": "LID Papper Bowl 1000 ML Bening",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100428",
    "nama": "Lid Sealer Bos Motif Daun Teh 750",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100435",
    "nama": "LID Sealer Alibaba",
    "satuan": "PCS",
    "qty": "24",
    "jenis_item": "LID"
  },
  {
    "kode": "100135",
    "nama": "12 oz Datar 8 gr Printing SA",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100101",
    "nama": "12 oz Oval 8 gr Printing SA",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100169",
    "nama": "12 oz PET Datar 11 gr Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100088",
    "nama": "12 oz PET Datar 11 gr Printing SJP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100159",
    "nama": "12 oz PET Datar 11 gr TAM Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100091",
    "nama": "16 OZ PET SJP PRINTING",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100206",
    "nama": "16 oz Datar 7 gr Starindo Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100141",
    "nama": "22 oz Datar 7 gr Printing TAM",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100182",
    "nama": "22 oz Datar Rib 8 gr TAM PRINTING",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100197",
    "nama": "Papper Bowl 650 ML Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100229",
    "nama": "Papper Cup Double Wall 12 oz Printing",
    "satuan": "PCS",
    "qty": "28",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100211",
    "nama": "Papper Cup Double Wall 8 oz Printing",
    "satuan": "PCS",
    "qty": "4495",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100312",
    "nama": "Papper Cup Single 12 oz Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100313",
    "nama": "Papper Cup Single 16 oz Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100245",
    "nama": "Papper Cup Single 8 oz Printing",
    "satuan": "PCS",
    "qty": "1100",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100381",
    "nama": "Papper Cup Double Wall 16 oz Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP PRINTING"
  },
  {
    "kode": "100340",
    "nama": "LID Inject Flip Leak Proof",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID PRINTING"
  },
  {
    "kode": "100317",
    "nama": "LID Papper Cup 12/16 oz Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID PRINTING"
  },
  {
    "kode": "100311",
    "nama": "LID Papper Cup 8 oz Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID PRINTING"
  },
  {
    "kode": "100172",
    "nama": "PLASTIK HDPE 15 x 25",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100173",
    "nama": "PLASTIK HDPE 24 x 32",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100174",
    "nama": "PLASTIK HDPE 28 x 38",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100219",
    "nama": "PLASTIK OPP SEAL 30 X 40",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100252",
    "nama": "PLASTIK PE 05 / 30 x 50",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100253",
    "nama": "PLASTIK PE 05 / 40 x 60",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100096",
    "nama": "PLASTIK PE 20 x 40",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100143",
    "nama": "Plastik PE 28",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100113",
    "nama": "Plastik PE Single Cup (G1)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100179",
    "nama": "PLASTIK PUTIH 21 x 40",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100108",
    "nama": "PLASTIK PUTIH 24 x 42",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100180",
    "nama": "PLASTIK PUTIH 28 x 48",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100248",
    "nama": "PLASTIK SELONGSONG CUP",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100187",
    "nama": "PLASTIK SLOP CUP",
    "satuan": "PCS",
    "qty": "301",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100109",
    "nama": "PLASTIK TRASHBAG",
    "satuan": "PCS",
    "qty": "1",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100210",
    "nama": "Plastik HD Plong Putih 20 x 30",
    "satuan": "PCS",
    "qty": "20000",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100225",
    "nama": "Plastik Klip Pond Hitam 20 x 30",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100227",
    "nama": "Plastik Klip Pond Transparan 30 x 38",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100290",
    "nama": "Plastik PE Bening Party Besar 35",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100276",
    "nama": "Plastik PE Bening Party Kecil 15",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100301",
    "nama": "Plastik PE Bening Party Super 30",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100097",
    "nama": "Plastik PE Bening Party Tanggung 24",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100294",
    "nama": "Plastik PE Double Cup Lebar",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100114",
    "nama": "Plastik PE Double Cup Tinggi (G2)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100267",
    "nama": "Plastik Plong Transparan 25 x 40",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100261",
    "nama": "Plastik Plong Transparan 30 x 45",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100235",
    "nama": "Standing Klip Window Hitam 14 x 22",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100236",
    "nama": "Standing Klip Window Hitam 16 x 24",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100352",
    "nama": "Plastik Kresek Macan Putih 75 x 50",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100354",
    "nama": "PLASTIK KRESEK MACAN PUTIH  65 X 45",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100314",
    "nama": "STRAW / SEDOTAN BOBA",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100052",
    "nama": "STRAW/SEDOTAN LANCIP 620 ISI 300 PCS",
    "satuan": "PCS",
    "qty": "24",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100053",
    "nama": "SEAL KAKA ISI 1200",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100110",
    "nama": "STRAW/SEDOTAN LANCIP 620 ISI 250 PCS",
    "satuan": "PCS",
    "qty": "-2000",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100250",
    "nama": "STRAW/SEDOTAN TEKUK 620 ISI 250 PCS",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100320",
    "nama": "STRAW/SEDOTAN HITAM MASTER/BOBA",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100111",
    "nama": "Bubble Wrap Hitam",
    "satuan": "PCS",
    "qty": "24",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100355",
    "nama": "Stirrer/Sedotan Kopi 12,5 Coklat",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100346",
    "nama": "STRAW/SEDOTAN LANCIP 620 ISI 500 PCS",
    "satuan": "PCS",
    "qty": "21",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100185",
    "nama": "STRAW / SEDOTAN STIRRER HITAM",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100186",
    "nama": "STRAW / SEDOTAN STIRRER COKLAT",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100362",
    "nama": "SOS  Papper Bag Cokelat M Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100363",
    "nama": "SOS  Papper Bag Cokelat L Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100364",
    "nama": "SOS  Papper Bag Cokelat S Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100365",
    "nama": "SOS  Papper Bag Putih M Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100403",
    "nama": "Plastik Klip Zipper Buram/Doff  30 x 40",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100418",
    "nama": "Plastik PE 15",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100120",
    "nama": "Papper Rice",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100214",
    "nama": "Papper Rice Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100269",
    "nama": "Wrap Papper Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100315",
    "nama": "Wrap Papper Printing 10.20",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100316",
    "nama": "Wrap Papper Printing 27.27",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100361",
    "nama": "Wrap Paper Printing 28 X 28",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100345",
    "nama": "Wrap Paper Printing 20 X 20",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100387",
    "nama": "Wrap Paper Printing 14 X 14",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100426",
    "nama": "Wrap Paper Printing 22.5 X 35",
    "satuan": "PCS",
    "qty": "1500",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100430",
    "nama": "Wrap Paper Printing 18 X 18",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100417",
    "nama": "Leak Proof Paper 125 mm",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100413",
    "nama": "Wrap Paper Printing 24 X 24",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PAPPER RICE"
  },
  {
    "kode": "100234",
    "nama": "Box Craft 16 X 16 Sambung",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100103",
    "nama": "Box Craft 20 X 20",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100249",
    "nama": "Box Craft 20 X 20 Sambung",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100183",
    "nama": "Box Pizza 30 x 30 cm",
    "satuan": "PCS",
    "qty": "1498",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100212",
    "nama": "Burger Box",
    "satuan": "PCS",
    "qty": "500",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100237",
    "nama": "Cup Holder (Sleeve) 8 Oz",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100046",
    "nama": "LUNCH BOX COKELAT L",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100045",
    "nama": "LUNCH BOX COKELAT M",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100044",
    "nama": "Lunch Box Cokelat (S)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100213",
    "nama": "Lunch Box Cokelat Aldo (L)",
    "satuan": "PCS",
    "qty": "150",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100200",
    "nama": "Lunch Box Cokelat Aldo (M)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100279",
    "nama": "Lunch Box Eco (S)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100295",
    "nama": "Lunch Box Printing (L)",
    "satuan": "PCS",
    "qty": "1190",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100189",
    "nama": "Lunch Box Printing (M)",
    "satuan": "PCS",
    "qty": "1300",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100296",
    "nama": "Lunch Box Printing (S)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100297",
    "nama": "Lunch Box Printing Custom",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100177",
    "nama": "Lunch Box Printing Full Laminasi",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100064",
    "nama": "Lunch Box Putih (M)",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100238",
    "nama": "Mika Box 20 x 20",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100142",
    "nama": "Papper Bag 15 x 30",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100190",
    "nama": "Papper Bag Food Printing",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100289",
    "nama": "Papper Take Away Custom",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100208",
    "nama": "Papper Tray Coklat/Craft L",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100207",
    "nama": "Papper Tray Coklat/Craft M",
    "satuan": "PCS",
    "qty": "800",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100291",
    "nama": "Papper Tray Coklat/Craft S",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100191",
    "nama": "Papper Tray Printing",
    "satuan": "PCS",
    "qty": "5",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100224",
    "nama": "SOS Papper Bag Coklat L",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100302",
    "nama": "SOS Papper Bag Coklat M",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100303",
    "nama": "SOS Papper Bag Putih M",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100360",
    "nama": "SOS Papper Bag Coklat S",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100376",
    "nama": "Box Craft 18 X 18 Sambung",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100268",
    "nama": "Standing Klip Pouch Craft 13 x 20 cm",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING"
  },
  {
    "kode": "100266",
    "nama": "Standing Klip Pouch Craft 14 x 23 cm",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING"
  },
  {
    "kode": "100265",
    "nama": "Standing Klip Pouch Craft 22 x 33 cm",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING"
  },
  {
    "kode": "100251",
    "nama": "Standing Klip Pouch Craft 22 x 33 cm",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING"
  },
  {
    "kode": "100434",
    "nama": "12 oz Inject Datar Frosted Hitam Merak",
    "satuan": "PCS",
    "qty": "3950",
    "jenis_item": "CUP"
  },
  {
    "kode": "100454",
    "nama": "12 Oz Pet Souffle Plp",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100439",
    "nama": "Papper Tray Printing S",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100458",
    "nama": "Cup Holder (Sleeve) 12 Oz",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100459",
    "nama": "Papper Cup Putih 8 oz Plain Irp",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100461",
    "nama": "LID Hitam Papper Cup 8 oz Irp",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "LID"
  },
  {
    "kode": "100463",
    "nama": "Thinwall RoundBowl 750 Ml Victory + Lid",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "CUP"
  },
  {
    "kode": "100464",
    "nama": "Papper Tray Printing M",
    "satuan": "PCS",
    "qty": "0",
    "jenis_item": "PACKAGING PAPPER"
  },
  {
    "kode": "100460",
    "nama": "LID PET Strawless Tam",
    "satuan": "PCS",
    "qty": "100396",
    "jenis_item": "LID"
  },
  {
    "kode": "100488",
    "nama": "Plastik Klip Pond Transparan 30 x 40",
    "satuan": "PCS",
    "qty": "500",
    "jenis_item": "PLASTIK"
  },
  {
    "kode": "100489",
    "nama": "LID Connect Inject Orange Glosy Starglas",
    "satuan": "PCS",
    "qty": "1000",
    "jenis_item": "LID"
  }
]


__ETERNAL_ATS_YGY_357_JSON_V1__

cat > "$WORK_DIR/ro_ats_ygy_357_live_preview.py" <<'__ETERNAL_ATS_YGY_357_PY_V1__'
#!/usr/bin/env python3
"""READ-ONLY live match + post-cutoff ATS preview for YGY-MAIN 357.

Self-contained for live release 9ff5a0cd:
  - NO imports of stock_ats runtime / SSI catalog helpers (PR #730 may be undeployed)
  - Uses motor + dotenv from current backend .venv only
  - Mongo URI/DB from proven VPS env (/etc/eternal-packaging/backend.env)

Cutoff Owner: 2026-11-04T22:45:00+07:00
UTC is DERIVED from CUTOFF_AT (never independently hardcoded).

No insert/update/delete/adjust/import/deploy/posting/restart/activation/pointer switch.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReadPreference

WH = "b3d0bf36-1140-4eac-b0f7-b5c924ed0d24"
BR = "5c92529c-3df8-4d9f-b290-a5c94fa51903"
WH_CODE = "YGY-MAIN"
CRITICAL = ("100463", "100464", "100488", "100489")
EXPECT_100032_BASELINE = 9000.0
SOURCE_FILE_NAME = "ATS_YGY_357_20261104.csv"
SOURCE_FILE_SHA256 = "31ec53244b8095044e336225681a17d654babe7c0303aa65aeadb9254ef6bfb3"
SNAPSHOT_VS_CUTOFF = "OWNER_STATED_FILE_EQUALS_CUTOFF_UNVERIFIED_BY_AGENT"

# Single source of truth for cutoff wall-clock (Owner).
CUTOFF_AT = "2026-11-04T22:45:00+07:00"

LIMIT_MASTERS = 2000
LIMIT_ORDERS = 8000
LIMIT_LEDGER = 30000

ACCURATE_LIVE_BRANCH_TOKENS = ("50", "YGY", BR)

# Ledger kinds → preview buckets (inline RO v1; not the undeployed PR#730 collector).
TAMBAH_KINDS = {
    "PURCHASE_RECEIPT",
    "RECEIVE_FREE",
    "RECEIVE",
    "RELEASE",
    "RELEASE_RESERVATION",
    "ADJUST_IN",
    "STOCK_IN",
}
KURANG_KINDS = {
    # SALES_DELIVERY is NOT here: free-stock baseline already nets open POs.
    # Pre-cutoff PO ship must not re-cut; post-cutoff ship is SHIP_RESERVED (effect 0)
    # after RESERVE. See runtime collect_ats_stream_for_ygy SKIP_OLD_PO_SHIP.
    "ISSUE_FREE",
    "ISSUE",
    "RESERVE",
    "ADJUST_OUT",
    "STOCK_OUT",
}


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cutoff_utc_z(cutoff_at: str = CUTOFF_AT) -> str:
    """Derive UTC Zulu from CUTOFF_AT. Fail closed if unparsable."""
    dt = _parse_dt(cutoff_at)
    if dt is None:
        raise ValueError(f"unparsable CUTOFF_AT={cutoff_at!r}")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Derived once — do not hardcode a second calendar date.
CUTOFF_UTC = cutoff_utc_z(CUTOFF_AT)


def assert_cutoff_pair_ok() -> None:
    """Self-check: CUTOFF_UTC must equal derivation from CUTOFF_AT."""
    derived = cutoff_utc_z(CUTOFF_AT)
    if CUTOFF_UTC != derived:
        raise RuntimeError(f"CUTOFF_UTC drift: const={CUTOFF_UTC} derived={derived}")
    # Explicit known-good for tonight's Owner cutoff.
    if CUTOFF_AT == "2026-11-04T22:45:00+07:00" and CUTOFF_UTC != "2026-11-04T15:45:00Z":
        raise RuntimeError(f"CUTOFF_UTC wrong for Owner cutoff: {CUTOFF_UTC}")


def txn_at_or_after_cutoff(occurred_at: Any, cutoff: Optional[datetime] = None) -> bool:
    """Transaction boundary: occurred_at >= cutoff (inclusive), timezone-aware."""
    c = cutoff or _parse_dt(CUTOFF_AT)
    assert c is not None
    dt = _parse_dt(occurred_at)
    if dt is None:
        return False
    return dt >= c


def txn_before_cutoff(occurred_at: Any, cutoff: Optional[datetime] = None) -> bool:
    c = cutoff or _parse_dt(CUTOFF_AT)
    assert c is not None
    dt = _parse_dt(occurred_at)
    if dt is None:
        return False
    return dt < c


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_wib() -> str:
    return datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%dT%H:%M:%S%z")


def _units_ok(a: str, b: str) -> bool:
    return str(a or "").strip().upper() == str(b or "").strip().upper()


def _qty(v: Any) -> float:
    if v in ("", None):
        return 0.0
    return float(Decimal(str(v)))


def _sha_rows(rows: List[dict]) -> str:
    payload = [
        {
            "kode": r["kode"],
            "satuan": r["satuan"],
            "accurate_ats": float(r["accurate_ats"]),
            "master_item_id": r.get("master_item_id") or "",
            "eligible_master": bool(r.get("eligible_master")),
        }
        for r in sorted(rows, key=lambda x: (x["kode"], x["satuan"]))
    ]
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


async def _fetch_capped(cursor, limit: int) -> Tuple[List[dict], bool]:
    docs = await cursor.to_list(limit)
    return docs, len(docs) >= limit


def _order_change_times(order: dict) -> List[datetime]:
    times: List[datetime] = []
    for key in (
        "created_at",
        "imported_at",
        "updated_at",
        "cancelled_at",
        "status_updated_at",
        "last_synced_at",
    ):
        dt = _parse_dt(order.get(key))
        if dt is not None:
            times.append(dt)
    return times


def _order_relevant_after_cutoff(order: dict, cutoff: datetime) -> bool:
    times = _order_change_times(order)
    if not times:
        return False
    created = _parse_dt(order.get("created_at") or order.get("imported_at"))
    if created is not None and created > cutoff:
        return True
    return any(t > cutoff for t in times)


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items() if k != "_id"}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def _ygy_alloc_qty(line: dict) -> Tuple[float, str]:
    """Return (qty, note) for YGY-MAIN reserve from a line (inline RO policy)."""
    unit = str(line.get("unit") or line.get("satuan") or "PCS")
    try:
        line_qty = float(Decimal(str(line.get("qty") or line.get("quantity") or 0)))
    except (InvalidOperation, TypeError, ValueError):
        line_qty = 0.0
    allocs = line.get("stock_allocations") if isinstance(line.get("stock_allocations"), list) else []
    if not allocs:
        # Owner 2026-11-03 default: empty alloc → full line to YGY-MAIN
        return line_qty, "EMPTY_ALLOC_DEFAULT_YGY"
    ygy = 0.0
    for a in allocs:
        if not isinstance(a, dict):
            continue
        if str(a.get("warehouse_id") or "").strip() != WH:
            continue
        try:
            ygy += float(Decimal(str(a.get("qty") or 0)))
        except (InvalidOperation, TypeError, ValueError):
            pass
    if ygy > 0:
        return ygy, "YGY_ALLOC"
    return 0.0, "NO_YGY_ALLOC"


async def main() -> int:
    assert_cutoff_pair_ok()
    started = _now()
    out = Path(os.environ["ETERNAL_RO_OUT_DIR"])
    env_file = Path(os.environ["ETERNAL_RO_ENV_FILE"])
    cand_path = Path(os.environ["ETERNAL_RO_CANDIDATES_JSON"])
    out.mkdir(parents=True, exist_ok=True)

    candidates = json.loads(cand_path.read_text(encoding="utf-8"))
    if len(candidates) != 357:
        print(f"STOP_CANDIDATE_COUNT got={len(candidates)} expect=357")
        return 2

    if not env_file.is_file():
        print(f"STOP_ENV missing {env_file}")
        return 2
    env = dotenv_values(str(env_file))
    uri = env.get("MONGO_URL") or env.get("MONGODB_URI") or env.get("DATABASE_URL")
    dbn = env.get("DB_NAME") or env.get("MONGODB_DB") or env.get("MONGO_DB") or "eternal_packaging"
    if not uri:
        print("STOP_MONGO_URL missing in env file")
        return 2
    uri_safe = re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", uri)

    cutoff = _parse_dt(CUTOFF_AT)
    assert cutoff is not None
    as_of = _now()

    client = AsyncIOMotorClient(uri, read_preference=ReadPreference.PRIMARY)
    db = client[dbn]

    codes = sorted({str(c["kode"]).strip() for c in candidates})
    code_set = set(codes)
    docs, trunc_masters = await _fetch_capped(
        db["master_items"].find(
            {"item_code": {"$in": codes}},
            {
                "_id": 0,
                "id": 1,
                "item_code": 1,
                "name": 1,
                "item_name": 1,
                "unit": 1,
                "active": 1,
                "is_active": 1,
                "item_type": 1,
            },
        ),
        LIMIT_MASTERS,
    )
    by_code: Dict[str, List[dict]] = {}
    for d in docs:
        by_code.setdefault(str(d.get("item_code") or "").strip(), []).append(d)

    matched_rows: List[dict] = []
    missing: List[dict] = []
    inactive: List[dict] = []
    mismatch: List[dict] = []
    match_report: List[dict] = []

    for c in candidates:
        kode = str(c["kode"]).strip()
        satuan = str(c.get("satuan") or "PCS").strip() or "PCS"
        nama = str(c.get("nama") or "").strip()
        try:
            qty = _qty(c.get("qty"))
        except Exception:
            mismatch.append({**c, "reason": "INVALID_QTY"})
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": nama, "qty": c.get("qty"),
                "status": "MISMATCH", "reason": "INVALID_QTY", "master_item_id": "",
            })
            continue
        hits = by_code.get(kode) or []
        if not hits:
            missing.append({**c, "reason": "NO_MASTER_ITEM"})
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": nama, "qty": qty,
                "status": "MISSING", "reason": "NO_MASTER_ITEM", "master_item_id": "",
            })
            continue
        unit_hits = [h for h in hits if _units_ok(satuan, h.get("unit") or "PCS")]
        if len(unit_hits) == 1:
            pick = unit_hits[0]
        elif len(hits) == 1 and not unit_hits:
            mismatch.append({
                **c, "reason": "UNIT_MISMATCH",
                "master_unit": hits[0].get("unit"),
                "master_item_id": hits[0].get("id"),
            })
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": nama, "qty": qty,
                "status": "MISMATCH", "reason": "UNIT_MISMATCH",
                "master_item_id": str(hits[0].get("id") or ""),
                "master_unit": hits[0].get("unit"),
            })
            continue
        elif len(unit_hits) > 1 or len(hits) > 1:
            mismatch.append({**c, "reason": "AMBIGUOUS_MASTER_OR_UNIT", "hits": len(hits)})
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": nama, "qty": qty,
                "status": "MISMATCH", "reason": "AMBIGUOUS_MASTER_OR_UNIT",
                "master_item_id": "", "hit_count": len(hits),
            })
            continue
        else:
            missing.append({**c, "reason": "NO_UNIT_MATCH"})
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": nama, "qty": qty,
                "status": "MISSING", "reason": "NO_UNIT_MATCH", "master_item_id": "",
            })
            continue

        mid = str(pick.get("id") or "").strip()
        if not mid:
            missing.append({**c, "reason": "MASTER_MISSING_ID"})
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": nama, "qty": qty,
                "status": "MISSING", "reason": "MASTER_MISSING_ID", "master_item_id": "",
            })
            continue
        if mid.startswith("synthetic-") or mid.startswith("pending-"):
            mismatch.append({**c, "reason": "SYNTHETIC_ID_REFUSED", "master_item_id": mid})
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": nama, "qty": qty,
                "status": "MISMATCH", "reason": "SYNTHETIC_ID_REFUSED",
                "master_item_id": mid,
            })
            continue

        active_flag = pick.get("active")
        if active_flag is None:
            active_flag = pick.get("is_active", True)
        item_type = str(pick.get("item_type") or "INVENTORY").upper()
        eligible = bool(active_flag) and item_type not in ("SERVICE", "NON_INVENTORY")
        row = {
            "kode": kode,
            "nama": nama or str(pick.get("name") or pick.get("item_name") or ""),
            "satuan": satuan,
            "accurate_ats": float(qty),
            "master_item_id": mid,
            "eligible_master": eligible,
        }
        if not eligible:
            inactive.append({**row, "reason": "INACTIVE_OR_NON_INVENTORY"})
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": row["nama"], "qty": qty,
                "status": "INACTIVE", "reason": "INACTIVE_OR_NON_INVENTORY",
                "master_item_id": mid,
            })
        else:
            match_report.append({
                "kode": kode, "satuan": satuan, "nama": row["nama"], "qty": qty,
                "status": "MATCHED", "reason": "LIVE_KODE_SATUAN_OK",
                "master_item_id": mid,
            })
        matched_rows.append(row)

    critical_status = {}
    for ck in CRITICAL:
        hits = [m for m in match_report if m["kode"] == ck]
        critical_status[ck] = hits[0] if hits else {"status": "MISSING", "reason": "NOT_IN_FILE"}

    doc_rows = list(matched_rows)
    unmatched_report = list(missing) + list(mismatch)
    source_sha = _sha_rows(doc_rows) if doc_rows else hashlib.sha256(b"empty").hexdigest()
    baseline_doc = {
        "warehouse_id": WH,
        "warehouse_code": WH_CODE,
        "cutoff_at": CUTOFF_AT,
        "cutoff_at_utc": CUTOFF_UTC,
        "source_date": "2026-11-04",
        "source_file_name": SOURCE_FILE_NAME,
        "source_file_sha256": SOURCE_FILE_SHA256,
        "source_sha256": source_sha,
        "source_meaning": (
            "free blank goods already net of open orders at cutoff; "
            "Owner Accurate Stok Dapat Dijual; do not re-subtract open POs"
        ),
        "baseline_semantics": "FREE_STOCK_AT_CUTOFF",
        "status": "RO_LIVE_MATCHED_CANDIDATE",
        "rows": doc_rows,
        "inactive_rows": inactive,
        "unmatched_rows": unmatched_report,
        "row_count": len(doc_rows),
        "eligible_count": sum(1 for r in doc_rows if r.get("eligible_master")),
        "pending_live_master_count": len(unmatched_report),
        "built_at": _now(),
        "preview_engine": "INLINE_RO_V1_NO_RUNTIME_IMPORT",
        "live_target_sha_hint": "9ff5a0cd",
        "install_gate": {
            "requires_live_ro_match": True,
            "requires_post_cutoff_preview": True,
            "do_not_activate_pointer_until_owner_ack": True,
            "critical_codes_must_have_real_master_ids": list(CRITICAL),
            "critical_all_matched": all(
                critical_status[c].get("status") == "MATCHED" for c in CRITICAL
            ),
        },
        "snapshot_vs_cutoff": {
            "status": SNAPSHOT_VS_CUTOFF,
            "owner_cutoff_at": CUTOFF_AT,
            "owner_cutoff_at_utc": CUTOFF_UTC,
            "note": (
                "Agent cannot independently verify Accurate export wall-clock "
                "against file bytes; Owner stated file @ cutoff. Mark uncertainty."
            ),
        },
    }
    (out / "MATCHED_BASELINE.json").write_text(
        json.dumps(baseline_doc, indent=2, ensure_ascii=False) + "\n"
    )

    live_mids = sorted({r["master_item_id"] for r in doc_rows if r.get("master_item_id")})
    mid_set = set(live_mids)
    mid_to_row = {r["master_item_id"]: r for r in doc_rows}

    # ---- Post-cutoff orders (created OR changed after cutoff) ----
    trunc_flags: Dict[str, bool] = {"masters": trunc_masters}
    branch_or = [
        {"branch_id": BR},
        {"cabang_id": BR},
        {"branch_id": {"$in": list(ACCURATE_LIVE_BRANCH_TOKENS)}},
        {"cabang_id": {"$in": list(ACCURATE_LIVE_BRANCH_TOKENS)}},
        {"accurate_branch_id": {"$in": list(ACCURATE_LIVE_BRANCH_TOKENS)}},
    ]
    item_or: List[dict] = [{"line_items.item_code": {"$in": codes}}]
    if live_mids:
        item_or.append({"line_items.master_item_id": {"$in": live_mids}})
    order_query = {"$and": [{"$or": branch_or}, {"$or": item_or}]}
    order_proj = {
        "_id": 0, "id": 1, "po_number": 1, "order_number": 1,
        "branch_id": 1, "cabang_id": 1, "accurate_branch_id": 1,
        "accurate_id": 1, "import_source": 1, "warehouse_id": 1,
        "cancelled": 1, "is_cancelled": 1, "cancelled_at": 1,
        "status": 1, "status_label": 1,
        "created_at": 1, "imported_at": 1, "updated_at": 1,
        "status_updated_at": 1, "last_synced_at": 1, "line_items": 1,
    }
    orders_raw, trunc_flags["orders_raw"] = await _fetch_capped(
        db["orders"].find(order_query, order_proj), LIMIT_ORDERS
    )
    orders_out: List[dict] = []
    # per-mid reserve qty from post-cutoff-relevant orders
    reserve_by_mid: Dict[str, float] = {m: 0.0 for m in live_mids}
    for o in orders_raw:
        if not _order_relevant_after_cutoff(o, cutoff):
            continue
        lines_kept = []
        for idx, li in enumerate(o.get("line_items") or []):
            if not isinstance(li, dict):
                continue
            ic = str(li.get("item_code") or "")
            mid = str(li.get("master_item_id") or "")
            if ic not in code_set and mid not in mid_set:
                continue
            lines_kept.append(li)
            if mid in mid_set:
                # Only apply RESERVE for orders CREATED after cutoff (free-stock baseline
                # already nets open POs at cutoff; pre-cutoff PO changes need proven keys).
                created = _parse_dt(o.get("created_at") or o.get("imported_at"))
                if created is not None and created > cutoff:
                    q, _note = _ygy_alloc_qty(li)
                    reserve_by_mid[mid] = reserve_by_mid.get(mid, 0.0) + q
        if not lines_kept:
            continue
        created = _parse_dt(o.get("created_at") or o.get("imported_at"))
        relevance = (
            "POST_CUTOFF_CREATED"
            if created is not None and created > cutoff
            else "PRE_CUTOFF_CHANGED_AFTER_CUTOFF"
        )
        row = dict(o)
        row["line_items"] = lines_kept
        row["_ro_relevance"] = relevance
        orders_out.append(_json_safe(row))

    order_by_id = {
        str(o.get("id") or ""): o
        for o in orders_raw
        if isinstance(o, dict) and str(o.get("id") or "").strip()
    }

    # ---- Ledger at/after cutoff ----
    ledger_query: Dict[str, Any] = {
        "warehouse_id": WH,
        "$or": [
            {"occurred_at": {"$gte": cutoff}},
            {"occurred_at": {"$gte": CUTOFF_UTC}},
            {"occurred_at": {"$gte": CUTOFF_AT}},
        ],
    }
    if live_mids:
        ledger_query = {"$and": [ledger_query, {"master_item_id": {"$in": live_mids}}]}
    ledger_proj = {
        "_id": 0, "id": 1, "master_item_id": 1, "branch_id": 1, "warehouse_id": 1,
        "movement_type": 1, "qty_delta": 1, "reference_type": 1, "reference_id": 1,
        "reason": 1, "occurred_at": 1, "created_at": 1, "idempotency_key": 1,
    }
    ledger_raw, trunc_flags["ledger"] = await _fetch_capped(
        db["physical_stock_ledger"].find(ledger_query, ledger_proj), LIMIT_LEDGER
    )
    # Defensive filter: keep only rows that parse >= cutoff
    ledger_kept = []
    ledger_tambah: Dict[str, float] = {m: 0.0 for m in live_mids}
    ledger_kurang: Dict[str, float] = {m: 0.0 for m in live_mids}
    unmapped_ledger: Dict[str, List[str]] = {m: [] for m in live_mids}
    boundary_excluded_before = 0
    for d in ledger_raw:
        if not txn_at_or_after_cutoff(d.get("occurred_at"), cutoff):
            boundary_excluded_before += 1
            continue
        ledger_kept.append(d)
        mid = str(d.get("master_item_id") or "")
        if mid not in mid_set:
            continue
        mtype = str(d.get("movement_type") or "").strip().upper()
        try:
            q = abs(float(Decimal(str(d.get("qty_delta") or 0))))
        except (InvalidOperation, TypeError, ValueError):
            q = 0.0
            unmapped_ledger[mid].append(f"BAD_QTY:{d.get('id')}")
            continue
        if mtype in TAMBAH_KINDS:
            ledger_tambah[mid] = ledger_tambah.get(mid, 0.0) + q
        elif mtype in KURANG_KINDS:
            ledger_kurang[mid] = ledger_kurang.get(mid, 0.0) + q
        elif mtype == "SALES_DELIVERY":
            # Align with PR#730 runtime: never ISSUE_FREE / re-cut free-stock baseline.
            # Pre-cutoff PO ship → skip. Post-cutoff reserved ship → effect 0.
            # Without order linkage → explicit gap (fail closed), not silent cut.
            meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            oid = str(meta.get("order_id") or "").strip()
            order = order_by_id.get(oid) if oid else None
            created = None
            if order is not None:
                created = _parse_dt(order.get("created_at") or order.get("imported_at"))
            if created is not None and created <= cutoff:
                # SKIP_OLD_PO_SHIP — baseline already netted this commitment
                continue
            if created is not None and created > cutoff:
                # Post-cutoff ship against reservation: effect 0 (RESERVE already counted)
                continue
            unmapped_ledger[mid].append(
                f"UNATTRIBUTED_OR_UNRESERVED_SHIP:{d.get('id')}:order={oid or '-'}"
            )
        elif mtype in ("SHIP_RESERVED", "REVERSAL"):
            # SHIP_RESERVED: effect 0 (already reserved). REVERSAL: gap unless paired.
            if mtype == "REVERSAL":
                unmapped_ledger[mid].append(f"REVERSAL_INLINE_UNMAPPED:{d.get('id')}")
        else:
            unmapped_ledger[mid].append(f"UNMAPPED_KIND:{mtype}:{d.get('id')}")

    trunc_flags["orders_exported"] = bool(trunc_flags.get("orders_raw"))
    any_trunc = any(bool(v) for v in trunc_flags.values())
    has_post = bool(orders_out or ledger_kept)
    if not live_mids:
        preview_class = "BASELINE_ONLY"
        class_note = "No matched masters — cannot compute live post-cutoff preview."
    elif has_post:
        preview_class = "POST_CUTOFF_INCLUDED"
        class_note = (
            "Live Mongo orders/ledger after cutoff included via INLINE_RO_V1 "
            "(no PR#730 runtime import). ATS akhir = baseline ± mapped effects. "
            "NOT claiming BASELINE_ONLY as current."
        )
    else:
        preview_class = "POST_CUTOFF_QUERY_EMPTY_NET"
        class_note = (
            "Live post-cutoff query ran but returned no orders/ledger rows for matched "
            "masters at inspection time. Distinct from local BASELINE_ONLY without RO."
        )

    preview_rows: List[dict] = []
    for c in candidates:
        kode = str(c["kode"]).strip()
        satuan = str(c.get("satuan") or "PCS").strip() or "PCS"
        nama = str(c.get("nama") or "").strip()
        try:
            qty = _qty(c.get("qty"))
        except Exception:
            qty = None
        mr = next((m for m in match_report if m["kode"] == kode and m["satuan"] == satuan), None)
        status_m = (mr or {}).get("status")
        if status_m == "MISSING":
            preview_rows.append({
                "kode": kode, "nama": nama, "satuan": satuan,
                "baseline": qty, "penambahan": None, "pengurangan": None,
                "ats_akhir": None, "status": "MISSING_MASTER",
                "gap": (mr or {}).get("reason") or "NO_MASTER",
                "master_item_id": "", "preview_class": preview_class,
                "post_cutoff_effect": False,
            })
            continue
        if status_m == "MISMATCH":
            preview_rows.append({
                "kode": kode, "nama": nama, "satuan": satuan,
                "baseline": qty, "penambahan": None, "pengurangan": None,
                "ats_akhir": None, "status": "MISMATCH",
                "gap": (mr or {}).get("reason") or "MISMATCH",
                "master_item_id": (mr or {}).get("master_item_id") or "",
                "preview_class": preview_class,
                "post_cutoff_effect": False,
            })
            continue
        mid = str((mr or {}).get("master_item_id") or "")
        base = float((mid_to_row.get(mid) or {}).get("accurate_ats") or qty or 0)
        pen = float(ledger_tambah.get(mid, 0.0))
        peng = float(ledger_kurang.get(mid, 0.0)) + float(reserve_by_mid.get(mid, 0.0))
        gaps = list(unmapped_ledger.get(mid) or [])
        if status_m == "INACTIVE":
            st = "INACTIVE"
            ats = None
        elif gaps:
            st = "UNAVAILABLE"
            ats = None
            gaps = gaps[:12]
        else:
            st = "READY"
            ats = base + pen - peng
        effect = (pen != 0.0) or (peng != 0.0)
        preview_rows.append({
            "kode": kode,
            "nama": nama or (mid_to_row.get(mid) or {}).get("nama") or "",
            "satuan": satuan,
            "baseline": base,
            "penambahan": pen,
            "pengurangan": peng,
            "ats_akhir": ats,
            "status": st,
            "gap": "; ".join(gaps) if gaps else "",
            "master_item_id": mid,
            "preview_class": preview_class,
            "post_cutoff_effect": effect,
        })

    # Consistency across preview table (list/detail/export share same inline projection)
    prev_100032 = next((r for r in preview_rows if r["kode"] == "100032"), None)
    consistency = {
        "checked": bool(prev_100032),
        "kode": "100032",
        "engine": "INLINE_RO_V1_NO_RUNTIME_IMPORT",
        "list_baseline": prev_100032.get("baseline") if prev_100032 else None,
        "detail_baseline": prev_100032.get("baseline") if prev_100032 else None,
        "export_baseline": prev_100032.get("baseline") if prev_100032 else None,
        "list_ats": prev_100032.get("ats_akhir") if prev_100032 else None,
        "detail_ats": prev_100032.get("ats_akhir") if prev_100032 else None,
        "export_ats": prev_100032.get("ats_akhir") if prev_100032 else None,
        "agree": bool(prev_100032),
        "baseline_9000_ok": bool(
            prev_100032 and float(prev_100032.get("baseline") or -1) == EXPECT_100032_BASELINE
        ),
        "note": (
            "On live 9ff5a0cd without PR#730 overlay, list/detail/export consistency is "
            "verified on the same INLINE projection rows (single source)."
        ),
    }
    sample_100032 = {
        "kode": "100032",
        "ats_baseline": prev_100032.get("baseline") if prev_100032 else None,
        "ats_penambahan": prev_100032.get("penambahan") if prev_100032 else None,
        "ats_pengurangan": prev_100032.get("pengurangan") if prev_100032 else None,
        "ats_sekarang": prev_100032.get("ats_akhir") if prev_100032 else None,
        "ats_cutover_status": prev_100032.get("status") if prev_100032 else None,
        "expect_baseline": EXPECT_100032_BASELINE,
        "baseline_ok": consistency.get("baseline_9000_ok"),
    }

    finished = _now()
    status_counts: Dict[str, int] = {}
    for r in preview_rows:
        status_counts[str(r.get("status") or "?")] = status_counts.get(str(r.get("status") or "?"), 0) + 1
    effect_codes = [r["kode"] for r in preview_rows if r.get("post_cutoff_effect")]
    install_blocked_reasons = []
    if not all(critical_status[c].get("status") == "MATCHED" for c in CRITICAL):
        install_blocked_reasons.append("CRITICAL_CODES_NEED_REAL_LIVE_MASTER_IDS")
    if missing:
        install_blocked_reasons.append(f"MISSING_MASTERS:{len(missing)}")
    if mismatch:
        install_blocked_reasons.append(f"MISMATCHES:{len(mismatch)}")
    if preview_class == "BASELINE_ONLY":
        install_blocked_reasons.append("PREVIEW_CLASS_BASELINE_ONLY_NOT_CURRENT")
    if SNAPSHOT_VS_CUTOFF.startswith("OWNER_STATED"):
        install_blocked_reasons.append("SNAPSHOT_VS_CUTOFF_UNVERIFIED")

    summary = {
        "export_kind": "ATS_YGY_357_LIVE_MATCH_POST_CUTOFF_PREVIEW_RO",
        "preview_engine": "INLINE_RO_V1_NO_RUNTIME_IMPORT",
        "self_contained_for_live_sha": "9ff5a0cd",
        "depends_on_pr730_runtime": False,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "started_at_wib": _now_wib(),
        "cutoff_at": CUTOFF_AT,
        "cutoff_at_utc": CUTOFF_UTC,
        "cutoff_utc_derived_from_cutoff_at": True,
        "as_of_utc": as_of,
        "warehouse_id": WH,
        "warehouse_code": WH_CODE,
        "candidate_count": 357,
        "matched_count": len([m for m in match_report if m["status"] == "MATCHED"]),
        "inactive_count": len(inactive),
        "missing_count": len(missing),
        "mismatch_count": len(mismatch),
        "critical_codes": critical_status,
        "preview_class": preview_class,
        "class_note": class_note,
        "activation_ready": False,
        "install_pointer_ready": False,
        "install_blocked_reasons": install_blocked_reasons,
        "status_counts": status_counts,
        "codes_with_post_cutoff_effect": effect_codes,
        "codes_with_post_cutoff_effect_count": len(effect_codes),
        "orders_exported": len(orders_out),
        "ledger_exported": len(ledger_kept),
        "ledger_raw_fetched": len(ledger_raw),
        "boundary_excluded_before_cutoff": boundary_excluded_before,
        "truncation": trunc_flags,
        "history_complete": not any_trunc,
        "list_detail_export_consistency": consistency,
        "sample_100032": sample_100032,
        "snapshot_vs_cutoff": baseline_doc.get("snapshot_vs_cutoff"),
        "source_file_sha256": SOURCE_FILE_SHA256,
        "matched_baseline_sha256": source_sha,
        "physical_write": False,
        "mode_env_mutated": False,
        "registry_mutated": False,
        "mongo_uri_redacted": uri_safe,
        "db_name": dbn,
        "env_file": str(env_file),
        "notes": [
            "READ-ONLY. No physical/ledger writes. No registry/mode mutation.",
            "Self-contained: no stock_ats runtime import (works on live 9ff5a0cd).",
            "CUTOFF_UTC derived from CUTOFF_AT; do not hardcode a second date.",
            "Do not treat BASELINE_ONLY as current saldo.",
            "Production ATS may already be activated — this script does not re-activate.",
        ],
        "identity": {
            "active_sha": os.environ.get("ETERNAL_RO_ACTIVE_SHA") or "",
            "current_link": os.environ.get("ETERNAL_RO_CURRENT_LINK") or "",
            "ats_mode_env_observed": os.environ.get("ETERNAL_RO_ATS_MODE") or "",
            "host": os.environ.get("ETERNAL_RO_HOST") or "",
            "user": os.environ.get("ETERNAL_RO_USER") or "",
            "db_name": dbn,
            "env_file": str(env_file),
        },
        "files": [
            "manifest.json",
            "MASTER_MATCH_REPORT.json",
            "MISSING.json",
            "INACTIVE.json",
            "MISMATCH.json",
            "MATCHED_BASELINE.json",
            "PREVIEW_ROWS.json",
            "PREVIEW_TABLE.tsv",
            "CRITICAL_FOUR.json",
            "orders.json",
            "physical_stock_ledger.json",
            "CANDIDATES_357.json",
        ],
    }

    (out / "manifest.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    (out / "MASTER_MATCH_REPORT.json").write_text(json.dumps(match_report, indent=2, ensure_ascii=False) + "\n")
    (out / "MISSING.json").write_text(json.dumps(missing, indent=2, ensure_ascii=False) + "\n")
    (out / "INACTIVE.json").write_text(json.dumps(inactive, indent=2, ensure_ascii=False) + "\n")
    (out / "MISMATCH.json").write_text(json.dumps(mismatch, indent=2, ensure_ascii=False) + "\n")
    (out / "PREVIEW_ROWS.json").write_text(json.dumps(preview_rows, indent=2, ensure_ascii=False) + "\n")
    (out / "CRITICAL_FOUR.json").write_text(json.dumps(critical_status, indent=2, ensure_ascii=False) + "\n")
    (out / "orders.json").write_text(json.dumps(orders_out, indent=2, ensure_ascii=False) + "\n")
    (out / "physical_stock_ledger.json").write_text(
        json.dumps([_json_safe(d) for d in ledger_kept], indent=2, ensure_ascii=False) + "\n"
    )

    fields = [
        "kode", "nama", "satuan", "baseline", "penambahan", "pengurangan",
        "ats_akhir", "status", "gap", "master_item_id", "preview_class",
        "post_cutoff_effect",
    ]
    with (out / "PREVIEW_TABLE.tsv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for row in preview_rows:
            w.writerow(row)

    print("RO_EXPORT_OK")
    print(f"STARTED={started}")
    print(f"FINISHED={finished}")
    print(f"CUTOFF_AT={CUTOFF_AT}")
    print(f"CUTOFF_UTC={CUTOFF_UTC}")
    print("CUTOFF_UTC_DERIVED=True")
    print(f"MATCHED={summary['matched_count']}")
    print(f"MISSING={summary['missing_count']}")
    print(f"INACTIVE={summary['inactive_count']}")
    print(f"MISMATCH={summary['mismatch_count']}")
    print(f"PREVIEW_CLASS={preview_class}")
    print(f"ORDERS={len(orders_out)}")
    print(f"LEDGER={len(ledger_kept)}")
    print(f"EFFECT_CODES={len(effect_codes)}")
    print(
        "CRITICAL_OK="
        + str(all(critical_status[c].get("status") == "MATCHED" for c in CRITICAL))
    )
    print(f"CONSISTENCY={json.dumps(consistency, ensure_ascii=False)}")
    print(f"INSTALL_BLOCKED={json.dumps(install_blocked_reasons, ensure_ascii=False)}")
    print("DEPENDS_ON_PR730_RUNTIME=False")
    print("ACTIVATION_READY=False")
    print("REGISTRY_MUTATED=False")
    print("PHYSICAL_WRITE=False")
    print(f"ENV_FILE={env_file}")
    print(f"OUT_DIR={out}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

__ETERNAL_ATS_YGY_357_PY_V1__

cp "$WORK_DIR/CANDIDATES_357.json" "$OUT_DIR/"
cp "$WORK_DIR/ro_ats_ygy_357_live_preview.py" "$OUT_DIR/"

{
  echo "CURRENT_LINK=$CURRENT_LINK"
  echo "ACTIVE_SHA=$ACTIVE_SHA"
  echo "ACTIVE_SHA_FILE=${ACTIVE_SHA_FILE:-}"
  echo "PREVIOUS_ID=${PREVIOUS_ID:-}"
  echo "ETERNAL_ATS_YGY_MODE_OBSERVED=$ATS_MODE"
  echo "HEALTH=$HEALTH_JSON"
  echo "CUTOFF_AT=2026-11-04T22:45:00+07:00"
  echo "CUTOFF_UTC_DERIVED_EXPECT=2026-11-04T15:45:00Z"
  echo "ENV_DECLARED=$ENV_DECLARED"
  echo "ENV_EFFECTIVE=$ENV_FILE"
  echo "WAREHOUSE=YGY-MAIN"
  echo "CRITICAL=100463,100464,100488,100489"
  echo "EXPECT_100032_BASELINE=9000"
  echo "PREVIEW_ENGINE=INLINE_RO_V1_NO_RUNTIME_IMPORT"
  echo "DEPENDS_ON_PR730_RUNTIME=False"
  echo "SNAPSHOT_VS_CUTOFF=OWNER_STATED_FILE_EQUALS_CUTOFF_UNVERIFIED_BY_AGENT"
  echo "NOTE=Production ATS may already be activated; this script does NOT re-activate or switch pointer."
} | tee "$OUT_DIR/live_identity.txt"

export ETERNAL_RO_ENV_FILE="$ENV_FILE"
export ETERNAL_RO_BACKEND="$BE"
export ETERNAL_RO_OUT_DIR="$OUT_DIR"
export ETERNAL_RO_ACTIVE_SHA="$ACTIVE_SHA"
export ETERNAL_RO_ACTIVE_SHA_FILE="${ACTIVE_SHA_FILE:-}"
export ETERNAL_RO_PREVIOUS_ID="${PREVIOUS_ID:-}"
export ETERNAL_RO_CURRENT_LINK="$CURRENT_LINK"
export ETERNAL_RO_ATS_MODE="$ATS_MODE"
export ETERNAL_RO_HEALTH_JSON="$HEALTH_JSON"
export ETERNAL_RO_CANDIDATES_JSON="$OUT_DIR/CANDIDATES_357.json"
export ETERNAL_RO_HOST="$HOST_NAME"
export ETERNAL_RO_USER="$RUN_USER"

cd "$BE"
"$PY" "$OUT_DIR/ro_ats_ygy_357_live_preview.py" | tee "$OUT_DIR/export_stdout.txt"

TGZ="${OUT_DIR}.tgz"
tar -C "$(dirname "$OUT_DIR")" -czf "$TGZ" "$(basename "$OUT_DIR")"
echo "TGZ=$TGZ"
ls -la "$OUT_DIR" "$TGZ"
echo "=== RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE_END utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "NOTE: READ-ONLY complete. No DB update / restart / deploy / activation / pointer switch."
echo "SCP_TO_DOWNLOADS:"
echo "scp eternaladmin@HOST:$TGZ ~/Downloads/"
