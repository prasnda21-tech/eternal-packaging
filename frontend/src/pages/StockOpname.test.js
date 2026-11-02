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
    expect(src).toMatch(/Download Excel/);
    expect(src).toMatch(/Import Excel/);
    expect(src).toMatch(/data-testid="opname-cutoff"/);
    expect(src).toMatch(/Mulai Cutoff/);
    expect(src).toMatch(/stockOpnameCountImportPreview/);
    expect(src).toMatch(/data-testid="opname-import-preview"/);
    expect(src).toMatch(/data-testid="opname-import-apply"/);
    expect(src).toMatch(/Terapkan READY/);
    expect(src).toMatch(/data-testid="opname-review"/);
    expect(src).toMatch(/>Review</);
    expect(src).toMatch(/Konfirmasi akan menyesuaikan Stock Fisik sesuai hasil hitung/);
    expect(src).toMatch(/Konfirmasi Stock Opname/);
    expect(src).toMatch(/data-testid="opname-cancel"/);
    expect(src).toMatch(/>Batalkan</);
    expect(src).toMatch(/data-testid="opname-history"/);
    expect(src).toMatch(/stock-opname-summary/);
    expect(src).toMatch(/stock-opname-toolbar/);
    expect(src).toMatch(/stock-opname-table-wrap/);
    for (const label of [
      "Kode",
      "Nama Barang",
      "Satuan Dasar",
      "Stock Buku",
      "Qty Pesanan",
      "Hasil Hitung Fisik",
      "Qty Tersedia Setelah Opname",
      "Status Hitung",
    ]) {
      expect(src).toMatch(new RegExp(`<th>${label}</th>`));
    }
    expect(src).toMatch(/Qty Siap Belum Terkirim/);
    expect(src).toMatch(/opname-belum-note/);
    expect(src).toMatch(/bukan hitungan fisik gudang/);
    expect(src).toMatch(/Stock Fisik − Stock Buku/);
    expect(src).toMatch(/Selisih/);
    expect(src).toMatch(/Adjustment \(target − buku\)/);
    expect(src).toMatch(/Saldo Buku Sekarang/);
    expect(src).toMatch(/Net Mutasi Setelah Hitung/);
    expect(src).toMatch(/Target Saldo Sekarang/);
  });

  test("create payload carries explicit mode freeze|operational and defaults freeze", () => {
    expect(src).toMatch(/data-testid="opname-create-mode"/);
    expect(src).toMatch(/useState\("freeze"\)/);
    expect(src).toMatch(/physical_count_period_start_at/);
    expect(src).toMatch(/physical_count_period_end_at/);
    expect(src).toMatch(/default_physical_count_at/);
    expect(src).toMatch(/opname-create-start-date/);
    expect(src).toMatch(/opname-create-end-time/);
    expect(src).toMatch(/Tanggal Mulai/);
    expect(src).toMatch(/Jam Akhir/);
    expect(src).toMatch(/Hasil fisik dianggap posisi pada waktu akhir/);
    expect(src).toMatch(/Date\.now\(\) \+ 60_000/);
    expect(src).toMatch(/toleransi jam/);
    expect(src).toMatch(/Never silently change mode/);
    expect(src).toMatch(/operationalCreate \? "operational" : "freeze"/);
    expect(src).toMatch(/formatWibPeriod/);
    expect(src).toMatch(/opname-history-period/);
  });

  test("count payload separates physical_count_qty, physical_count_at, and imported_at", () => {
    expect(src).toMatch(/physical_count_qty/);
    expect(src).toMatch(/physical_count_at/);
    expect(src).toMatch(/imported_at/);
    expect(src).toMatch(/buildStockOpnameCountPayload/);
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
    expect(src).toMatch(/confirmInFlightRef/);
    expect(src).toMatch(/if \(confirmInFlightRef\.current \|\| busy \|\| session\?\.status === "CONFIRMED"\) return/);
    expect(src).toMatch(/confirmed\?\.status !== "CONFIRMED"/);
    expect(src).toMatch(/Tidak menampilkan sukses/);
    expect(src).toMatch(/disabled=\{busy \|\| session\.status === "CONFIRMED"\}/);
    expect(src).toMatch(/Do not auto-retry posting/);
    expect(src).toMatch(/sessionStatusLabel/);
  });

  test("review flushes count drafts and datetime-local updates timeDrafts", () => {
    expect(src).toMatch(/buildStockOpnameCountPayload/);
    expect(src).toMatch(/Flush drafts first so physical_count_at/);
    expect(src).toMatch(/onInput=\{\(event\) => setTimeDrafts/);
    expect(src).toMatch(/onInput=\{\(event\) => setBulkCountAt/);
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
    expect(stockOpnameErrorMessage({
      response: { status: 409, data: { detail: "Konfirmasi stock opname gagal karena perubahan bersamaan." } },
    }, { sessionStatusLabel: "Review" }))
      .toMatch(/Status sesi saat ini: Review/);
    expect(stockOpnameErrorMessage({
      response: { status: 409, data: { detail: "Konfirmasi stock opname gagal karena perubahan bersamaan." } },
    })).toMatch(/kunci sementara|Konfirmasi lain/i);
    expect(stockOpnameErrorMessage({ response: { status: 503, data: { detail: "Transaksi tidak tersedia" } } }))
      .toMatch(/R2/);
    expect(stockOpnameErrorMessage({ response: { status: 400, data: { detail: "r2 belum diaktifkan" } } }))
      .toMatch(/R2|r2/);
  });
});
