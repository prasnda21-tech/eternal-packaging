# ETERNAL — Legacy Dirty Files Closeout

Status inventaris rekonsiliasi (bukti Owner / VPS, tidak di-hash ulang di Cloud Agent).

## Baseline

| Item | Nilai |
|---|---|
| GitHub baseline pembanding | `5779ef6958597283bdfa78e7b10df49ca184dc57` |
| Legacy checkout (VPS) | `/opt/eternal-packaging` — dipertahankan; tidak di-reset / clean |
| Backup verified | `/home/eternaladmin/eternal-legacy-recovery/20261022-210124` |
| Backup count | 63 file (`dirty-files.tar` / `tracked.patch` / `files.sha256` OK per Owner) |

## Klasifikasi hash (agregat Owner)

| Kelas | Jumlah |
|---|---|
| SAME | 35 |
| MATCHES_OLD_TARGET | 3 |
| DIFFERENT_REVIEW | 25 |
| Total dirty | 63 |

## Keputusan closeout (turn ini)

Backup verified; semantic reconciliation of 25 DIFFERENT_REVIEW files remains open; no legacy code restored.

Ringkasan penegakan:

1. Tidak ada salinan massal kode legacy ke GitHub.
2. `set_urgent` lama **tidak** dipulihkan.
3. Permission / cancel sebelum PR #635 **tidak** dipulihkan.
4. 25 file DIFFERENT_REVIEW **tidak** dinyatakan REPLACED semantik — hanya status hash agregat yang tersedia.
5. Tidak ada fungsi LOCAL-UNIQUE yang terbukti di Cloud Agent → **tidak ada port legacy**.
6. Checkout legacy dan backup Owner tetap utuh (tidak `git reset`, `git clean`, atau hapus file).
7. Checkout legacy **tidak boleh dibersihkan otomatis** — arsip/cleanup hanya setelah Owner menutup 25 DIFFERENT_REVIEW.

## Yang sudah diganti di GitHub (konteks, bukan klaim file dirty VPS)

Permukaan bisnis yang pernah dipersoalkan pada audit dirty-files — status **target** `5779ef69`, bukan klasifikasi per-path VPS:

- Soft-cancel penerimaan → `sync_order_payment_projections` (+ heal proyeksi basi); jangan restore `_recalc_order_payment_facts_after_receipt_delete(..., [])`.
- Direktur / OPT_IN cancel-edit — kontrak gate pasca PR #635; jangan restore auto-allow.

## Bukti yang masih dibutuhkan (hanya jika Owner menutup 25 DIFFERENT_REVIEW)

Untuk keputusan akhir LOCAL-UNIQUE / CONFLICT per path: byte atau hunk terbatas dari backup `20261022-210124` untuk path kandidat saja (bukan ulang inventaris / hash penuh 63).

Tidak diminta di Cloud Agent pada turn ini.

## Lingkup di luar closeout legacy

- Implementasi Delivery SJ↔Loading follow-up UI dan UX 9.0 desktop date format berada di branch pengembangan terpisah; bukan restore legacy.
- PR #636 dan pekerjaan stok/HPP agent lain tidak disentuh.
