import fs from "fs";
import path from "path";
import { formatRupiah } from "../lib/purchaseUi";

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

import { estimateCoverage, openingHistoryCells } from "./NilaiPersediaan";

const source = fs.readFileSync(path.join(__dirname, "NilaiPersediaan.jsx"), "utf8");

describe("Nilai Persediaan read-only page", () => {
  test("formats the approved moving-average fixture in Indonesian Rupiah", () => {
    expect(formatRupiah(1066.67)).toBe("Rp 1.066,67");
    expect(formatRupiah(160000)).toBe("Rp 160.000");
  });

  test("renders canonical API values and all required statuses", () => {
    expect(source).toMatch(/Stok Fisik/);
    expect(source).toMatch(/Harga Pokok/);
    expect(source).not.toMatch(/HPP Rata-Rata/);
    expect(source).toMatch(/Nilai Estimasi/);
    [
      "OK",
      "MISSING_COST",
      "NEEDS_COST",
      "NEEDS_UOM",
      "QTY_MISMATCH",
      "FAILED",
    ].forEach((status) => expect(source).toContain(status));
    expect(source).toMatch(/item\.current_hpp != null/);
    expect(source).toMatch(/item\.inventory_value != null/);
    expect(source).toMatch(/hasHpp \? formatRupiah\(item\.current_hpp\) : "Belum Ada HPP"/);
    expect(source).toMatch(/hasValue \? formatRupiah\(item\.inventory_value\) : "—"/);
    expect(source).toMatch(/STOCK_STATE_UNAVAILABLE/);
    expect(source).toMatch(/Tidak tersedia/);
  });

  test("contains no stock, purchasing, or customer-pricing write calls", () => {
    expect(source).not.toMatch(/currentStockUpdate|createPurchase|updatePurchase|publishCustomerPricing/);
    expect(source).not.toMatch(/manual_hpp|hpp_snapshot|tier_prices/);
    expect(source).toMatch(/inventory_valuation\.export/);
    expect(source).not.toMatch(/current_stock\.(view|export)/);
  });

  test("keeps opening explicit and separates estimate from book value", () => {
    expect(source).not.toMatch(/Atur HPP Awal/);
    expect(source).not.toMatch(/inventoryValuationOpeningPreview/);
    expect(source).not.toMatch(/confirmInventoryValuationOpening/);
    expect(source).not.toMatch(/Konfirmasi HPP Awal/);
    expect(source).not.toMatch(/window\.confirm/);
    expect(source).toMatch(/Valuasi Published \(bukan saldo buku\)/);
    expect(source).toMatch(/Posting saldo awal/);
    expect(source).toMatch(/bukan saldo buku posted/);
    expect(source).not.toMatch(/HPP Rata-Rata \/ OK/);
    expect(source).toMatch(/Daftar Barang/);
    expect(source).toMatch(/Saldo Awal/);
    expect(source).toMatch(/HPP Penjualan/);
    expect(source).toMatch(/Rekonsiliasi & Laporan/);
    expect(source).toMatch(/MasterItemSearchSelect/);
    expect(source).toMatch(/exactMasterItemId/);
    expect(source).toMatch(/Ambil snapshot saldo sekarang/);
    expect(source).toMatch(/Periksa saldo awal/);
    expect(source).toMatch(/Belum lengkap/);
    expect(source).toMatch(/Perlu diselesaikan/);
    expect(source).toMatch(/Cocok/);
    expect(source).toMatch(/Tidak masuk periode/);
    expect(source).toMatch(/Belum dapat dihitung/);
    expect(source).toMatch(/Estimasi sebagian/);
    expect(source).toMatch(/Kelengkapan nilai belum diketahui/);
  });

  test("classifies estimate coverage from summary metadata, not a zero total", () => {
    const base = { total_inventory_value: 0, valued_items: 0, items_without_hpp: 1, problem_items: 0 };
    expect(estimateCoverage({ summary: base, total: 1, loading: false, failed: false, filterActive: false }).value).toBe("Belum dapat dihitung");
    expect(estimateCoverage({
      summary: { total_inventory_value: 500000, valued_items: 1, items_without_hpp: 2, problem_items: 1 },
      total: 4,
      loading: false,
      failed: false,
      filterActive: false,
    })).toMatchObject({ state: "partial", label: "Estimasi sebagian", value: "Rp 500.000" });
    expect(estimateCoverage({
      summary: { total_inventory_value: 0, valued_items: 2, items_without_hpp: 0, problem_items: 0 },
      total: 2,
      loading: false,
      failed: false,
      filterActive: false,
    })).toMatchObject({ state: "complete", label: "Nilai estimasi stok", value: "Rp 0" });
    expect(estimateCoverage({ summary: base, total: 0, loading: false, failed: false, filterActive: false }).value).toBe("Tidak ada barang");
    expect(estimateCoverage({ summary: null, total: 3, loading: false, failed: true, filterActive: false }).value).toBe("Gagal dimuat");
    expect(estimateCoverage({ summary: null, total: 3, loading: true, failed: false, filterActive: false }).value).toBe("Memuat…");
    expect(estimateCoverage({
      summary: { total_inventory_value: 500000, valued_items: 1, items_without_hpp: 0, problem_items: 0 },
      total: 1,
      loading: false,
      failed: false,
      filterActive: true,
    }).value).toBe("Kelengkapan nilai belum diketahui");
  });

  test("opening history keeps unidentified and mixed-unit rows blank", () => {
    expect(openingHistoryCells({
      effective_date: "2026-10-01",
      total: 100000,
      status: "POSTED",
      lines: [{ master_item_id: "mid-1", item_code: "CUP", item_name: "Cup", qty: 100, base_uom: "PCS" }],
    })).toMatchObject({ date: "2026-10-01", barang: "CUP · Cup", qty: "100 PCS", nilai: "Rp 100.000", status: "Posted" });
    expect(openingHistoryCells({
      effective_date: "2026-10-02",
      total: 30000,
      status: "NEEDS_RECOVERY",
      lines: [
        { master_item_id: "a", qty: 2, base_uom: "PCS" },
        { master_item_id: "b", item_name: "Tinta", qty: 3, base_uom: "KG" },
      ],
    })).toMatchObject({ barang: "2 barang", qty: "—", status: "Perlu pemulihan" });
    expect(openingHistoryCells({
      status: "POSTED",
      lines: [
        { item_code: "A", qty: 2, base_uom: "PCS" },
        { item_code: "B", qty: 3, base_uom: "PCS" },
      ],
      total: 50,
    }).qty).toBe("5 PCS");
    expect(openingHistoryCells({ status: "POSTED", lines: [{ master_item_id: "only-id", qty: 4, base_uom: "PCS" }] }).barang).toBe("—");
  });
});
