import fs from "fs";
import path from "path";
import { stockOpnameErrorMessage } from "../lib/stockOpnameErrors";

const src = fs.readFileSync(path.join(__dirname, "StockOpname.jsx"), "utf8");

describe("Stock Opname operational workflow", () => {
  test("opens from stock, limits warehouses, and keeps the count lifecycle", () => {
    expect(src).toMatch(/opnameWarehouseChoices/);
    expect(src).toMatch(/warehouseLocationLabel/);
    expect(src).not.toMatch(/operationalOpeningWarehouses/);
    expect(src).toMatch(/Tidak ada gudang operasional aktif/);
    expect(src).toMatch(/Prefer warehouse_id from Stock Saat Ini deep-link/);
    expect(src).toMatch(/only auto-select when exactly one warehouse/);
    expect(src).toMatch(/useSearchParams/);
    expect(src).toMatch(/requestedWarehouseId/);
    expect(src).toMatch(/opnameLocationLabel/);
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
      "Qty Tersedia Setelah Opname",
      "Status Hitung",
    ]) {
      expect(src).toMatch(new RegExp(`<th>${label}</th>`));
    }
    expect(src).toMatch(/Selisih/);
    expect(src).toMatch(/Adjustment \(perkiraan\)/);
  });

  test("create payload carries explicit mode freeze|operational and defaults freeze", () => {
    expect(src).toMatch(/data-testid="opname-create-mode"/);
    expect(src).toMatch(/createMode === "operational" \? "operational" : "freeze"/);
    expect(src).toMatch(/useState\("freeze"\)/);
    expect(src).toMatch(/mode: createMode === "operational" \? "operational" : "freeze"/);
    expect(src).toMatch(/Never silently change mode/);
  });

  test("count payload separates physical_count_qty, physical_count_at, and imported_at", () => {
    expect(src).toMatch(/physical_count_qty/);
    expect(src).toMatch(/physical_count_at/);
    expect(src).toMatch(/imported_at/);
    expect(src).toMatch(/wibLocalInputToIso/);
    expect(src).toMatch(/isoToWibLocalInput/);
    expect(src).toMatch(/STOCK_OPNAME_TZ_LABEL/);
    expect(src).toMatch(/data-testid="opname-apply-bulk-time"/);
    expect(src).toMatch(/pilihan eksplisit/);
    expect(src).toMatch(/never treat import time as count time/i);
    expect(src).toMatch(/Kosong bukan nol|kosong ≠ 0/);
  });

  test("operational review shows API book/net/target/adjustment fields", () => {
    expect(src).toMatch(/book_qty_live/);
    expect(src).toMatch(/net_movement_after_count/);
    expect(src).toMatch(/target_qty/);
    expect(src).toMatch(/data-testid=\{`opname-net-\$\{line\.item_code\}`\}/);
    expect(src).toMatch(/data-testid=\{`opname-target-\$\{line\.item_code\}`\}/);
    expect(src).toMatch(/diverifikasi ulang saat konfirmasi/);
    expect(src).toMatch(/data-testid="opname-operational-banner"/);
    expect(src).toMatch(/data-testid="opname-freeze"/);
  });

  test("confirm guards double-submit and only shows success after CONFIRMED", () => {
    expect(src).toMatch(/if \(busy \|\| session\?\.status === "CONFIRMED"\) return/);
    expect(src).toMatch(/confirmed\?\.status !== "CONFIRMED"/);
    expect(src).toMatch(/Tidak menampilkan sukses/);
    expect(src).toMatch(/disabled=\{busy \|\| session\.status === "CONFIRMED"\}/);
  });
});

describe("stockOpnameErrorMessage", () => {
  test("maps 400/409/503 without inventing retries", () => {
    expect(stockOpnameErrorMessage({ response: { status: 400, data: { detail: "Hitungan kosong" } } }))
      .toMatch(/Perbaiki input/);
    expect(stockOpnameErrorMessage({ response: { status: 409, data: { detail: "Backdated" } } }))
      .toMatch(/review ulang/);
    expect(stockOpnameErrorMessage({ response: { status: 409, data: { detail: "Backdated" } } }))
      .not.toMatch(/otomatis retry posting/i);
    expect(stockOpnameErrorMessage({ response: { status: 503, data: { detail: "Transaksi tidak tersedia" } } }))
      .toMatch(/R2/);
    expect(stockOpnameErrorMessage({ response: { status: 400, data: { detail: "r2 belum diaktifkan" } } }))
      .toMatch(/R2|r2/);
  });
});
