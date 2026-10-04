import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "StockOpname.jsx"), "utf8");

describe("Stock Opname XLSX workflow", () => {
  test("uses backend capability and only exposes export/import workflow", () => {
    expect(src).toMatch(/can_apply_stock_opname/);
    expect(src).toMatch(/stock-opname-export-template/);
    expect(src).toMatch(/stock-opname-import-button/);
    expect(src).not.toMatch(/stock-opname-unprinted|stock-opname-printed|stock-opname-save/);
    expect(src).toMatch(/accept="\.xlsx,application\/vnd\.openxmlformats-officedocument\.spreadsheetml\.sheet"/);
    expect(src).toMatch(/stock-opname-template-\$\{new Date\(\)\.toISOString\(\)\.slice\(0, 10\)\}\.xlsx/);
  });

  test("preview shows the required columns and sends the idempotency key", () => {
    for (const label of [
      "Kode",
      "Nama",
      "Stock Fisik Sistem",
      "Stock Fisik Hasil Hitung",
      "Selisih Qty",
      "Belum Terkirim",
      "Stock Dapat Dijual Setelah Opname",
      "Aksi",
      "Status",
    ]) {
      expect(src).toMatch(new RegExp(`<th[^>]*>${label}</th>`));
    }
    expect(src).toMatch(/batch_request_id: preview\.batch_request_id/);
  });

  test("uses the physical-count backend contract without legacy available-stock fields", () => {
    expect(src).toMatch(/stock_physical_before/);
    expect(src).toMatch(/stock_physical_counted/);
    expect(src).toMatch(/difference_qty/);
    expect(src).toMatch(/outstanding_so_qty/);
    expect(src).toMatch(/stock_sellable_after/);
    expect(src).toMatch(/fmtQty\(it\.physical_qty\)/);
    expect(src).toMatch(/Qty pesanan \/ Belum Terkirim tidak perlu/);
    expect(src).not.toMatch(/stock_current_system|stock_current_opname|r\.selisih/);
  });
});
