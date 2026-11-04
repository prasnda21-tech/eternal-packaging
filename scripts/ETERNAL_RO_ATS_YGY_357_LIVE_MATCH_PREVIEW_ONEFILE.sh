#!/usr/bin/env bash
# ETERNAL — READ-ONLY ONLY. No insert/update/delete/adjust/import/deploy/posting/restart/activation/pointer switch.
# One-file YGY-MAIN ATS 357 live master match + post-cutoff preview.
# Cutoff Owner: 2026-11-04T22:45:00+07:00 (UTC 2026-11-03T15:45:00Z)
# Critical codes (real live IDs only): 100463 100464 100488 100489
#
# Download + run on VPS (as eternaladmin; needs backend .venv + .env read):
#   scp scripts/ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh eternaladmin@HOST:/tmp/
#   ssh eternaladmin@HOST 'bash /tmp/ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh'
# Or paste via:
#   ssh eternaladmin@HOST 'bash -s' < ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh
set -euo pipefail

R="${ETERNAL_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
ENV_FILE="${ETERNAL_BACKEND_ENV:-/opt/eternal-packaging/backend/.env}"
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

test -r "$ENV_FILE" || { echo "STOP_ENV path=$ENV_FILE"; exit 1; }
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
"""READ-ONLY live match + post-cutoff ATS preview for YGY-MAIN 357 candidate.

- Matches ALL kode+satuan against live master_items (no writes).
- Critical codes 100463/100464/100488/100489 require REAL live IDs (refuse synthetic).
- Computes ATS preview from live orders/ledger AFTER cutoff (not BASELINE_ONLY-as-current).
- Does NOT mutate registry, ETERNAL_ATS_YGY_MODE, physical_stock_balances, or ledger.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CUTOFF_AT = "2026-11-04T22:45:00+07:00"
CUTOFF_UTC = "2026-11-03T15:45:00Z"
WH = "b3d0bf36-1140-4eac-b0f7-b5c924ed0d24"
WH_CODE = "YGY-MAIN"
BR = "5c92529c-3df8-4d9f-b290-a5c94fa51903"
CRITICAL = ("100463", "100464", "100488", "100489")
EXPECT_100032_BASELINE = 9000.0
SOURCE_FILE_NAME = "ATS_YGY_357_20261104.csv"
SOURCE_FILE_SHA256 = "31ec53244b8095044e336225681a17d654babe7c0303aa65aeadb9254ef6bfb3"
# Owner stated cutoff = file time; agent cannot independently prove Accurate export clock.
SNAPSHOT_VS_CUTOFF = "OWNER_STATED_FILE_EQUALS_CUTOFF_UNVERIFIED_BY_AGENT"


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


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


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


async def main() -> int:
    started = _now()
    out = Path(os.environ["ETERNAL_RO_OUT_DIR"])
    be = Path(os.environ["ETERNAL_RO_BACKEND"])
    env_file = Path(os.environ["ETERNAL_RO_ENV_FILE"])
    cand_path = Path(os.environ["ETERNAL_RO_CANDIDATES_JSON"])
    out.mkdir(parents=True, exist_ok=True)

    _load_dotenv(env_file)
    sys.path.insert(0, str(be))

    candidates = json.loads(cand_path.read_text(encoding="utf-8"))
    if len(candidates) != 357:
        print(f"STOP_CANDIDATE_COUNT got={len(candidates)} expect=357")
        return 2

    mongo_url = os.environ.get("MONGO_URL") or os.environ.get("MONGODB_URI")
    db_name = os.environ.get("DB_NAME") or "eternal_packaging"
    if not mongo_url:
        print("STOP_MONGO_URL missing in env")
        return 2

    from motor.motor_asyncio import AsyncIOMotorClient
    from services.stock_ats_baseline_store import (
        build_baseline_document,
        clear_baseline_cache,
        validate_baseline_manifest,
    )
    from services.stock_ats_ygy_runtime import (
        apply_ats_overlay_to_ssi_row,
        collect_ats_stream_for_ygy,
        project_master_ats,
    )
    from services.physical_stock import list_physical_stock_catalog

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    codes = sorted({str(c["kode"]).strip() for c in candidates})
    docs = await db.master_items.find(
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
    ).to_list(len(codes) * 5 + 50)
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
                **c,
                "reason": "UNIT_MISMATCH",
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
        # Refuse synthetic / non-uuid-looking fabricated ids (must be live DB id).
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

    # Build candidate baseline with REAL ids only (pending stay unmatched — no synthetic).
    eligible_for_doc = [r for r in matched_rows if r.get("eligible_master")]
    inactive_in_doc = [r for r in matched_rows if not r.get("eligible_master")]
    # Include inactive in rows as ineligible for EXCLUDED projection.
    doc_rows = list(matched_rows)
    unmatched_report = list(missing) + list(mismatch)

    # Manual baseline doc (may have fewer than 357 eligible).
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
        "open_po_policy": "ALREADY_NETTED_IN_FILE_DO_NOT_RE_SUBTRACT",
        "outside_file_policy": "DO_NOT_ZERO",
        "inactive_policy": "REPORT_SEPARATELY_EXCLUDED",
        "rows": doc_rows,
        "inactive_rows": inactive,
        "unmatched_rows": unmatched_report,
        "row_count": len(doc_rows),
        "eligible_count": len(eligible_for_doc),
        "pending_live_master_count": len(unmatched_report),
        "built_at": _now(),
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
            "note": (
                "Agent cannot independently verify Accurate export wall-clock "
                "against file bytes; Owner stated file @ cutoff. Mark uncertainty."
            ),
        },
    }
    # validate only if we have rows with required fields
    if doc_rows:
        try:
            baseline_doc = validate_baseline_manifest(baseline_doc)
        except Exception as exc:
            # keep unvalidated but record
            baseline_doc["validation_error"] = str(exc)

    matched_path = out / "MATCHED_BASELINE.json"
    matched_path.write_text(json.dumps(baseline_doc, indent=2, ensure_ascii=False) + "\n")

    # Process-local draft_read + baseline override — NEVER touch production env files.
    os.environ["ETERNAL_ATS_YGY_MODE"] = "draft_read"
    os.environ["ETERNAL_ATS_YGY_BASELINE_PATH"] = str(matched_path)
    clear_baseline_cache()

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mids = [r["master_item_id"] for r in doc_rows if r.get("master_item_id")]
    stream = {"by_master": {}, "evidence_complete": False, "skipped": []}
    preview_class = "BASELINE_ONLY"
    class_note = "No matched masters — cannot compute live post-cutoff preview."
    if mids:
        stream = await collect_ats_stream_for_ygy(
            db,
            master_item_ids=mids,
            as_of=as_of,
            warehouse_id=WH,
            branch_id=BR,
        )
        # Detect whether collector saw any post-cutoff effect material
        any_events = False
        for mid, pack in (stream.get("by_master") or {}).items():
            if pack.get("events") or pack.get("gaps"):
                any_events = True
                break
        if any_events:
            preview_class = "POST_CUTOFF_INCLUDED"
            class_note = (
                "Live Mongo orders/ledger after cutoff included. "
                "ATS akhir = baseline ± mapped events. NOT claiming BASELINE_ONLY as current."
            )
        else:
            preview_class = "POST_CUTOFF_QUERY_EMPTY_NET"
            class_note = (
                "Live collector ran against post-cutoff window but no net mapped events "
                "for matched masters at inspection time. Distinct from local BASELINE_ONLY "
                "without RO; still not auto install-ready."
            )

    # Project every file code
    preview_rows: List[dict] = []
    mid_to_row = {r["master_item_id"]: r for r in doc_rows}
    by_master = stream.get("by_master") or {}
    global_skipped = list(stream.get("skipped") or [])

    for c in candidates:
        kode = str(c["kode"]).strip()
        satuan = str(c.get("satuan") or "PCS").strip() or "PCS"
        nama = str(c.get("nama") or "").strip()
        qty = None
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
        if mid not in by_master:
            pack = {
                "reservations_at_cutoff": [],
                "events": [],
                "gaps": ["NO_STREAM_PACK"],
                "evidence_complete": False,
            }
        else:
            pack = by_master[mid]
        evidence = bool(pack.get("evidence_complete")) and not pack.get("gaps")
        # Prefer stream evidence_complete flag when present
        if "evidence_complete" in pack:
            evidence = bool(pack.get("evidence_complete")) and not list(pack.get("gaps") or [])
        result = project_master_ats(
            master_item_id=mid,
            as_of=as_of,
            reservations_at_cutoff=pack.get("reservations_at_cutoff") or (),
            events=pack.get("events") or (),
            evidence_complete=evidence,
        )
        pen = result.get("penambahan")
        peng = result.get("pengurangan")
        effect = False
        try:
            effect = float(pen or 0) != 0.0 or float(peng or 0) != 0.0
        except (TypeError, ValueError):
            effect = False
        gaps = list(pack.get("gaps") or [])
        if result.get("status") == "UNAVAILABLE":
            gaps.append(result.get("error") or result.get("reason") or "COVERAGE_GAP")
        if status_m == "INACTIVE":
            st = "INACTIVE"
        else:
            st = result.get("status") or "UNKNOWN"
        preview_rows.append({
            "kode": kode,
            "nama": nama or (mid_to_row.get(mid) or {}).get("nama") or "",
            "satuan": satuan,
            "baseline": result.get("baseline_qty") if result.get("baseline_qty") is not None else qty,
            "penambahan": pen,
            "pengurangan": peng,
            "ats_akhir": result.get("available_qty"),
            "status": st,
            "gap": "; ".join(gaps) if gaps else "",
            "master_item_id": mid,
            "preview_class": preview_class,
            "post_cutoff_effect": effect,
            "reason": result.get("reason"),
        })

    # list/detail/export consistency via catalog (process-local draft_read + override)
    consistency = {"checked": False}
    sample_100032 = None
    try:
        cat = await list_physical_stock_catalog(
            db,
            branch_id=BR,
            warehouse_id=WH,
            q="100032",
            page=1,
            limit=20,
        )
        items = cat.get("items") or []
        hit = next((i for i in items if str(i.get("item_code")) == "100032"), None)
        if hit:
            apply_ats_overlay_to_ssi_row  # noqa: ensure import used
            # overlay already applied inside list when mode enabled
            sample_100032 = {
                "kode": "100032",
                "ats_baseline": hit.get("ats_baseline"),
                "ats_penambahan": hit.get("ats_penambahan"),
                "ats_pengurangan": hit.get("ats_pengurangan"),
                "ats_sekarang": hit.get("ats_sekarang"),
                "ats_cutover_status": hit.get("ats_cutover_status"),
                "expect_baseline": EXPECT_100032_BASELINE,
                "baseline_ok": float(hit.get("ats_baseline") or -1) == EXPECT_100032_BASELINE,
            }
            # detail-style re-fetch
            cat2 = await list_physical_stock_catalog(
                db, branch_id=BR, warehouse_id=WH, q="100032", page=1, limit=20,
            )
            hit2 = next((i for i in (cat2.get("items") or []) if str(i.get("item_code")) == "100032"), None)
            # export-style: same overlay fields
            prev_row = next((r for r in preview_rows if r["kode"] == "100032"), None)
            consistency = {
                "checked": True,
                "kode": "100032",
                "list_baseline": hit.get("ats_baseline"),
                "detail_baseline": hit2.get("ats_baseline") if hit2 else None,
                "export_baseline": prev_row.get("baseline") if prev_row else None,
                "list_ats": hit.get("ats_sekarang"),
                "detail_ats": hit2.get("ats_sekarang") if hit2 else None,
                "export_ats": prev_row.get("ats_akhir") if prev_row else None,
                "agree": bool(
                    hit2
                    and prev_row
                    and hit.get("ats_baseline") == hit2.get("ats_baseline") == prev_row.get("baseline")
                    and hit.get("ats_sekarang") == hit2.get("ats_sekarang") == prev_row.get("ats_akhir")
                    and hit.get("ats_cutover_status") == hit2.get("ats_cutover_status") == prev_row.get("status")
                ),
                "baseline_9000_ok": float(hit.get("ats_baseline") or -1) == EXPECT_100032_BASELINE,
            }
    except Exception as exc:
        consistency = {"checked": False, "error": str(exc)}

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
        "started_at_utc": started,
        "finished_at_utc": finished,
        "started_at_wib": _now_wib(),
        "cutoff_at": CUTOFF_AT,
        "cutoff_at_utc": CUTOFF_UTC,
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
        "list_detail_export_consistency": consistency,
        "sample_100032": sample_100032,
        "snapshot_vs_cutoff": baseline_doc.get("snapshot_vs_cutoff"),
        "source_file_sha256": SOURCE_FILE_SHA256,
        "matched_baseline_sha256": source_sha,
        "physical_write": False,
        "mode_env_mutated": False,
        "registry_mutated": False,
        "notes": [
            "READ-ONLY. No physical/ledger writes. No registry/mode mutation.",
            "Do not treat BASELINE_ONLY as current saldo.",
            "Production ATS may already be activated — this script does not re-activate.",
            "Critical four codes must resolve to live IDs before pointer install.",
        ],
        "identity": {
            "active_sha": os.environ.get("ETERNAL_RO_ACTIVE_SHA") or "",
            "current_link": os.environ.get("ETERNAL_RO_CURRENT_LINK") or "",
            "ats_mode_env_observed": os.environ.get("ETERNAL_RO_ATS_MODE") or "",
            "host": os.environ.get("ETERNAL_RO_HOST") or "",
            "user": os.environ.get("ETERNAL_RO_USER") or "",
            "db_name": db_name,
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
    print(f"MATCHED={summary['matched_count']}")
    print(f"MISSING={summary['missing_count']}")
    print(f"INACTIVE={summary['inactive_count']}")
    print(f"MISMATCH={summary['mismatch_count']}")
    print(f"PREVIEW_CLASS={preview_class}")
    print(f"EFFECT_CODES={len(effect_codes)}")
    print(f"CRITICAL_OK={summary['install_gate'] if False else all(critical_status[c].get('status')=='MATCHED' for c in CRITICAL)}")
    print(f"CONSISTENCY={json.dumps(consistency, ensure_ascii=False)}")
    print(f"INSTALL_BLOCKED={json.dumps(install_blocked_reasons, ensure_ascii=False)}")
    print("ACTIVATION_READY=False")
    print("REGISTRY_MUTATED=False")
    print("PHYSICAL_WRITE=False")
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
  echo "CUTOFF=2026-11-04T22:45:00+07:00"
  echo "WAREHOUSE=YGY-MAIN"
  echo "CRITICAL=100463,100464,100488,100489"
  echo "EXPECT_100032_BASELINE=9000"
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
