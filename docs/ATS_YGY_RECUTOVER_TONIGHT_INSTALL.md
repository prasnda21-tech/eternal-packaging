# ATS YGY-MAIN — cutover ulang (357 @ 2026-11-04T22:45+07)

Status: **paket pemasangan siap**. Production ATS mode **sudah activated** — installer hanya mengganti pointer baseline setelah RO evidence + Owner ACK. **Agent tidak mengaktifkan ulang / tidak menulis fisik/ledger.**

## Semantik baseline baru

| Aturan | Perilaku |
| --- | --- |
| Isi file | Stok bebas Accurate sudah net pesanan pada cutoff |
| Open PO | **Jangan** kurangi lagi seluruh PO terbuka |
| Di luar file | **Tidak** dinolkan |
| Inactive | Dilaporkan terpisah (`INACTIVE.json` / status EXCLUDED) |
| Missing / mismatch | Dilaporkan terpisah (`MISSING.json` / `MISMATCH.json`) |
| Empat kode kritis | `100463` `100464` `100488` `100489` → **ID Mongo live nyata saja** (no synthetic) |
| Fisik / ledger | **Tidak** ditulis |
| Cutoff | `2026-11-04T22:45:00+07:00` |
| Pointer aktif sekarang | Nov-2 (`ygy_main_2026-11-02T232200+0700_draft_162`) sampai install disetujui |
| Candidate | `ygy_main_2026-11-04T224500+0700_d8bef8b608c8` (353 eligible + 4 pending live match) |

## 1) Script RO mandiri (satu file, siap unduh)

Agent **tidak punya akses Mongo live**. Semua bukti match + preview pasca-cutoff diambil dengan **satu** script:

`scripts/ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh`

Mandiri terhadap live **9ff5a0cd** (tanpa import `services.stock_ats_*` / overlay PR #730).  
Mongo dari env terbukti: `/etc/eternal-packaging/backend.env` (bukan legacy `/opt/.../backend/.env`).  
`CUTOFF_UTC` diturunkan otomatis dari `CUTOFF_AT` → `2026-11-04T15:45:00Z`.

```bash
# dari laptop Owner
scp scripts/ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh eternaladmin@HOST:/tmp/
ssh eternaladmin@HOST 'bash /tmp/ETERNAL_RO_ATS_YGY_357_LIVE_MATCH_PREVIEW_ONEFILE.sh'
# unduh tarball yang dicetak TGZ=...
scp eternaladmin@HOST:/tmp/eternal_ro_ats_ygy_357_*.tgz ~/Downloads/
```

Script ini (READ-ONLY):

1. Mencocokkan **seluruh 357** `kode+satuan` ke `master_items` live.
2. Menolak ID sintetik; empat kode kritis wajib ID nyata.
3. Memisahkan `MISSING` / `INACTIVE` / `MISMATCH`.
4. Menghitung preview inline dari transaksi live **≥ cutoff** sampai waktu pemeriksaan (bukan mengklaim `BASELINE_ONLY` sebagai saldo sekarang).
5. Menulis `PREVIEW_TABLE.tsv` (baseline, penambahan, pengurangan, ats_akhir, status, gap) untuk semua kode.
6. Memverifikasi konsistensi sample `100032` baseline **9000** pada proyeksi yang sama.
7. Menandai `SNAPSHOT_VS_CUTOFF=OWNER_STATED_FILE_EQUALS_CUTOFF_UNVERIFIED_BY_AGENT` (ketidakpastian jam file vs cutoff).

Tidak mengubah registry, `ETERNAL_ATS_YGY_MODE`, saldo fisik, atau ledger.

## 2) Preview lokal tanpa RO (bukan saldo sekarang)

```bash
ETERNAL_ATS_YGY_MODE=draft_read \
ETERNAL_ATS_YGY_BASELINE_PATH=backend/data/ats_baselines/ygy_main_2026-11-04T224500+0700_d8bef8b608c8.json \
python3 backend/scripts/preview_ats_ygy_ssi_local.py --q 100032
```

Class `BASELINE_ONLY` di sini **bukan** saldo terkini — hanya angka file tanpa transaksi pasca-cutoff.

## 3) Paket pemasangan pointer (+ rollback)

`scripts/VPS_ADMIN_ATS_YGY_INSTALL_357_POINTER.sh`

Prasyarat:

- Tarball RO sudah ditinjau (`MATCHED_BASELINE.json`, `PREVIEW_TABLE.tsv`, `CRITICAL_FOUR.json`).
- Empat kode kritis = `MATCHED` dengan ID live.
- `unmatched_rows` kosong.
- Owner ACK snapshot file == cutoff.
- Production mode tetap `activated` (script **tidak** menulis ulang mode).

```bash
# install pointer (bukan re-aktivasi mode)
export ETERNAL_ATS_INSTALL_ACK=OWNER_ACK_RO_EVIDENCE_AND_SNAPSHOT
sudo -i bash /path/VPS_ADMIN_ATS_YGY_INSTALL_357_POINTER.sh install \
  /path/eternal_ro_ats_ygy_357_*/MATCHED_BASELINE.json

# rollback pointer ke baseline Nov-2 (100032=20000)
sudo -i bash /path/VPS_ADMIN_ATS_YGY_INSTALL_357_POINTER.sh rollback

# status pointer
sudo -i bash /path/VPS_ADMIN_ATS_YGY_INSTALL_357_POINTER.sh status
```

Bukti sukses install: `PROOF_100032=PASS` (baseline 9000), cutoff `2026-11-04T22:45:00+07:00`, critical four OK.

## 4) Pemeriksaan

- [ ] RO: 357 matched (atau missing/inactive/mismatch terpisah + Owner putusan)
- [ ] Critical four = ID live nyata
- [ ] Preview class ≠ klaim `BASELINE_ONLY` sebagai current
- [ ] `100032` baseline 9000 di list/detail/export
- [ ] Snapshot vs cutoff di-ACK Owner
- [ ] Install pointer hanya setelah ACK; mode tidak diubah
- [ ] Rollback Nov-2 tersedia dan terbukti

## Bukan dilakukan oleh agent

- Aktivasi ulang `ETERNAL_ATS_YGY_MODE`
- Mengubah pointer production sebelum install disetujui
- Menulis `physical_stock_balances` / ledger
- Membuat master otomatis / ID sintetik
