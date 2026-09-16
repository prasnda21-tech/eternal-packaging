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
      "Nama Barang",
      "Stock Sistem",
      "Stock Fisik Hasil Opname",
      "Selisih",
      "Status",
    ]) {
      expect(src).toMatch(new RegExp(`<th[^>]*>${label}</th>`));
    }
    expect(src).toMatch(/batch_request_id: preview\.batch_request_id/);
  });
});
