# ATS YGY-MAIN — cutover ulang (file Accurate malam ini)

Status: **kode + preview/tes siap**. Tunggu file + cutoff Owner. **Tidak ada deploy/aktivasi dari agent.**

## Semantik baseline baru

| Aturan | Perilaku |
| --- | --- |
| Isi file | Stok bebas Accurate sudah net pesanan pada cutoff |
| Open PO | **Jangan** kurangi lagi seluruh PO terbuka |
| Di luar file | **Tidak** dinolkan |
| Inactive | Dilaporkan terpisah (`inactive_rows` / status EXCLUDED) |
| Fisik / ledger | **Tidak** ditulis |
| Cutoff | Dari file Owner (bukan hardcode 2 Nov) |
| Baseline lama | Diarsipkan di `backend/data/ats_baselines/archive/` + registry history |

## Ambil file Accurate (jeda operasional sesingkat mungkin)

1. Owner tentukan **waktu cutoff** (ISO + zona, contoh `2026-11-04T22:30:00+07:00`).
2. Di Accurate, export **Stok Dapat Dijual** gudang YGY-MAIN **tepat pada/ setelah cutoff** dengan kolom minimal: `kode`, `satuan`, `qty` (nama opsional).
3. Selama jeda cutoff → aktivasi: catat bahwa transaksi live tetap masuk collector `as_of=now` (RESERVE / SHIP_RESERVED / RELEASE / RECEIVE / REVERSE) — **jangan** mengunci angka dump.
4. Serahkan file + cutoff ke agent/operator; jangan kirim password di chat.

## Build baseline berversi (local / VPS admin, belum activate mode)

```bash
cd /opt/eternal-packaging-runtime/current/backend   # atau checkout branch
python3 scripts/build_ats_ygy_baseline_from_accurate.py \
  --file /path/StokDapatDijual_YGY.xlsx \
  --cutoff 'YYYY-MM-DDTHH:MM:SS+07:00' \
  --db-name eternal_packaging
# cek JSON summary: eligible/inactive/unmatched + source_sha256
# lalu aktifkan pointer registry (masih belum set ETERNAL_ATS_YGY_MODE):
python3 scripts/build_ats_ygy_baseline_from_accurate.py \
  --file /path/StokDapatDijual_YGY.xlsx \
  --cutoff 'YYYY-MM-DDTHH:MM:SS+07:00' \
  --activate
```

Rollback pointer (tanpa hapus arsip):

```python
from services.stock_ats_baseline_store import rollback_registry_to
rollback_registry_to("ygy_main_2026-11-02T232200+0700_draft_162")
```

## Preview SSI (draft_read, DB throwaway)

```bash
ETERNAL_ATS_YGY_MODE=draft_read \
python3 scripts/preview_ats_ygy_ssi_local.py --q 100032
```

Periksa: `ats_baseline` = qty file, `ats_sekarang` = baseline ± transaksi pasca-cutoff, `physical_qty` tidak diganti, gap terlihat jika evidence incomplete.

## Aktivasi production (hanya setelah Owner ACK file+cutoff+preview)

1. Deploy SHA branch ini (manual Owner).
2. Pastikan registry `active` = baseline malam ini.
3. Jalankan prosedur admin `VPS_ADMIN_ATS_YGY_ACTIVATE.sh` yang `EXPECT_SHA` = SHA deploy, cutoff proof = cutoff file.
4. Bukti HTTP sample + `RESULT=ACTIVATED_READY|ACTIVATED_WITH_GAPS`.
5. Rollback mode: `ETERNAL_ATS_YGY_MODE=off` atau `... rollback`; baseline file tetap bisa di-rollback via registry.

## Pemeriksaan seluruh kode file

- [ ] Setiap baris file → master `kode+satuan` (unmatched tercatat)
- [ ] Inactive terpisah, tidak memaksa ATS 0 pada SKU di luar file
- [ ] Tidak ada RESERVE massal untuk seluruh PO pra-cutoff
- [ ] Transaksi pasca-cutoff muncul sekali (tidak dobel ship)
- [ ] Perubahan PO pra-cutoff hanya delta terbukti (`ats_reserved_qty_at_cutoff`) atau gap eksplisit
- [ ] SSI list/detail/export ATS konsisten; fisik unreconciled = **Belum direkonsiliasi**
- [ ] Rollback registry + mode off tersedia
