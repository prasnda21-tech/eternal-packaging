#!/usr/bin/env bash
# ETERNAL — READ-ONLY ONLY. No insert/update/delete/adjust/import/deploy/posting/restart/activation.
# One-file YGY-MAIN ATS post-cutoff RO export (embeds Python helper + CANDIDATES_162.json).
# Cutoff Owner: 2026-11-02T23:22:00+07:00 (UTC 2026-11-02T16:22:00Z)
# Run from Mac Terminal:
#   ssh eternaladmin@103.126.116.122 'bash -s' <<'ETERNAL_RO'
#   ...paste this whole file...
#   ETERNAL_RO
set -euo pipefail

R="${ETERNAL_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
ENV_FILE="${ETERNAL_BACKEND_ENV:-/opt/eternal-packaging/backend/.env}"
WORK_DIR="${ETERNAL_RO_WORK_DIR:-/tmp/eternal_ro_ats_ygy_post_cutoff_pkg_$$}"
OUT_DIR="${ETERNAL_RO_OUT_DIR:-/tmp/eternal_ro_ats_ygy_post_cutoff_$(date -u +%Y%m%dT%H%M%SZ)}"
HOST_NAME="$(hostname 2>/dev/null || echo unknown)"
RUN_USER="$(id -un 2>/dev/null || echo unknown)"

echo "=== RO_ATS_YGY_POST_CUTOFF_ONEFILE_BEGIN utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) host=$HOST_NAME user=$RUN_USER ==="
echo "WORK_DIR=$WORK_DIR"
echo "OUT_DIR=$OUT_DIR"

mkdir -p "$WORK_DIR" "$OUT_DIR"
cleanup_work() {
  rm -rf "$WORK_DIR"
}
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
  if [[ -z "${ATS_MODE:-}" ]]; then
    ATS_MODE="UNSET"
  fi
fi

HEALTH_JSON=""
if command -v curl >/dev/null 2>&1; then
  HEALTH_JSON="$(curl -fsS --max-time 8 http://127.0.0.1:8000/api/system/health 2>/dev/null || echo HEALTH_CURL_FAIL)"
fi

cat > "$WORK_DIR/CANDIDATES_162.json" <<'__ETERNAL_ATS_YGY_RO_JSON_V1__'
[
  {
    "kode": "100272",
    "nama": "10 oz Oval 8 gr BSM",
    "satuan": "PCS",
    "accurate_ats": 15900,
    "master_item_id": "29098e4b-527a-47a6-bbc2-b0647ea4d87c",
    "eligible_master": true
  },
  {
    "kode": "100089",
    "nama": "12 oz Datar 5 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "85ead3a7-c464-44fb-a739-2e34ea12524a",
    "eligible_master": true
  },
  {
    "kode": "100385",
    "nama": "12 oz Datar 7 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 150,
    "master_item_id": "13b20e4f-fd16-4267-b7ec-a869a645f3ef",
    "eligible_master": true
  },
  {
    "kode": "100032",
    "nama": "12 oz Datar 7 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 20000,
    "master_item_id": "a88fb167-cf11-4d1f-8862-25dae1723053",
    "eligible_master": true
  },
  {
    "kode": "100001",
    "nama": "12 oz Datar 8 gr SA",
    "satuan": "PCS",
    "accurate_ats": 9000,
    "master_item_id": "958bb5e9-1090-4676-9999-eff93ff0bd5b",
    "eligible_master": true
  },
  {
    "kode": "100263",
    "nama": "12 oz Datar RJM",
    "satuan": "PCS",
    "accurate_ats": 1900,
    "master_item_id": "7d8517cf-db26-4f09-bb58-6e699f42ba02",
    "eligible_master": true
  },
  {
    "kode": "100147",
    "nama": "12 oz Inject Bening Wingoh",
    "satuan": "PCS",
    "accurate_ats": 8700,
    "master_item_id": "f67753a5-f0cf-4c08-9607-373315aeeb10",
    "eligible_master": true
  },
  {
    "kode": "100397",
    "nama": "12 oz Inject Datar Bening Klir",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "8b35ac50-72f8-4261-a9c2-b6473b191234",
    "eligible_master": true
  },
  {
    "kode": "100321",
    "nama": "12 oz Inject Datar Bening Victory",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "f9974506-3bd1-4de2-bc7e-a1296f6b3acc",
    "eligible_master": true
  },
  {
    "kode": "100434",
    "nama": "12 oz Inject Datar Frosted Hitam Merak",
    "satuan": "PCS",
    "accurate_ats": 3950,
    "master_item_id": "079326e1-5a15-461e-b16c-a66e07f95cb5",
    "eligible_master": true
  },
  {
    "kode": "100388",
    "nama": "12 oz Inject Datar Frosted Hitam Wingoh",
    "satuan": "PCS",
    "accurate_ats": 3500,
    "master_item_id": "b221bbe4-aafd-4cdd-abd1-f7ad5d0d99b9",
    "eligible_master": true
  },
  {
    "kode": "100432",
    "nama": "12 oz Inject Datar Frosted Klir",
    "satuan": "PCS",
    "accurate_ats": 2000,
    "master_item_id": "ed7a867d-54aa-429c-8fd0-3900d25feef6",
    "eligible_master": true
  },
  {
    "kode": "100331",
    "nama": "12 oz Inject Datar Frosted Victory",
    "satuan": "PCS",
    "accurate_ats": 5000,
    "master_item_id": "590fa08b-517d-42ab-819f-fb1325d317ba",
    "eligible_master": true
  },
  {
    "kode": "100259",
    "nama": "12 oz Inject Datar Frosted Wingoh",
    "satuan": "PCS",
    "accurate_ats": 13000,
    "master_item_id": "b6a55188-b06c-4566-be78-221784756959",
    "eligible_master": true
  },
  {
    "kode": "100374",
    "nama": "12 oz Inject Oval Bening Victory",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "bb2c0a33-aa0a-475f-99e1-c84a615056d0",
    "eligible_master": true
  },
  {
    "kode": "100405",
    "nama": "12 oz Oval 7 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 100,
    "master_item_id": "cd99b8ed-0945-4e00-8b5c-053cd7423ed3",
    "eligible_master": true
  },
  {
    "kode": "100031",
    "nama": "12 oz Oval 7 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 16600,
    "master_item_id": "7ecd01c3-a75f-4450-b37e-40873170bab3",
    "eligible_master": true
  },
  {
    "kode": "100049",
    "nama": "12 oz Oval 8 gr SA",
    "satuan": "PCS",
    "accurate_ats": 2600,
    "master_item_id": "223df450-1fc7-48b0-8516-607bb5c16aa7",
    "eligible_master": true
  },
  {
    "kode": "100056",
    "nama": "12 oz Oval Inject Bening",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "ed774962-6c19-43a8-b75b-45623ea0a0bb",
    "eligible_master": false
  },
  {
    "kode": "100341",
    "nama": "12 oz Oval Inject Bening Klir",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "80ec240c-f67e-4988-b6cf-155c8dc3cb66",
    "eligible_master": true
  },
  {
    "kode": "100359",
    "nama": "12 oz Oval Inject Bening Super",
    "satuan": "PCS",
    "accurate_ats": 4500,
    "master_item_id": "5d42d005-7500-4caf-bfd7-65be603507fb",
    "eligible_master": true
  },
  {
    "kode": "100170",
    "nama": "12 oz Oval PET PLP",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "5f3ea463-7d0e-4f70-a992-34da505c721a",
    "eligible_master": true
  },
  {
    "kode": "100292",
    "nama": "12 oz Oval RYC TAM",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "a304cf8d-3ab1-41bf-a242-43d070dcfa5a",
    "eligible_master": true
  },
  {
    "kode": "100169",
    "nama": "12 oz PET Datar 11 gr Printing",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "6d76bcb8-3559-43f2-9c83-39ae413fb082",
    "eligible_master": true
  },
  {
    "kode": "100003",
    "nama": "12 oz PET Datar 11 gr SJP",
    "satuan": "PCS",
    "accurate_ats": 13200,
    "master_item_id": "511e1d5d-90b1-4af8-b482-5b90abaada76",
    "eligible_master": true
  },
  {
    "kode": "100146",
    "nama": "12 oz PET Datar 11 gr TAM",
    "satuan": "PCS",
    "accurate_ats": 145000,
    "master_item_id": "023fd328-480b-40d6-a7bf-c96ce0b323b9",
    "eligible_master": true
  },
  {
    "kode": "100083",
    "nama": "12 oz Papper Cup Cold Putih Starindo",
    "satuan": "PCS",
    "accurate_ats": 400,
    "master_item_id": "df434c14-b767-495a-bbed-97dbfb9e14e1",
    "eligible_master": true
  },
  {
    "kode": "100194",
    "nama": "12 oz Papper Cup Hot Putih BSM",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "1d9e948f-1c3e-47da-b238-3b407a202ec5",
    "eligible_master": true
  },
  {
    "kode": "100080",
    "nama": "14 oz Datar 5 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 7000,
    "master_item_id": "d266657c-6d13-496f-b4cd-b1b93dfe6481",
    "eligible_master": true
  },
  {
    "kode": "100406",
    "nama": "14 oz Datar 7 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 97,
    "master_item_id": "2df66778-08ac-43ab-ac07-86a61dc51352",
    "eligible_master": true
  },
  {
    "kode": "100015",
    "nama": "14 oz Datar 7 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 8500,
    "master_item_id": "8ba0e4ee-3f9f-494f-8b36-e760af312b1d",
    "eligible_master": true
  },
  {
    "kode": "100209",
    "nama": "14 oz Datar Big 7 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 3500,
    "master_item_id": "a2cb72a5-a2d3-4b0a-bd66-e14833add673",
    "eligible_master": true
  },
  {
    "kode": "100217",
    "nama": "14 oz Inject Datar Bening Klir",
    "satuan": "PCS",
    "accurate_ats": 8500,
    "master_item_id": "0ee1140e-7ed5-4e0c-b456-fe3b8e107480",
    "eligible_master": true
  },
  {
    "kode": "100241",
    "nama": "14 oz Inject Datar Bening Super",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "96a6d1bf-903e-4111-a079-9eb732296b2b",
    "eligible_master": true
  },
  {
    "kode": "100061",
    "nama": "14 oz Inject Datar Bening Victory",
    "satuan": "PCS",
    "accurate_ats": 5000,
    "master_item_id": "5227f6fe-17c3-4e17-a416-2704fc960f19",
    "eligible_master": true
  },
  {
    "kode": "100293",
    "nama": "14 oz Inject Datar Frosted Hitam Wingoh",
    "satuan": "PCS",
    "accurate_ats": 9475,
    "master_item_id": "a9940988-c5fd-4126-939c-8a7fba4b965f",
    "eligible_master": true
  },
  {
    "kode": "100233",
    "nama": "14 oz Inject Datar Frosted Klir",
    "satuan": "PCS",
    "accurate_ats": 7000,
    "master_item_id": "af76caad-c07b-451e-bf85-49fa71125197",
    "eligible_master": true
  },
  {
    "kode": "100319",
    "nama": "14 oz Inject Datar Frosted Super",
    "satuan": "PCS",
    "accurate_ats": 15000,
    "master_item_id": "059d2c58-b954-4ee1-bdca-e6e7ede19c8d",
    "eligible_master": true
  },
  {
    "kode": "100137",
    "nama": "14 oz Inject Datar Frosted Victory",
    "satuan": "PCS",
    "accurate_ats": 8500,
    "master_item_id": "b33b7ed3-7071-4ae5-abc9-75dadbcc57d1",
    "eligible_master": true
  },
  {
    "kode": "100332",
    "nama": "14 oz Inject Datar Frosted Wingoh",
    "satuan": "PCS",
    "accurate_ats": 8000,
    "master_item_id": "095b16a6-ddfa-4cf2-b5a4-88e83c8b3117",
    "eligible_master": true
  },
  {
    "kode": "100407",
    "nama": "14 oz Oval 7 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 100,
    "master_item_id": "bd034824-e144-4efb-9651-7d5a5b300a03",
    "eligible_master": true
  },
  {
    "kode": "100010",
    "nama": "14 oz Oval 8 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 3650,
    "master_item_id": "9d55eb0f-d235-448a-851f-ba36df6f9ef4",
    "eligible_master": true
  },
  {
    "kode": "100006",
    "nama": "14 oz PET Datar 11 gr PLP",
    "satuan": "PCS",
    "accurate_ats": 21500,
    "master_item_id": "a1a33821-2fed-440c-9c14-e5f1cc37659b",
    "eligible_master": true
  },
  {
    "kode": "100019",
    "nama": "14 oz PET Oval 11 gr SJP",
    "satuan": "PCS",
    "accurate_ats": 2000,
    "master_item_id": "86d3f23e-7040-421e-a359-bdccf0057f17",
    "eligible_master": true
  },
  {
    "kode": "100033",
    "nama": "16 oz Datar 5 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 12000,
    "master_item_id": "42c2e038-894c-4bde-ad12-7aacb5289944",
    "eligible_master": true
  },
  {
    "kode": "100386",
    "nama": "16 oz Datar 7 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 47,
    "master_item_id": "7493e77e-2cc7-426b-84b8-f11419f0e026",
    "eligible_master": true
  },
  {
    "kode": "100016",
    "nama": "16 oz Datar 7 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 3100,
    "master_item_id": "abaaf1c2-3e93-4d58-8974-d1dcad6f40df",
    "eligible_master": true
  },
  {
    "kode": "100408",
    "nama": "16 oz Datar 8 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 50,
    "master_item_id": "38d37a57-351d-457d-ba36-da4d5fed88d1",
    "eligible_master": true
  },
  {
    "kode": "100002",
    "nama": "16 oz Datar 8 gr SA",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "e593be28-d989-42e7-b250-164da98672f3",
    "eligible_master": true
  },
  {
    "kode": "100226",
    "nama": "16 oz Inject Datar Bening Klir",
    "satuan": "PCS",
    "accurate_ats": 7650,
    "master_item_id": "4b754819-0aab-4dbd-b014-7aca298ae68d",
    "eligible_master": true
  },
  {
    "kode": "100242",
    "nama": "16 oz Inject Datar Bening Super",
    "satuan": "PCS",
    "accurate_ats": 6000,
    "master_item_id": "1438d12a-cf4f-4ffb-84ae-0e7d7adcbbc5",
    "eligible_master": true
  },
  {
    "kode": "100076",
    "nama": "16 oz Inject Datar Bening Victory",
    "satuan": "PCS",
    "accurate_ats": 4000,
    "master_item_id": "132d1a9c-8e00-4027-b752-d99923e6aa2d",
    "eligible_master": true
  },
  {
    "kode": "100433",
    "nama": "16 oz Inject Datar Frosted Klir",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "584f9050-b45d-4f1f-b021-917faab9ba68",
    "eligible_master": true
  },
  {
    "kode": "100333",
    "nama": "16 oz Inject Datar Frosted Wingoh",
    "satuan": "PCS",
    "accurate_ats": 10250,
    "master_item_id": "77433bed-955a-44f3-8635-2dee7ad50931",
    "eligible_master": true
  },
  {
    "kode": "100412",
    "nama": "16 oz Oval 8 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 50,
    "master_item_id": "3893f412-6601-4039-9bdb-e8fa47c581a5",
    "eligible_master": true
  },
  {
    "kode": "100050",
    "nama": "16 oz Oval 8 gr SA",
    "satuan": "PCS",
    "accurate_ats": 1500,
    "master_item_id": "7195b72a-ca16-41af-b6b1-4e6f68bd5940",
    "eligible_master": true
  },
  {
    "kode": "100011",
    "nama": "16 oz Oval 8 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 3000,
    "master_item_id": "41caeb16-1d09-45d8-8fec-df9ec0fbc0e0",
    "eligible_master": true
  },
  {
    "kode": "100004",
    "nama": "16 oz PET Datar 11 gr SJP",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "0105d7b1-9780-4052-bfa7-a08bc0f3f05b",
    "eligible_master": true
  },
  {
    "kode": "100150",
    "nama": "16 oz PET Datar 11 gr TAM",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "5e62ef5e-671d-4bfc-945a-9a1575cebbed",
    "eligible_master": true
  },
  {
    "kode": "100107",
    "nama": "18 oz Datar 7 gr Merak",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "d2a92878-a456-457c-a18e-7ea553778ae3",
    "eligible_master": true
  },
  {
    "kode": "100040",
    "nama": "18 oz Datar 8 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 2500,
    "master_item_id": "a8338023-4728-4df8-9edc-57e081ef6c7b",
    "eligible_master": true
  },
  {
    "kode": "100409",
    "nama": "18 oz Datar Slim 9 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 50,
    "master_item_id": "163baca7-3437-4318-98f4-40604dda4f87",
    "eligible_master": true
  },
  {
    "kode": "100427",
    "nama": "22 oz Datar 7 gr Berlian",
    "satuan": "PCS",
    "accurate_ats": 5500,
    "master_item_id": "1f1f5123-bde5-43e6-9367-44e7436b6dce",
    "eligible_master": true
  },
  {
    "kode": "100422",
    "nama": "22 oz Datar 8 gr Berlian",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "0bf88845-2dd1-4302-8583-b9d9ef5b5aff",
    "eligible_master": true
  },
  {
    "kode": "100140",
    "nama": "22 oz Datar 8 gr Merak",
    "satuan": "PCS",
    "accurate_ats": 14000,
    "master_item_id": "28d66f74-ba1f-4142-a1d7-3c6832ad282d",
    "eligible_master": true
  },
  {
    "kode": "100086",
    "nama": "22 oz Datar 8 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 5000,
    "master_item_id": "77aa8cbb-57dc-4be3-afc9-a10ce1ef041d",
    "eligible_master": true
  },
  {
    "kode": "100149",
    "nama": "22 oz Datar 8 gr Unggul",
    "satuan": "PCS",
    "accurate_ats": 2000,
    "master_item_id": "44258a7f-5f27-4543-8875-3564a1bee060",
    "eligible_master": true
  },
  {
    "kode": "100410",
    "nama": "22 oz Datar 9 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 50,
    "master_item_id": "5b656809-cc0b-435e-8c05-b7bb41c9fff5",
    "eligible_master": true
  },
  {
    "kode": "100047",
    "nama": "22 oz Datar 9,5 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 950,
    "master_item_id": "831d3632-f1be-45f1-9125-ade9f8146fa0",
    "eligible_master": true
  },
  {
    "kode": "100153",
    "nama": "22 oz Datar Rib 8 gr TAM",
    "satuan": "PCS",
    "accurate_ats": 129000,
    "master_item_id": "cd807b26-f23e-49b6-aecd-9d469df06131",
    "eligible_master": true
  },
  {
    "kode": "100112",
    "nama": "22 oz Oval 8 gr Merak",
    "satuan": "PCS",
    "accurate_ats": 3000,
    "master_item_id": "3e4d175e-3178-4bdb-af5e-1ce759321afd",
    "eligible_master": true
  },
  {
    "kode": "100411",
    "nama": "22 oz Oval 9 gr Polycup",
    "satuan": "PCS",
    "accurate_ats": 50,
    "master_item_id": "7322f82d-a4bb-4f57-b46d-910f0471fc96",
    "eligible_master": true
  },
  {
    "kode": "100073",
    "nama": "22 oz Oval 9.5 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "d09fe0e3-d7e5-47d4-9282-200ee7101500",
    "eligible_master": true
  },
  {
    "kode": "100181",
    "nama": "22 oz Papper Cup Cold Putih Starindo",
    "satuan": "PCS",
    "accurate_ats": 900,
    "master_item_id": "2fba16bf-91a3-40d7-9209-df03be40aeb6",
    "eligible_master": true
  },
  {
    "kode": "100228",
    "nama": "24 oz Inject Datar Bening KLIR",
    "satuan": "PCS",
    "accurate_ats": 2500,
    "master_item_id": "7c320dec-7e08-4371-a39b-a8db49668f98",
    "eligible_master": true
  },
  {
    "kode": "100098",
    "nama": "8 oz PET 6.2 gr Starindo",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "1dcedb1a-561f-4b67-a344-349fc2dd7240",
    "eligible_master": true
  },
  {
    "kode": "100163",
    "nama": "8 oz PET Cup Pudding PLP",
    "satuan": "PCS",
    "accurate_ats": 3000,
    "master_item_id": "e664da3d-3e85-4dc2-8d67-a41c9c346a9d",
    "eligible_master": true
  },
  {
    "kode": "100392",
    "nama": "Bowl 650 ml PP Datar Putih + LID Klir",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "7055768d-bde0-4bde-b15b-5304fb643bc6",
    "eligible_master": true
  },
  {
    "kode": "100183",
    "nama": "Box Pizza 30 x 30 cm",
    "satuan": "PCS",
    "accurate_ats": 1498,
    "master_item_id": "bee577f5-a51e-4fcd-a0d9-d894183e5e1f",
    "eligible_master": true
  },
  {
    "kode": "100111",
    "nama": "Bubble Wrap Hitam",
    "satuan": "ROLL",
    "accurate_ats": 24,
    "master_item_id": "2b61e7ae-a566-401a-9b43-5d42c32cd935",
    "eligible_master": false
  },
  {
    "kode": "100212",
    "nama": "Burger Box",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "39c6ba28-3f83-4534-9abb-0f53c7601e9c",
    "eligible_master": true
  },
  {
    "kode": "100348",
    "nama": "Holder Cup Pulp (4 Sekat)",
    "satuan": "PCS",
    "accurate_ats": 2100,
    "master_item_id": "dfaf4ed1-e344-416c-bbff-ed69314c0376",
    "eligible_master": true
  },
  {
    "kode": "100230",
    "nama": "LID 12 oz Double Wall Hitam",
    "satuan": "PCS",
    "accurate_ats": 2000,
    "master_item_id": "9cdb86bb-5fc6-45fa-9edc-82d96ead90fb",
    "eligible_master": true
  },
  {
    "kode": "100167",
    "nama": "LID 8 oz Double Wall White",
    "satuan": "PCS",
    "accurate_ats": 250,
    "master_item_id": "1d294c7c-9bf2-46b4-b732-6d5ae379a985",
    "eligible_master": true
  },
  {
    "kode": "100055",
    "nama": "LID Bening Papper Bowl 500 ML",
    "satuan": "PCS",
    "accurate_ats": 2500,
    "master_item_id": "283fcd10-881d-4f1a-b031-261af3e75d87",
    "eligible_master": true
  },
  {
    "kode": "100042",
    "nama": "LID Bening Papper Bowl 650 ML Wmp",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "9ca0dc76-f024-4a11-96ae-c7093bbecc3e",
    "eligible_master": true
  },
  {
    "kode": "100070",
    "nama": "LID Bening Papper Bowl 720 ML",
    "satuan": "PCS",
    "accurate_ats": 600,
    "master_item_id": "b51fbc2b-5919-432b-9444-317169727520",
    "eligible_master": true
  },
  {
    "kode": "100062",
    "nama": "LID Cembung DB Starindo",
    "satuan": "PCS",
    "accurate_ats": 2000,
    "master_item_id": "c641578a-38b0-4c82-bc11-60ad9ea794c6",
    "eligible_master": true
  },
  {
    "kode": "100275",
    "nama": "LID Connect Frosted Biru Tua",
    "satuan": "PCS",
    "accurate_ats": 5000,
    "master_item_id": "8d10ba25-3a1c-4824-9c71-bb10209a17bb",
    "eligible_master": true
  },
  {
    "kode": "100286",
    "nama": "LID Connect Frosted Hitam Victory",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "c0edd202-d99a-4178-b231-aec6fcf184be",
    "eligible_master": true
  },
  {
    "kode": "100131",
    "nama": "LID Connect Inject Bening GSS",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "454a4f19-f525-4934-8b1c-f50fff452ac4",
    "eligible_master": true
  },
  {
    "kode": "100244",
    "nama": "LID Connect Inject Bening Klir",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "cd0ebd85-3283-4708-80e9-abfd7e81b07d",
    "eligible_master": true
  },
  {
    "kode": "100243",
    "nama": "LID Connect Inject Bening Super",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "3b381ef3-c2bf-46b9-b35c-2ee531a18a1a",
    "eligible_master": true
  },
  {
    "kode": "100115",
    "nama": "LID Connect Inject Bening Victory",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "d67457f3-3bb4-4b42-91e0-3011a215beeb",
    "eligible_master": true
  },
  {
    "kode": "100148",
    "nama": "LID Connect Inject Bening Wingoh",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "a80fb6c9-cf22-474e-aca2-74284ffbb518",
    "eligible_master": true
  },
  {
    "kode": "100334",
    "nama": "LID Connect Inject Frosted Wingoh",
    "satuan": "PCS",
    "accurate_ats": 17000,
    "master_item_id": "c1a0a9cb-4509-4601-bd76-d1f0b7dea777",
    "eligible_master": true
  },
  {
    "kode": "100129",
    "nama": "LID Connect Inject Frosted/Dop Victory",
    "satuan": "PCS",
    "accurate_ats": 2850,
    "master_item_id": "48cb1ee4-18d6-4a7b-bab8-52fae69f4ad8",
    "eligible_master": true
  },
  {
    "kode": "100116",
    "nama": "LID Connect Inject Hitam",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "d98801b4-10e9-4f36-a8de-41e9b3d75067",
    "eligible_master": true
  },
  {
    "kode": "100389",
    "nama": "LID Connect Inject Hitam Frosted Wingoh",
    "satuan": "PCS",
    "accurate_ats": 9000,
    "master_item_id": "2c3d8b3f-3280-4d04-bad1-fa590adb19af",
    "eligible_master": true
  },
  {
    "kode": "100394",
    "nama": "LID Connect Inject Hitam Glosy Klir",
    "satuan": "PCS",
    "accurate_ats": 5000,
    "master_item_id": "788e064a-26e5-4151-830c-8dfb6e9f54b5",
    "eligible_master": true
  },
  {
    "kode": "100393",
    "nama": "LID Connect Inject Hitam Glosy Super",
    "satuan": "PCS",
    "accurate_ats": 5000,
    "master_item_id": "ad20b9b8-6bcb-4f14-b279-7cc946a02dda",
    "eligible_master": true
  },
  {
    "kode": "100304",
    "nama": "LID Connect Inject Merah",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "b9aee36a-d38d-4d50-8b68-f5c4025364d3",
    "eligible_master": true
  },
  {
    "kode": "100027",
    "nama": "LID Datar DB Starindo",
    "satuan": "PCS",
    "accurate_ats": 7000,
    "master_item_id": "b20c517f-bb20-4d32-9bef-e8a86025a06d",
    "eligible_master": true
  },
  {
    "kode": "100178",
    "nama": "LID Flat PP TAM",
    "satuan": "PCS",
    "accurate_ats": 2000,
    "master_item_id": "f4ad6176-bc8e-420f-8454-698353cb42f5",
    "eligible_master": true
  },
  {
    "kode": "100026",
    "nama": "LID Hitam Papper Cup 8 oz STR",
    "satuan": "PCS",
    "accurate_ats": 3997,
    "master_item_id": "32e94cbf-ee25-44d7-a495-549838470071",
    "eligible_master": true
  },
  {
    "kode": "100390",
    "nama": "LID Inject Bening Dome Wingoh",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "497d397a-ae0d-43ca-a972-59d7dc8580a4",
    "eligible_master": true
  },
  {
    "kode": "100260",
    "nama": "LID Inject Flip Bening Wingoh",
    "satuan": "PCS",
    "accurate_ats": 7800,
    "master_item_id": "6a25f1dc-2aa5-45e6-994b-33948eb01b60",
    "eligible_master": true
  },
  {
    "kode": "100288",
    "nama": "LID Inject Flip Hitam Victory",
    "satuan": "PCS",
    "accurate_ats": 8500,
    "master_item_id": "3ff8aa1e-bbd5-4ae7-b4a4-916a6a3b569a",
    "eligible_master": true
  },
  {
    "kode": "100277",
    "nama": "LID Inject Flip Hitam Wingoh",
    "satuan": "PCS",
    "accurate_ats": 6500,
    "master_item_id": "08e453b7-441e-4df4-85b4-6940228b861b",
    "eligible_master": true
  },
  {
    "kode": "100340",
    "nama": "LID Inject Flip Leak Proof",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "f5422e8b-4ca8-4273-892f-1cf2d91fed59",
    "eligible_master": true
  },
  {
    "kode": "100342",
    "nama": "LID Inject Flip Merah Wingoh",
    "satuan": "PCS",
    "accurate_ats": 4500,
    "master_item_id": "276b0ea1-ff1d-4bba-ad42-3118a4f64b8e",
    "eligible_master": true
  },
  {
    "kode": "100203",
    "nama": "LID PET Dome Starindo",
    "satuan": "PCS",
    "accurate_ats": 700,
    "master_item_id": "640634ca-7c3d-4382-a184-f4c442b7d9bc",
    "eligible_master": true
  },
  {
    "kode": "100087",
    "nama": "LID PET FLAT Starindo",
    "satuan": "PCS",
    "accurate_ats": 6000,
    "master_item_id": "56fc42b9-eeaf-4bab-8501-a8246e6b56c3",
    "eligible_master": true
  },
  {
    "kode": "100455",
    "nama": "LID PET Strawless 9.8 PLP",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "d882b3ae-088c-46e1-a82b-66405f80fab5",
    "eligible_master": true
  },
  {
    "kode": "100023",
    "nama": "LID PET Strawless PLP",
    "satuan": "PCS",
    "accurate_ats": 6400,
    "master_item_id": "e678ff02-2738-4fee-9605-ea3973948cc5",
    "eligible_master": true
  },
  {
    "kode": "100240",
    "nama": "LID PET Strawless TPP",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "1fdc5547-0d0e-42e3-aa57-8044cc7ea017",
    "eligible_master": true
  },
  {
    "kode": "100460",
    "nama": "LID PET Strawless Tam",
    "satuan": "PCS",
    "accurate_ats": 118400,
    "master_item_id": "e69b90ec-4f13-4e9f-8157-b237de39e505",
    "eligible_master": true
  },
  {
    "kode": "100396",
    "nama": "LID PP Bowl Bening TipTop",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "9f9e9c14-b556-4838-a254-bda8f82a65ab",
    "eligible_master": true
  },
  {
    "kode": "100037",
    "nama": "LID PP Strawless Starindo",
    "satuan": "PCS",
    "accurate_ats": 5900,
    "master_item_id": "88d330b0-c7bf-4db5-95ee-96e60003036b",
    "eligible_master": true
  },
  {
    "kode": "100048",
    "nama": "LID Putih Papper Cup 8 oz STR",
    "satuan": "PCS",
    "accurate_ats": 2000,
    "master_item_id": "047ec862-2d5a-457d-bb21-44b7085a33e0",
    "eligible_master": true
  },
  {
    "kode": "100145",
    "nama": "LID Putih Papper Cup Cold 16/22 oz Starindo",
    "satuan": "PCS",
    "accurate_ats": 5000,
    "master_item_id": "79d77f46-c7e0-43cf-95f5-6bac5ce52f22",
    "eligible_master": true
  },
  {
    "kode": "100435",
    "nama": "LID Sealer Alibaba",
    "satuan": "ROLL",
    "accurate_ats": 24,
    "master_item_id": "1cd774c8-c501-477e-8245-6be5472cb04d",
    "eligible_master": true
  },
  {
    "kode": "100166",
    "nama": "Lid Hitam Paper 9 oz Starindo",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "99f53e4c-fd04-4edb-8dda-1d056579bc2c",
    "eligible_master": true
  },
  {
    "kode": "100213",
    "nama": "Lunch Box Cokelat Aldo (L)",
    "satuan": "PCS",
    "accurate_ats": 300,
    "master_item_id": "80dcabce-dea8-4c2e-87a4-9283a4a79509",
    "eligible_master": true
  },
  {
    "kode": "100200",
    "nama": "Lunch Box Cokelat Aldo (M)",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "aca7f6e5-35ed-4736-bd68-39ac7a71f137",
    "eligible_master": true
  },
  {
    "kode": "100295",
    "nama": "Lunch Box Printing (L)",
    "satuan": "PCS",
    "accurate_ats": 632,
    "master_item_id": "2a64089e-8007-433b-a092-83f0abc9c27e",
    "eligible_master": true
  },
  {
    "kode": "100189",
    "nama": "Lunch Box Printing (M)",
    "satuan": "PCS",
    "accurate_ats": 1300,
    "master_item_id": "eb6e56f5-7dcb-4a9b-ad59-f8ea2a95d3c3",
    "eligible_master": true
  },
  {
    "kode": "100102",
    "nama": "PASTIK TRASBAG JUMBO",
    "satuan": "PACK",
    "accurate_ats": 1,
    "master_item_id": "759aa21f-4159-400c-ac5c-643479d60f4a",
    "eligible_master": false
  },
  {
    "kode": "100187",
    "nama": "PLASTIK SLOP CUP",
    "satuan": "KG",
    "accurate_ats": 301,
    "master_item_id": "b05b7e9f-d99a-4748-a3f5-f5b519bb4c9c",
    "eligible_master": true
  },
  {
    "kode": "100109",
    "nama": "PLASTIK TRASHBAG",
    "satuan": "PACK",
    "accurate_ats": 1,
    "master_item_id": "50c10ae7-53a2-40bc-9a94-03e14a1ddafa",
    "eligible_master": true
  },
  {
    "kode": "100160",
    "nama": "PLAT PRINTING",
    "satuan": "PCS",
    "accurate_ats": 1,
    "master_item_id": "abb6b507-cb24-4eb6-83dc-c37e6491a947",
    "eligible_master": true
  },
  {
    "kode": "100395",
    "nama": "PP Bowl 650 ML Putih TipTop",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "9041c615-9237-49c2-aa46-c269fb1cd3fd",
    "eligible_master": true
  },
  {
    "kode": "100425",
    "nama": "Paper Bag Hijau (M)",
    "satuan": "PCS",
    "accurate_ats": 10,
    "master_item_id": "d8110ee5-5376-4e78-b864-09a79f4a9d41",
    "eligible_master": true
  },
  {
    "kode": "100120",
    "nama": "Paper Rice",
    "satuan": "PCS",
    "accurate_ats": 1500,
    "master_item_id": "3541f32d-4ccc-4f71-a957-e9563c6a9957",
    "eligible_master": true
  },
  {
    "kode": "100054",
    "nama": "Papper Bowl 500 ML",
    "satuan": "PCS",
    "accurate_ats": 2000,
    "master_item_id": "f13dee05-8e28-43ff-b848-f83aba9476a4",
    "eligible_master": true
  },
  {
    "kode": "100041",
    "nama": "Papper Bowl 650 ML",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "402dd6e2-0c79-4ff0-8839-53d2e8ef90d5",
    "eligible_master": true
  },
  {
    "kode": "100414",
    "nama": "Papper Bowl 650 ML Craft Wmp",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "81b1c84c-1595-433d-9222-6056784d02b1",
    "eligible_master": true
  },
  {
    "kode": "100069",
    "nama": "Papper Bowl 720 ML",
    "satuan": "PCS",
    "accurate_ats": 900,
    "master_item_id": "8f4d7c7b-428d-43ac-bdd0-64057b81a66a",
    "eligible_master": true
  },
  {
    "kode": "100072",
    "nama": "Papper Bowl 800 ML",
    "satuan": "PCS",
    "accurate_ats": 300,
    "master_item_id": "32052be3-be0e-4b16-bdce-bad897b1e691",
    "eligible_master": true
  },
  {
    "kode": "100034",
    "nama": "Papper Cup Coklat/Craft 8 oz Starindo",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "cac87723-4e56-4185-975e-ddd2861f5dd9",
    "eligible_master": true
  },
  {
    "kode": "100229",
    "nama": "Papper Cup Double Wall 12 oz Printing",
    "satuan": "PCS",
    "accurate_ats": 28,
    "master_item_id": "a9ed6939-5e87-4e5b-ae98-a2fb0491ade7",
    "eligible_master": true
  },
  {
    "kode": "100051",
    "nama": "Papper Cup Double Wall 8 oz Coklat/Craft",
    "satuan": "PCS",
    "accurate_ats": 4500,
    "master_item_id": "0279c296-02df-49f3-b22a-35d205aa61e1",
    "eligible_master": true
  },
  {
    "kode": "100211",
    "nama": "Papper Cup Double Wall 8 oz Printing",
    "satuan": "PCS",
    "accurate_ats": 4495,
    "master_item_id": "a64a43fb-8fd0-4cfa-9b12-dd54516ac434",
    "eligible_master": true
  },
  {
    "kode": "100168",
    "nama": "Papper Cup Double Wall 8 oz Putih",
    "satuan": "PCS",
    "accurate_ats": 8647,
    "master_item_id": "eced7bb7-cda9-43b0-85a4-03ecb1c4662f",
    "eligible_master": true
  },
  {
    "kode": "100021",
    "nama": "Papper Cup Hitam 8 oz Starindo",
    "satuan": "PCS",
    "accurate_ats": 3497,
    "master_item_id": "1c20d4b3-d0da-4a83-a24e-d9296fc86db7",
    "eligible_master": true
  },
  {
    "kode": "100060",
    "nama": "Papper Cup Putih 4.5 oz",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "78635b02-d371-41f8-8732-a1cd282a0f06",
    "eligible_master": true
  },
  {
    "kode": "100081",
    "nama": "Papper Cup Putih 6.5 oz",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "4652c77e-4fc8-491f-af1f-489422804f3d",
    "eligible_master": true
  },
  {
    "kode": "100036",
    "nama": "Papper Cup Putih 8 oz Starindo",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "e811300b-c409-433d-8399-dbb9bbe57b7d",
    "eligible_master": true
  },
  {
    "kode": "100245",
    "nama": "Papper Cup Single 8 oz Printing",
    "satuan": "PCS",
    "accurate_ats": 100,
    "master_item_id": "6eaa4b10-62db-4d40-b530-49566aeda77b",
    "eligible_master": true
  },
  {
    "kode": "100207",
    "nama": "Papper Tray Coklat/Craft M",
    "satuan": "PCS",
    "accurate_ats": 800,
    "master_item_id": "c614b119-538c-4b9f-8354-d25f336b003d",
    "eligible_master": true
  },
  {
    "kode": "100191",
    "nama": "Papper Tray Printing",
    "satuan": "PCS",
    "accurate_ats": 5,
    "master_item_id": "62628075-504c-42ad-abce-d5aeb69c4db4",
    "eligible_master": true
  },
  {
    "kode": "100447",
    "nama": "Papper Tray Printing 290 Gr",
    "satuan": "PCS",
    "accurate_ats": 958,
    "master_item_id": "970f7835-87cd-467d-bf63-f1e738609978",
    "eligible_master": true
  },
  {
    "kode": "100210",
    "nama": "Plastik HD Plong Putih 20 x 30",
    "satuan": "PCS",
    "accurate_ats": 20000,
    "master_item_id": "9208b6b4-fea1-487d-9eeb-41248a56f788",
    "eligible_master": true
  },
  {
    "kode": "100225",
    "nama": "Plastik Klip Pond Hitam 20 x 30",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "54c4a9ea-6769-4b9f-8991-64580c35adbf",
    "eligible_master": true
  },
  {
    "kode": "100314",
    "nama": "STRAW / SEDOTAN BOBA",
    "satuan": "PCS",
    "accurate_ats": 0,
    "master_item_id": "f36f8b8a-4601-4018-a350-83e165dee195",
    "eligible_master": true
  },
  {
    "kode": "100320",
    "nama": "STRAW/SEDOTAN HITAM MASTER/BOBA",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "f760e10a-464b-4f9d-8b7c-739c7f6a0986",
    "eligible_master": true
  },
  {
    "kode": "100052",
    "nama": "STRAW/SEDOTAN LANCIP 620 ISI 300 PCS",
    "satuan": "PACK",
    "accurate_ats": 24,
    "master_item_id": "d2be20b4-64d1-417a-bc82-5a9d65118a64",
    "eligible_master": true
  },
  {
    "kode": "100346",
    "nama": "STRAW/SEDOTAN LANCIP 620 ISI 500 PCS",
    "satuan": "PACK",
    "accurate_ats": 21,
    "master_item_id": "9be0671c-135e-42d8-98f3-4d1d38c7f61d",
    "eligible_master": true
  },
  {
    "kode": "100251",
    "nama": "Standing Klip Pouch Hitam 10 x 16 cm",
    "satuan": "PCS",
    "accurate_ats": 25,
    "master_item_id": "d5aa91bf-a21d-4d42-9f1d-04e0de4732f0",
    "eligible_master": true
  },
  {
    "kode": "100269",
    "nama": "Wrap Paper Printing",
    "satuan": "PCS",
    "accurate_ats": 500,
    "master_item_id": "11f2e845-8024-4364-ab76-85e16a053e92",
    "eligible_master": true
  },
  {
    "kode": "100426",
    "nama": "Wrap Paper Printing 22.5 X 35",
    "satuan": "PCS",
    "accurate_ats": 1500,
    "master_item_id": "6f3fcec7-6b1e-486f-b27b-da4ab10ae414",
    "eligible_master": true
  },
  {
    "kode": "100361",
    "nama": "Wrap Paper Printing 28 X 28",
    "satuan": "PCS",
    "accurate_ats": 1000,
    "master_item_id": "64b17340-f612-4b46-8273-199927c4b1d7",
    "eligible_master": true
  }
]
__ETERNAL_ATS_YGY_RO_JSON_V1__

cat > "$WORK_DIR/ro_ats_ygy_post_cutoff_export.py" <<'__ETERNAL_ATS_YGY_RO_PY_V1__'
#!/usr/bin/env python3
"""READ-ONLY YGY-MAIN ATS post-cutoff export for local SSI preview.

Cutoff (Owner): 2026-11-02T23:22:00+07:00 == 2026-11-02T16:22:00Z

Exports:
  - live release identity (from env / files passed by shell)
  - orders (with stock_allocations + change times) that are either
    created after cutoff OR updated/cancelled after cutoff
    (covers pre-cutoff PO that changed after cutoff)
  - YGY-MAIN physical_stock_ledger rows at/after cutoff (all kinds)
  - REVERSAL rows that reference those movements (even if edge-timed)

No insert/update/delete/adjust/import/deploy/posting/restart.
Not an atomic snapshot — started_at / finished_at only.
Hard caps → TRUNCATED flags; never claim complete if capped.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReadPreference

WH = "b3d0bf36-1140-4eac-b0f7-b5c924ed0d24"
BR = "5c92529c-3df8-4d9f-b290-a5c94fa51903"
WH_CODE = "YGY-MAIN"
CUTOFF_AT = "2026-11-02T23:22:00+07:00"
CUTOFF_UTC = "2026-11-02T16:22:00Z"

# Hard caps — if hit, result is TRUNCATED (never report as complete).
LIMIT_MASTERS = 500
LIMIT_ORDERS = 8000
LIMIT_LEDGER = 30000
LIMIT_REVERSAL = 30000

# Mode B Accurate Live branch tokens commonly seen for YGY (read-only filter aid).
ACCURATE_LIVE_BRANCH_TOKENS = ("50", "YGY", BR)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _cutoff() -> datetime:
    dt = _parse_dt(CUTOFF_AT)
    assert dt is not None
    return dt


def _load_candidates() -> list:
    path = Path(os.environ["ETERNAL_RO_CANDIDATES_JSON"])
    return json.loads(path.read_text())


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
    """True if created after cutoff OR any change time after cutoff."""
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


async def main() -> None:
    started_at = _now()
    trunc_flags: Dict[str, bool] = {}
    notes: List[str] = [
        "NOT_ATOMIC_SNAPSHOT: reads span started_at..finished_at on PRIMARY",
        "No writes / no ensure_indexes / no deploy / no restart",
        "Includes pre-cutoff PO that changed after cutoff (updated_at/cancelled_at)",
        "Mode B tokens used only as RO filter aid; collector applies official Mode B later",
    ]

    env = dotenv_values(os.environ["ETERNAL_RO_ENV_FILE"])
    uri = env.get("MONGO_URL") or env.get("MONGODB_URI") or env.get("DATABASE_URL")
    dbn = env.get("DB_NAME") or env.get("MONGODB_DB") or env.get("MONGO_DB")
    if not uri or not dbn:
        raise SystemExit("STOP_MONGO_ENV")

    # Never print credentials.
    uri_safe = re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", uri)

    out = Path(os.environ["ETERNAL_RO_OUT_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    candidates = _load_candidates()
    codes = [str(c["kode"]) for c in candidates]
    code_set = set(codes)
    cutoff = _cutoff()

    client = AsyncIOMotorClient(uri, read_preference=ReadPreference.PRIMARY)
    db = client[dbn]

    wh = await db["warehouses"].find_one(
        {"id": WH},
        {"_id": 0, "id": 1, "code": 1, "name": 1, "branch_id": 1, "is_active": 1},
    )
    if not wh:
        raise SystemExit("STOP_WAREHOUSE_NOT_FOUND")
    live_code = str(wh.get("code") or "").strip().upper()
    live_branch = str(wh.get("branch_id") or "").strip()
    if live_code != WH_CODE:
        raise SystemExit(f"STOP_WAREHOUSE_CODE_MISMATCH expected={WH_CODE} live={live_code}")
    if live_branch != BR:
        raise SystemExit(
            f"STOP_WAREHOUSE_BRANCH_MISMATCH expected={BR} live={live_branch}"
        )

    masters, trunc_flags["masters"] = await _fetch_capped(
        db["master_items"].find(
            {"item_code": {"$in": codes}},
            {
                "_id": 0,
                "id": 1,
                "item_code": 1,
                "item_name": 1,
                "unit": 1,
                "item_type": 1,
                "is_active": 1,
            },
        ),
        LIMIT_MASTERS,
    )
    by_code: Dict[str, List[dict]] = {}
    for m in masters:
        by_code.setdefault(str(m.get("item_code")), []).append(m)

    resolve_rows: List[dict] = []
    live_mids: List[str] = []
    for c in candidates:
        kode = str(c["kode"])
        hits = by_code.get(kode) or []
        prior_mid = str(c.get("master_item_id") or "").strip() or None
        resolved_mid = None
        resolve_status = "UNRESOLVED"
        if len(hits) == 1:
            resolved_mid = str(hits[0].get("id") or "").strip() or None
            if prior_mid and resolved_mid and prior_mid == resolved_mid:
                resolve_status = "LIVE_CODE_MATCH_PRIOR_ID_OK"
            elif prior_mid and resolved_mid and prior_mid != resolved_mid:
                resolve_status = "LIVE_CODE_MATCH_PRIOR_ID_STALE_IGNORED"
            else:
                resolve_status = "LIVE_CODE_MATCH"
        elif len(hits) > 1:
            resolve_status = "AMBIGUOUS_MULTIPLE_LIVE_MASTERS"
        elif prior_mid:
            resolve_status = "NO_LIVE_CODE_HIT_PRIOR_ID_NOT_TRUSTED"
        if resolved_mid:
            live_mids.append(resolved_mid)
        resolve_rows.append(
            {
                "kode": kode,
                "nama": c.get("nama"),
                "satuan": c.get("satuan"),
                "accurate_ats": c.get("accurate_ats"),
                "eligible_master": c.get("eligible_master"),
                "prior_master_item_id": prior_mid,
                "resolved_master_item_id": resolved_mid,
                "resolve_status": resolve_status,
            }
        )
    live_mids = sorted(set(live_mids))
    mid_set = set(live_mids)

    # Orders: Mode B / YGY branch tokens OR lines matching 162 masters.
    # Then keep only those with create-or-change time after cutoff.
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
        "_id": 0,
        "id": 1,
        "po_number": 1,
        "order_number": 1,
        "branch_id": 1,
        "cabang_id": 1,
        "accurate_branch_id": 1,
        "accurate_id": 1,
        "import_source": 1,
        "warehouse_id": 1,
        "cancelled": 1,
        "is_cancelled": 1,
        "cancelled_at": 1,
        "status": 1,
        "status_label": 1,
        "created_at": 1,
        "imported_at": 1,
        "updated_at": 1,
        "status_updated_at": 1,
        "last_synced_at": 1,
        "line_items": 1,
    }
    orders_raw, trunc_flags["orders_raw"] = await _fetch_capped(
        db["orders"].find(order_query, order_proj),
        LIMIT_ORDERS,
    )

    orders_out: List[dict] = []
    class_counts = {
        "post_cutoff_created": 0,
        "pre_cutoff_changed_after": 0,
        "skipped_no_post_cutoff_change": 0,
        "skipped_no_162_line": 0,
    }
    alloc_probe = {
        "order_lines_in_162": 0,
        "lines_with_stock_allocations": 0,
        "lines_without_stock_allocations": 0,
        "alloc_rows_ygy_main": 0,
        "alloc_qty_ygy_main": 0.0,
    }

    for o in orders_raw:
        lines_kept: List[dict] = []
        for li in o.get("line_items") or []:
            if not isinstance(li, dict):
                continue
            ic = str(li.get("item_code") or "")
            mid = str(li.get("master_item_id") or "")
            if ic not in code_set and mid not in mid_set:
                continue
            lines_kept.append(li)
            alloc_probe["order_lines_in_162"] += 1
            allocs = li.get("stock_allocations") if isinstance(li.get("stock_allocations"), list) else []
            if allocs:
                alloc_probe["lines_with_stock_allocations"] += 1
            else:
                alloc_probe["lines_without_stock_allocations"] += 1
            for a in allocs:
                if not isinstance(a, dict):
                    continue
                if str(a.get("warehouse_id") or "").strip() == WH:
                    alloc_probe["alloc_rows_ygy_main"] += 1
                    try:
                        alloc_probe["alloc_qty_ygy_main"] += float(a.get("qty") or 0)
                    except (TypeError, ValueError):
                        pass
        if not lines_kept:
            class_counts["skipped_no_162_line"] += 1
            continue
        if not _order_relevant_after_cutoff(o, cutoff):
            class_counts["skipped_no_post_cutoff_change"] += 1
            continue
        created = _parse_dt(o.get("created_at") or o.get("imported_at"))
        if created is not None and created > cutoff:
            class_counts["post_cutoff_created"] += 1
            relevance = "POST_CUTOFF_CREATED"
        else:
            class_counts["pre_cutoff_changed_after"] += 1
            relevance = "PRE_CUTOFF_CHANGED_AFTER_CUTOFF"
        row = dict(o)
        row["line_items"] = lines_kept
        row["_ro_relevance"] = relevance
        orders_out.append(_json_safe(row))

    trunc_flags["orders_exported"] = bool(trunc_flags.get("orders_raw"))

    # Ledger: YGY-MAIN, occurred_at >= cutoff (string or date). Include all kinds.
    cutoff_iso = CUTOFF_UTC
    ledger_query = {
        "warehouse_id": WH,
        "$or": [
            {"occurred_at": {"$gte": cutoff}},
            {"occurred_at": {"$gte": cutoff_iso}},
            {"occurred_at": {"$gte": CUTOFF_AT}},
        ],
    }
    if live_mids:
        ledger_query = {
            "$and": [
                ledger_query,
                {"master_item_id": {"$in": live_mids}},
            ]
        }
    ledger_proj = {
        "_id": 0,
        "id": 1,
        "master_item_id": 1,
        "branch_id": 1,
        "warehouse_id": 1,
        "movement_type": 1,
        "qty_delta": 1,
        "reference_type": 1,
        "reference_id": 1,
        "reason": 1,
        "actor_id": 1,
        "occurred_at": 1,
        "created_at": 1,
        "idempotency_key": 1,
        "metadata": 1,
        "balance_before": 1,
        "balance_after": 1,
    }
    ledger_raw, trunc_flags["ledger"] = await _fetch_capped(
        db["physical_stock_ledger"].find(ledger_query, ledger_proj),
        LIMIT_LEDGER,
    )
    ledger_out = [_json_safe(d) for d in ledger_raw]
    movement_ids: Set[str] = {
        str(d.get("id") or "").strip()
        for d in ledger_raw
        if str(d.get("id") or "").strip()
    }

    kind_counts: Dict[str, int] = {}
    for d in ledger_raw:
        k = str(d.get("movement_type") or "UNKNOWN")
        kind_counts[k] = kind_counts.get(k, 0) + 1

    # Extra REVERSAL rows that point at exported movements (coverage for late reverse).
    reversals_extra: List[dict] = []
    trunc_flags["reversals_extra"] = False
    if movement_ids:
        rev_query = {
            "warehouse_id": WH,
            "movement_type": "REVERSAL",
            "reference_id": {"$in": sorted(movement_ids)},
        }
        rev_raw, trunc_flags["reversals_extra"] = await _fetch_capped(
            db["physical_stock_ledger"].find(rev_query, ledger_proj),
            LIMIT_REVERSAL,
        )
        already = {str(d.get("id") or "") for d in ledger_raw}
        for d in rev_raw:
            rid = str(d.get("id") or "")
            if rid and rid not in already:
                reversals_extra.append(_json_safe(d))

    finished_at = _now()
    any_trunc = any(bool(v) for v in trunc_flags.values())
    history_complete = not any_trunc
    preview_class_hint = (
        "POST_CUTOFF_CANDIDATE_EXPORT"
        if (orders_out or ledger_out or reversals_extra)
        else "EMPTY_POST_CUTOFF_STREAM"
    )

    identity = {
        "active_sha_env": os.environ.get("ETERNAL_RO_ACTIVE_SHA") or "",
        "active_sha_file": os.environ.get("ETERNAL_RO_ACTIVE_SHA_FILE") or "",
        "previous_id": os.environ.get("ETERNAL_RO_PREVIOUS_ID") or "",
        "current_link": os.environ.get("ETERNAL_RO_CURRENT_LINK") or "",
        "ats_mode_env": os.environ.get("ETERNAL_RO_ATS_MODE") or "",
        "health_json": os.environ.get("ETERNAL_RO_HEALTH_JSON") or "",
        "mongo_uri_redacted": uri_safe,
        "db_name": dbn,
        "host": os.environ.get("ETERNAL_RO_HOST") or "",
        "user": os.environ.get("ETERNAL_RO_USER") or "",
    }

    manifest = {
        "export_kind": "ATS_YGY_POST_CUTOFF_RO",
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "cutoff_at": CUTOFF_AT,
        "cutoff_at_utc": CUTOFF_UTC,
        "warehouse_id": WH,
        "warehouse_code": WH_CODE,
        "branch_id": BR,
        "candidate_count": len(candidates),
        "resolved_master_count": len(live_mids),
        "orders_exported": len(orders_out),
        "ledger_exported": len(ledger_out),
        "reversals_extra_exported": len(reversals_extra),
        "order_class_counts": class_counts,
        "ledger_movement_type_counts": kind_counts,
        "allocation_probe": alloc_probe,
        "truncation": trunc_flags,
        "history_complete": history_complete,
        "history_complete_meaning": (
            "True only when no fetch hit a hard cap. Still NOT an atomic snapshot."
        ),
        "preview_class_hint": preview_class_hint,
        "activation_ready": False,
        "notes": notes,
        "identity": identity,
        "files": [
            "manifest.json",
            "live_identity.json",
            "master_resolve.json",
            "orders.json",
            "physical_stock_ledger.json",
            "reversals_extra.json",
            "CANDIDATES_162.json",
        ],
    }

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (out / "live_identity.json").write_text(json.dumps(identity, indent=2, ensure_ascii=False) + "\n")
    (out / "master_resolve.json").write_text(
        json.dumps(resolve_rows, indent=2, ensure_ascii=False) + "\n"
    )
    (out / "orders.json").write_text(json.dumps(orders_out, indent=2, ensure_ascii=False) + "\n")
    (out / "physical_stock_ledger.json").write_text(
        json.dumps(ledger_out, indent=2, ensure_ascii=False) + "\n"
    )
    (out / "reversals_extra.json").write_text(
        json.dumps(reversals_extra, indent=2, ensure_ascii=False) + "\n"
    )

    # Human summary line for SSH stdout
    print("RO_EXPORT_OK")
    print(f"STARTED={started_at}")
    print(f"FINISHED={finished_at}")
    print(f"ORDERS={len(orders_out)}")
    print(f"LEDGER={len(ledger_out)}")
    print(f"REVERSALS_EXTRA={len(reversals_extra)}")
    print(f"HISTORY_COMPLETE={history_complete}")
    print(f"TRUNCATION={json.dumps(trunc_flags, sort_keys=True)}")
    print(f"PREVIEW_CLASS_HINT={preview_class_hint}")
    print("ACTIVATION_READY=False")
    print(f"OUT_DIR={out}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())

__ETERNAL_ATS_YGY_RO_PY_V1__

cp "$WORK_DIR/CANDIDATES_162.json" "$OUT_DIR/"
cp "$WORK_DIR/ro_ats_ygy_post_cutoff_export.py" "$OUT_DIR/"

{
  echo "CURRENT_LINK=$CURRENT_LINK"
  echo "ACTIVE_SHA=$ACTIVE_SHA"
  echo "ACTIVE_SHA_FILE=${ACTIVE_SHA_FILE:-}"
  echo "PREVIOUS_ID=${PREVIOUS_ID:-}"
  echo "ETERNAL_ATS_YGY_MODE=$ATS_MODE"
  echo "HEALTH=$HEALTH_JSON"
  echo "CUTOFF=2026-11-02T23:22:00+07:00"
  echo "WAREHOUSE=YGY-MAIN"
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
export ETERNAL_RO_CANDIDATES_JSON="$OUT_DIR/CANDIDATES_162.json"
export ETERNAL_RO_HOST="$HOST_NAME"
export ETERNAL_RO_USER="$RUN_USER"

cd "$BE"
"$PY" "$OUT_DIR/ro_ats_ygy_post_cutoff_export.py" | tee "$OUT_DIR/export_stdout.txt"

TGZ="${OUT_DIR}.tgz"
tar -C "$(dirname "$OUT_DIR")" -czf "$TGZ" "$(basename "$OUT_DIR")"
echo "TGZ=$TGZ"
ls -la "$OUT_DIR" "$TGZ"
echo "=== RO_ATS_YGY_POST_CUTOFF_ONEFILE_END utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "NOTE: READ-ONLY complete. No DB update / restart / deploy / activation performed."
echo "SCP_TO_DOWNLOADS:"
echo "scp eternaladmin@103.126.116.122:$TGZ ~/Downloads/"
