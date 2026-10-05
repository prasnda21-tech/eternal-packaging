---
name: eternal-fast-precision
description: Gunakan untuk task coding Eternal Packaging agar Cursor bekerja presisi, cepat, dan hemat token. Batasi scope, hindari audit ulang, gunakan bukti yang sudah ada, tes terarah, dan fallback ke mode detail jika presisi menurun.
---

# Eternal Fast Precision Mode

Tujuan:
- PRESISI tinggi
- MODE CEPAT
- HEMAT TOKEN / CREDIT
- TANPA mengurangi guardrail bisnis Eternal Packaging

Repository:
prasnda21-tech/eternal-packaging

## DEFAULT MODE

Gunakan MAIN AGENT saja.

JANGAN gunakan:
- subagent
- delegation
- video
- broad repo scan
- audit ulang penuh
- test suite luas tanpa alasan
- build ulang tanpa perubahan input
- production data untuk test
- perubahan di luar scope

Default response harus singkat dan actionable.

## 1. PRESISI

Sebelum mengubah source:
- pahami task exact;
- gunakan audit/bukti sebelumnya jika masih relevan;
- jangan menebak field, endpoint, file, permission, status, atau business rule;
- jika satu fakta penting belum terbukti, cek hanya gap itu;
- jangan rediscover seluruh flow dari nol.

Gunakan source-of-truth yang sudah terbukti.

Jika audit sebelumnya sudah membuktikan:
- file,
- function,
- endpoint,
- data source,
- guard,
maka langsung gunakan itu.

Jangan ulang audit kecuali:
- source berubah material;
- CI/test gagal;
- ada bukti baru;
- hasil sebelumnya ambigu;
- owner meminta audit ulang.

## 2. FAST MODE

Bekerja hanya pada jalur terpendek yang valid.

Prioritas:
1. file langsung terkait task;
2. helper yang dipanggil langsung;
3. test langsung terkait;
4. dependency hanya jika benar-benar dibutuhkan.

Jangan membaca seluruh repository.

Jangan menjalankan:
- full grep repo,
- broad test suite,
- full build,
- smoke luas,
jika focused check sudah cukup.

Jika perlu file tambahan:
laporkan singkat alasan sebelum memperluas scope.

## 3. HEMAT TOKEN

Jangan mengulang di prompt/laporan:
- sejarah percakapan;
- aturan bisnis yang tidak terkait;
- daftar modul Eternal lain;
- hasil audit lama yang tidak dipakai;
- test PASS yang tidak berubah;
- penjelasan panjang yang tidak membantu keputusan.

Gunakan decision contract ringkas.

Contoh:

BUSINESS RULE
- qty baru >= committed qty
- fully delivered reject
- unit price unchanged

SCOPE
- backend/server.py
- backend/services/...
- focused tests

JANGAN UBAH
- accounting
- payment
- shipment semantics

Itu lebih baik daripada menjelaskan seluruh ERP.

## 4. AUDIT MODE

Untuk task baru yang source-of-truth belum jelas:
audit hanya yang dibutuhkan untuk menjawab task.

Audit output minimum:
- penyebab
- file/function
- data source
- guard
- side effect
- file terdampak
- test yang diperlukan
- uncertainty

Jangan implementasi pada tahap audit.

Stop setelah laporan.

## 5. IMPLEMENTATION MODE

Setelah audit cukup:
langsung implement scope minimum.

Prompt/task harus berisi:
- tujuan
- business rule exact
- expected files
- frozen areas penting
- focused tests
- stop gate

Jangan copy seluruh laporan audit kembali.

## 6. TEST POLICY

Jalankan hanya:
- test kasus yang berubah;
- regresi langsung;
- test business guard terkait.

Jangan ulang test yang sudah PASS jika:
- source terkait tidak berubah;
- input build tidak berubah;
- tidak ada failure/uncertainty baru.

Frontend build:
- jalankan jika frontend/build input berubah;
- jangan ulang jika frontend source sama dan build sebelumnya masih valid.

Backend:
- focused tests dulu;
- broad suite hanya jika ada bukti scope meluas.

## 7. REPORT POLICY

Laporan default harus singkat.

Format:

BASE:
BRANCH:
HEAD:

CHANGED:
- ...

TEST:
- ...

BUILD:
PASS / NOT NEEDED

RISK:
- ...

STATUS:
COMMIT:
PUSH:
PR:
MERGE:
DEPLOY:

Jangan membuat laporan panjang kecuali:
- accounting;
- stock;
- invoice vs PO;
- concurrency;
- shipment integrity;
- migration;
- permission/security;
- owner meminta detail.

## 8. COMPLEX TASK ESCALATION

Keluar dari fast mode dan gunakan detail lebih tinggi jika task menyentuh:

- accounting / journal
- payment / receipt
- stock fisik
- HPP
- invoice vs PO reconciliation
- shipment integrity
- concurrency / race condition
- permission/security
- migration/backfill
- multi-module business logic

Fast mode tidak boleh mengorbankan correctness.

## 9. OWNER FALLBACK RULE

Jika owner mengatakan:

- kurang presisi
- kurang detail
- terlalu singkat
- kembalikan seperti sebelumnya
- pakai versi lengkap
- hasilnya membingungkan
- hasilnya menyebabkan rework

maka segera pindah ke:

DETAILED MODE

untuk task tersebut.

Dalam Detailed Mode:
- audit lebih lengkap;
- jelaskan source-of-truth;
- edge case;
- side effect;
- file impact;
- test matrix;
- report lebih detail.

Tetap gunakan Detailed Mode sampai owner menyatakan fast mode boleh dipakai lagi.

## 10. BUSINESS SAFETY

Jangan pernah mengorbankan:

- aturan bisnis Eternal
- data integrity
- backend authority
- test evidence
- exact HEAD merge gate
- CI
- deploy safety
- production isolation

Hemat token berarti:
mengurangi pekerjaan berulang,
BUKAN mengurangi verifikasi penting.

## 11. MERGE / PR

Finalisasi PR harus ringkas:

- fetch latest main
- race-check
- semantic overlap
- diff review
- commit
- push
- PR
- CI exact HEAD
- stop

Jangan audit ulang seluruh fitur saat finalisasi.

## 12. DEPLOY

Deploy tetap manual.

Jangan deploy otomatis.

Gunakan exact approved merge SHA.

Jangan build ulang artifact yang sudah valid pada input sama.

Jika build terlihat hang:
cek proses aktual + artifact + health dulu.
Jangan ulang build secara buta.

## DEFAULT TASK TEMPLATE

Gunakan template ini bila task sudah cukup jelas:

MAIN AGENT ONLY.
NO SUBAGENT.
NO VIDEO.
NO PROD DATA.

TUGAS
[tujuan singkat]

BUSINESS RULE
- [rule 1]
- [rule 2]

SCOPE
- [file/modul]

JANGAN UBAH
- [modul terkait risiko langsung]

TES
- [focused case]
- [direct regression]
- build hanya jika perlu
- git diff --check

STOP
No commit/push/PR/merge/deploy.

LAPORAN
changed files, test result, risk, status.
