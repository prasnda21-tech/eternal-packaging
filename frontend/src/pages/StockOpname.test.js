import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "StockOpname.jsx"), "utf8");

describe("Stock Opname operational workflow", () => {
  test("opens from stock, limits warehouses, and keeps the count lifecycle", () => {
    expect(src).toMatch(/operationalOpeningWarehouses/);
    expect(src).toMatch(/data-testid="opname-start"/);
    expect(src).toMatch(/Mulai Stock Opname/);
    expect(src).toMatch(/Download Excel Hitung Fisik/);
    expect(src).toMatch(/Import Excel Hitung Fisik/);
    expect(src).toMatch(/Review Stock Opname/);
    expect(src).toMatch(/Konfirmasi akan menyesuaikan Stock Fisik sesuai hasil hitung/);
    expect(src).toMatch(/Konfirmasi Stock Opname/);
    expect(src).toMatch(/Batalkan Stock Opname/);
    expect(src).toMatch(/data-testid="opname-history"/);
    for (const label of [
      "Kode",
      "Nama Barang",
      "Satuan Dasar",
      "Stock Buku",
      "Qty Pesanan",
      "Qty Belum Terkirim",
      "Stock Fisik",
      "Selisih",
      "Qty Tersedia Setelah Opname",
      "Status Hitung",
    ]) {
      expect(src).toMatch(new RegExp(`<th>${label}</th>`));
    }
  });
});
