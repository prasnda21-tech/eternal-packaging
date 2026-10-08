import fs from "fs";
import path from "path";

describe("Gaji Operator page", () => {
  const src = fs.readFileSync(path.join(__dirname, "GajiOperator.jsx"), "utf8");
  const ms = fs.readFileSync(path.join(__dirname, "..", "components", "OperatorMultiSelect.jsx"), "utf8");

  test("has date filter export rate columns and checkbox multi-select", () => {
    expect(src).toMatch(/gaji-filter-from/);
    expect(src).toMatch(/gaji-filter-to/);
    expect(src).toMatch(/gaji-export/);
    expect(src).toMatch(/Upah Eligible/);
    expect(src).toMatch(/Tambahan Produk/);
    expect(src).toMatch(/Bonus Usulan/);
    expect(src).toMatch(/Total Payroll/);
    expect(src).toMatch(/Estimasi\/Usulan Total/);
    expect(src).toMatch(/gaji-bonus-usulan-/);
    expect(src).toMatch(/gaji-bonus-final-/);
    expect(src).toMatch(/gaji-total-payroll-/);
    expect(src).toMatch(/gaji-estimasi-total-/);
    expect(src).toMatch(/OperatorMultiSelect/);
    expect(src).toMatch(/operator_ids/);
    expect(src).toMatch(/gaji-unset-warning/);
    expect(src).toMatch(/Siap Payroll/);
    expect(src).toMatch(/Dalam Proses/);
    expect(src).toMatch(/Sudah Payroll/);
    expect(src).toMatch(/Perlu Perbaikan/);
    expect(src).toMatch(/payroll_reason/);
    expect(src).toMatch(/gaji-bonus-note/);
    expect(src).toMatch(/gaji-siapkan-payroll/);
    expect(src).toMatch(/hasPerm\("payroll.piece_holding.edit"\)/);
    expect(src).toMatch(/hrPayrollPreparePieceFromGaji/);
    expect(src).toMatch(/date_from: from/);
    expect(src).toMatch(/date_to: to/);
    expect(src).toMatch(/\/hr-payroll\/penggajian\?period=/);
    expect(src).toMatch(/gaji-eligibility-summary/);
    expect(src).toMatch(/eligibility_summary/);
    expect(src).toMatch(/SIAP PAYROLL/);
    expect(src).toMatch(/DALAM PROSES/);
    expect(src).toMatch(/SUDAH PAYROLL/);
    expect(src).toMatch(/PERLU PERBAIKAN/);
    expect(src).not.toMatch(/<select value=\{operatorId\}/);
  });

  test("compact dropdown keeps checkboxes search select-all apply clear", () => {
    expect(ms).toMatch(/gaji-filter-operator-all/);
    expect(ms).toMatch(/type="checkbox"/);
    expect(ms).toMatch(/gaji-filter-operator-search/);
    expect(ms).toMatch(/gaji-filter-operator-trigger/);
    expect(ms).toMatch(/Pilih Semua Operator/);
    expect(ms).toMatch(/Bersihkan/);
    expect(ms).toMatch(/Terapkan/);
    expect(ms).toMatch(/indeterminate/);
    expect(ms).toMatch(/nextSelectAll\(draft, allIds\)/);
    expect(ms).toMatch(/searchOperators\(operators, query\)/);
  });

  test("product filter is live searchable facet select not native scroll select", () => {
    expect(src).toMatch(/FacetSearchSelect/);
    expect(src).toMatch(/gaji-filter-item/);
    expect(src).toMatch(/gajiFilterItemValue/);
    expect(src).not.toMatch(/<select value=\{item\}/);
    expect(src).toMatch(/item_code \|\| it\.item_name/);
  });

  test("siapkan payroll uses report range and keeps existing filters", () => {
    const api = fs.readFileSync(path.join(__dirname, "..", "lib", "api.js"), "utf8");
    expect(api).toMatch(/hrPayrollPreparePieceFromGaji:/);
    expect(api).toMatch(/piece-periods\/from-gaji-operator/);
    expect(src).toMatch(/gaji-filter-from/);
    expect(src).toMatch(/gaji-export/);
    expect(src).toMatch(/gajiPrepared: res.reused \? "existing" : "new"/);
  });

  test("compact layout emphasizes siapkan payroll kpi status and grouped not eligible", () => {
    expect(src).toMatch(/className="primary gaji-simple-primary"/);
    expect(src).toMatch(/gaji-siapkan-payroll/);
    expect(src).toMatch(/gaji-kpi/);
    expect(src).toMatch(/Upah Eligible/);
    expect(src).toMatch(/bonusUsulan \? "Bonus Usulan" : "Bonus"/);
    expect(src).toMatch(/gaji-status-tab-\$\{id\}/);
    expect(src).toMatch(/\["BELUM_MASUK_PAYROLL", "Siap Payroll"\]/);
    expect(src).toMatch(/\["HELD", "Dalam Proses"\]/);
    expect(src).toMatch(/\["POSTED", "Sudah Payroll"\]/);
    expect(src).toMatch(/\["NOT_ELIGIBLE", "Perlu Perbaikan"\]/);
    expect(src).toMatch(/filteredPeople/);
    expect(src).toMatch(/payroll_status \|\| ""\) === statusTab/);
    expect(src).toMatch(/gaji-elig-detail/);
    expect(src).toMatch(/Perbaiki Tanggal Kerja/);
    expect(src).toMatch(/Perbaiki Snapshot Tarif/);
    expect(src).toMatch(/Buka Profil HR/);
    expect(src).toMatch(/Lihat Data Historis/);
    expect(src).toMatch(/gaji-repair-drawer/);
    expect(src).toMatch(/reports\.gaji_operator\.repair_work_date/);
    expect(src).toMatch(/reports\.gaji_operator\.repair_rate/);
    expect(src).toMatch(/gaji-person-toggle-/);
    expect(src).toMatch(/gaji-export/);
    expect(src).toMatch(/Sistem Upah/);
    expect(src).toMatch(/gaji-open-wage-settings/);
    expect(src).toMatch(/\/hr-payroll\/penggajian\?period=/);
    expect(src).toMatch(/gaji-period-label/);
    expect(src).toMatch(/formatTrackingPageDate/);
    expect(src).toMatch(/gaji-elig-line/);
    expect(src).toMatch(/is-compact/);
    expect(src).toMatch(/gaji-simple-period-head/);
    expect(src).toMatch(/gaji-simple-table-wrap/);
  });

  test("shows separate payroll and payment visibility without widening columns", () => {
    expect(src).toMatch(/gaji-status-stack/);
    expect(src).toMatch(/Pembayaran/);
    expect(src).toMatch(/Belum Dibayar/);
    expect(src).toMatch(/Sudah Dibayar/);
    expect(src).toMatch(/Menunggu Persetujuan/);
    expect(src).toMatch(/Siap Diposting/);
    expect(src).toMatch(/p\.payment_status/);
    expect(src).toMatch(/gaji-payment-summary/);
    expect(src).toMatch(/Sudah Dibayar: \{kpis\.sudah_dibayar/);
    expect(src).toMatch(/Belum Dibayar: \{kpis\.belum_dibayar/);
    expect(src).toMatch(/>Status</);
    expect(src).not.toMatch(/<th>Payroll<\/th>/);
    expect(src).not.toMatch(/<th>Pembayaran<\/th>/);
  });
});
