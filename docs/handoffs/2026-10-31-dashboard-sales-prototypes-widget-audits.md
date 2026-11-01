# Handoff — Sales dashboard batch + production widget audits + visual prototypes

**Tanggal (Asia/Jakarta):** 2026-10-31
**Branch kerja:** `cursor/sales-omzet-branch-scope-0694`
**Parent / baseline sebelum commit handoff:** `fb90599950f3844f30cb2c8da8b58e4bfe0dfa55` (`main` tip saat cabang ini)
**Tujuan dokumen:** handoff ke ChatGPT untuk audit gabungan dan implementasi berikutnya.
**Bukan:** deploy, merge, atau klaim dashboard selesai.

Prototype **hanya referensi visual**, belum diintegrasikan ke widget operasional.
Dokumen ini **tidak** menyimpan token, cookie, kredensial, atau session preview.

---

## 1. Cakupan batch di branch ini

### A. Operasional — Penjualan / omzet / scope cabang

- Omzet dan rincian omzet memakai populasi yang sama: faktur finalized, tidak dibatalkan, periode, plus **branch clause** (`operational_query`) AND **sales ownership**.
- Agregasi omzet menjumlah `after_discount` tanpa memuat seluruh set; nilai tidak terbaca → status tidak lengkap + detail jujur.
- Scope kosong untuk peran global (Direktur/Holding); user non-global mendapat clause cabang.

**File penting**
- `backend/services/sales_dashboard.py`
- `backend/tests/test_sales_dashboard.py`
- `backend/tests/test_sales_dashboard_branch_scope.py` (baru)
- `backend/tests/test_widget_dashboard_reads.py`

### B. Operasional — Pelunasan kartu ↔ rincian

- Widget/rincian `settlement` memakai `aggregate_settlement` (basis `grand_total` / status lunas), **bukan** basis omzet `after_discount`.
- Pagination membatasi baris; total KPI dihitung dari populasi penuh sebelum page.
- Periode eksplisit pada rincian mengikuti kartu; “hari ini WIB” hanya default jika widget today tanpa periode.

**File penting**
- `backend/services/dashboard_rincian.py`
- `backend/services/dashboard_rincian_http.py`
- `backend/services/sales_dashboard.py` (`aggregate_settlement`, `_settlement_page_row`)

### C. Operasional — Tampilan tanggal `dd/mm/yyyy`

- Input periode penjualan/rincian memakai `SalesDashboardDateInput` (teks `dd/mm/yyyy` + kalender tersembunyi), bukan native date ISO di UI.
- Nilai API tetap ISO `yyyy-mm-dd`.

**File penting**
- `frontend/src/components/widgetDashboard/SalesDashboardDateInput.jsx` (+ `.css`, `.test.js`)
- `frontend/src/pages/WidgetDashboard.jsx`
- `frontend/src/pages/DashboardRincianPage.jsx` (+ `.css`)
- `frontend/src/lib/dashboardRincianPresentation.js` (+ test)
- `frontend/src/components/widgetDashboard/widgetDashboard.css`

### D. Prototype visual (dev-only)

| Preview | Route |
|---|---|
| Dashboard gabungan Keuangan + Produksi | `/dev/dashboard-concept-preview` |
| Kartu PO Aktif | `/dev/po-aktif-card-preview` |
| Kartu PO Perlu Produksi | `/dev/po-perlu-produksi-card-preview` |

**File penting**
- `frontend/src/prototypes/dashboardConcept/*`
- `frontend/src/prototypes/poAktifCard/*`
- `frontend/src/prototypes/poPerluProduksiCard/*`
- Route registration: `frontend/src/App.js`

Fixture lokal saja. Tidak ada kredensial. Tidak mengubah widget produksi live.

### E. Audit read-only widget Produksi (belum implementasi perbaikan)

Diselesaikan di sesi yang sama, laporan di chat (tidak ada patch perbaikan adapter pada batch ini):

1. **PO Aktif** (`po_aktif`)
2. **PO Perlu Produksi** (`perlu_produksi`)
3. **Sisa Qty Produksi** (`sisa_qty`)

---

## 2. Ringkasan audit tiga widget (bukti source)

| Widget ID | Metric kartu | Endpoint | Agregasi | Drill / rincian |
|---|---|---|---|---|
| `po_aktif` | count PO unik active (belum fully delivered) | `GET /dashboard/produksi/snapshot` | `partition_orders_by_delivery` | Status PO perms (`nav.reports.status_po` / `schedule.view`) |
| `perlu_produksi` | count PO unik dengan flag `perlu-produksi` | snapshot → Kepala board | `(not fully) ∧ qty_production_remaining > 0` | Kepala OR: `nav.tasks.kepala_produksi` \| `schedule.edit` |
| `sisa_qty` | **qty per unit** (`by_unit`), bukan count PO | snapshot → `kpis["sisa-produksi"]` | Σ `qty_production_remaining` per `line_unit` | KPI key `sisa-produksi`; grain line; perm sama Kepala OR |

**Rumus sisa (terbukti):**
`qty_production_remaining = max(0, printable − print_completed_for_line)`
= **sisa cetak**, memakai `operator_splits[].qty_completed` per `line_idx` (split tanpa `line_idx` diabaikan). Bukan sisa jadwal, bukan sisa kirim.

**Populasi Perlu vs Sisa:** flag line **identik**; Perlu = unique PO; Sisa = sum qty by unit.

**Unit:** dari `line.unit` / `uom`, strip, default `"pcs"`; **tidak** case-normalize; **tidak** konversi; satuan beda **tidak** digabung. FE menampilkan semua entry `byUnit` (tidak memotong di compact).

---

## 3. Temuan belum diperbaiki (prioritas untuk ChatGPT)

### Adapter rincian Kepala (tinggi) — terdampak `perlu_produksi`, `sisa_qty`, `prioritas`

Board rows memakai field: `order_number`, `item`, `qty_production_remaining`, `effective_delivery_date`, `customer_name`.
Adapter `dashboard_rincian.py` (source `kepala`) memetakan ke: `po_number`, `item_name`, `qty_remaining`, `tanggal_kirim` — **mismatch**.
Akibat: kolom No PO / Item / Belum Diproduksi / Tanggal Kirim kosong atau salah di UI rincian runtime (jangan pakai mock FE sebagai bukti field tersedia).

### Agregasi / rincian per unit (tinggi) — `sisa_qty`

- Kartu: `by_unit`.
- Rincian: `kpi_summary` hanya unique PO + `line_count`; **tidak** mengembalikan total qty per unit.
- Label FE `sisa_qty: "Total PO Sisa Qty"` menyesatkan (PO count, bukan qty).
- Klik body kartu **tidak** memfilter unit.

### Qty / unit edge (sedang–tinggi)

- `pcs` vs `PCS` tidak dinormalisasi → risiko pecah bucket.
- Belum approval **tanpa** HOLD tidak masuk board Kepala → undercount mungkin vs ekspektasi bisnis.
- Over-production di-clamp ke 0 pada sisa (over terpisah di `qty_production_over`).
- Desimal string gagal `_safe_int` → 0.

### Lain

- Inventaris katalog: **25** widget Produksi + **31** Keuangan (ready + pending). Audit lengkap widget-by-widget **masih harus dilanjutkan** setelah ketiga di atas.
- Prototype gabungan / PO Aktif / PO Perlu = referensi visual saja.

---

## 4. Inventaris referensi katalog

### Produksi (25)

`po_aktif`, `perlu_produksi`, `sisa_qty`, `po_urgent`, `po_hold`, `deadline`, `approval`, `prioritas`, `output_hari_ini`, `tren_output`, `distribusi_tahap`, `output_operator`, `beban_operator`, `kesiapan`, `target_produksi`, `po_terlambat`, `target_realisasi`, `aging`, `ketepatan`, `bottleneck`, `komposisi`, `output_cabang`, `delivery_qty`, `qc_reject`, `utilisasi_mesin`

Sumber: `frontend/src/components/productionDashboard/productionWidgetCatalog.js`

### Keuangan (31)

Ready: `cash`, `expense`, `profit`, `purchases`, `recon`, `sales`, `products`, `sellers`, `trend`
Pending: `assets`, `overdue_invoices`, `cash_available`, `branch_profit`, `customers_owing`, `quote_win`, `quote_stale`, `sales_category`, `sales_brand`, `sales_daily`, `customer_growth`, `sales_orders`, `min_stock`, `hpp`, `item_requests`, `purchase_orders`, `my_activity`, `upcoming`, `missed`, `active_users`, `approvals`, `approvals_done`

Sumber: `frontend/src/components/financeDashboard/financeWidgetCatalog.js`

---

## 5. Verifikasi yang benar-benar dijalankan (sesi sebelumnya pada working tree ini)

Tidak mengklaim tes baru pada putaran handoff ini. Source batch di bawah tidak diubah lagi setelah verifikasi tersebut, selain penambahan dokumen handoff ini.

| Area | Yang dijalankan | Baseline |
|---|---|---|
| Sales omzet / branch scope / settlement rincian | Focused pytest terkait `test_sales_dashboard*`, `test_widget_dashboard_reads` (sesi implementasi) | Working tree di atas `fb905999…` |
| Date UI `dd/mm/yyyy` | Manual UI + artefak lokal (`/opt/cursor/artifacts/date-ui-*`, `*-ddmmyyyy*`); Jest `SalesDashboardDateInput.test.js` | sama |
| Prototype dashboard concept | Jest fixtures + preview route | sama |
| Prototype PO Aktif | Jest preview + `yarn build` OK saat itu; geometry footer | sama |
| Prototype PO Perlu Produksi | Jest 6/6 + `yarn build` OK; geometry footOk 1366/1440/zoom125 | sama |
| Audit Sisa Qty | **Read-only**; tes terkait **dibaca**, **tidak dijalankan** | `fb905999…` + source board/snapshot |

**Putaran handoff ini:** `git diff --check` bersih pada seluruh file batch; tidak menjalankan suite DB-write; tidak build ulang.

Artefak screenshot/log verifikasi ada di environment agent (`/opt/cursor/artifacts/`) dan **tidak** di-commit ke repo.

---

## 6. CI / safety untuk handoff PR

- Workflow yang bereaksi ke `pull_request` → `main`: `.github/workflows/reconciliation-validation.yml` (tidak punya gate `draft:`).
- Deploy workflows hanya `workflow_dispatch`.
- Owner: CI belum diizinkan. Commit handoff memakai markah **`[skip ci]`** (mekanisme skip native GitHub Actions untuk event `push` / `pull_request`) **tanpa** mengubah file workflow.
- Draft PR **sendiri tidak** menonaktifkan CI.

---

## 7. Yang sengaja tidak dilakukan

- Tidak merge ke `main`, tidak merge PR, tidak deploy, tidak prod write / production data.
- Tidak memperbaiki adapter rincian / widget berikutnya pada batch ini.
- Tidak mengintegrasikan prototype ke dashboard live.
- Tidak `reset` / `clean` / `stash`; seluruh pekerjaan sesi dipertahankan di branch.

---

## 8. Lanjut yang diharapkan ChatGPT

1. Pakai branch ini sebagai basis audit gabungan + perbaikan bertahap.
2. Prioritas perbaikan: adapter Kepala rincian + rekonsiliasi `sisa_qty` by_unit pada rincian.
3. Lanjutkan audit widget Produksi/Keuangan sisanya satu per satu; prototype hanya setelah audit widget tersebut.
4. Jangan anggap inventaris katalog = audit selesai.
