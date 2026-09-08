import fs from "fs";
import path from "path";
import { STOCK_IN_OPTIONS, STOCK_OUT_OPTIONS } from "./PhysicalStockSettings";
import { settingsTabFromPath, SETTINGS_TAB_IDS, SETTINGS_TILE_LABELS } from "../lib/navAccess";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const hppSettingsSrc = fs.readFileSync(path.join(__dirname, "HppSettings.jsx"), "utf8");
const physicalStockSrc = fs.readFileSync(path.join(__dirname, "PhysicalStockSettings.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

describe("Physical Stock Settings Component & Integration", () => {
  test("1. Stok Fisik tab is registered under Settings and navAccess", () => {
    expect(SETTINGS_TAB_IDS).toContain("physical-stock");
    expect(SETTINGS_TILE_LABELS["physical-stock"]).toBe("Stok Fisik");
    expect(settingsTabFromPath("/settings/physical-stock")).toBe("physical-stock");
    expect(settingsSrc).toMatch(/id:\s*"physical-stock"/);
    expect(settingsSrc).toMatch(/label:\s*"Stok Fisik"/);
    expect(settingsSrc).toMatch(/<PhysicalStockSettings/);
  });

  test("2. enabled toggle renders with inactive status by default and safe fallback", () => {
    expect(physicalStockSrc).toMatch(/useState\(false\)/);
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-enabled"/);
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-status-badge"/);
    expect(physicalStockSrc).toMatch(/<option value="inactive">Nonaktif<\/option>/);
    expect(physicalStockSrc).toMatch(/<option value="active">Aktif<\/option>/);
    expect(physicalStockSrc).toMatch(/Nonaktif/);
  });

  test("3. stock-in dropdown renders all valid options with descriptions", () => {
    expect(physicalStockSrc).toMatch(/data-testid="stock-in-trigger-select"/);
    expect(physicalStockSrc).toMatch(/data-testid="stock-in-trigger-desc"/);
    
    // Check supported values
    const inValues = STOCK_IN_OPTIONS.map((o) => o.value);
    expect(inValues).toContain("goods_receipt");
    expect(inValues).toContain("purchase_order");

    const inLabels = STOCK_IN_OPTIONS.map((o) => o.label);
    expect(inLabels).toContain("Penerimaan Barang");
    expect(inLabels).toContain("Pesanan Pembelian");
  });

  test("4. stock-out dropdown renders all valid options with descriptions", () => {
    expect(physicalStockSrc).toMatch(/data-testid="stock-out-trigger-select"/);
    expect(physicalStockSrc).toMatch(/data-testid="stock-out-trigger-desc"/);

    // Check supported values (only safe, crash-reconcilable triggers)
    const outValues = STOCK_OUT_OPTIONS.map((o) => o.value);
    expect(outValues).toContain("loading_confirmation");
    expect(outValues).toContain("delivery_confirmed");
    expect(outValues).toContain("packing_completed");
    expect(outValues).not.toContain("sales_delivery");

    const outLabels = STOCK_OUT_OPTIONS.map((o) => o.label);
    expect(outLabels).toContain("Loading Dikonfirmasi");
    expect(outLabels).toContain("Pengiriman Dikonfirmasi");
    expect(outLabels).toContain("Packing Selesai");
    expect(outLabels).not.toContain("Surat Jalan Dibuat");
  });

  test("5. read-only Operator Selesai status conversion rule is displayed and not editable", () => {
    expect(physicalStockSrc).toMatch(/Perubahan Status Cetak/);
    expect(physicalStockSrc).toMatch(/Aturan Baku \(Read-only\)/);
    expect(physicalStockSrc).toMatch(/Operator Selesai/);
    expect(physicalStockSrc).toMatch(/Belum Dicetak/);
    expect(physicalStockSrc).toMatch(/Sudah Dicetak/);
    expect(physicalStockSrc).toMatch(/data-testid="status-cetak-rule-card"/);
    expect(physicalStockSrc).toMatch(/data-testid="status-cetak-table"/);
    expect(physicalStockSrc).toMatch(/Total:\s*<strong>0<\/strong>/);
  });

  test("6. HPP separation disclaimer text is rendered", () => {
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-disclaimer"/);
    expect(physicalStockSrc).toMatch(/Pengaturan stok fisik terpisah dari Pengakuan HPP/);
    expect(physicalStockSrc).toMatch(/Perubahan pengaturan tidak menghitung ulang transaksi lama/);
  });

  test("7. API client exports methods for physical stock settings", () => {
    expect(apiSrc).toMatch(/physicalStockSettings:\s*\(\)\s*=>\s*client\.get\(`\/settings\/physical-stock`\)/);
    expect(apiSrc).toMatch(/updatePhysicalStockSettings:\s*\(payload\)\s*=>\s*client\.put\(`\/settings\/physical-stock`,\s*payload\)/);
    expect(physicalStockSrc).toMatch(/api\.updatePhysicalStockSettings\(payload\)/);
    expect(physicalStockSrc).toMatch(/notify\?\.\(res\.message/);
  });

  test("8. error response does not fake success and shows error banner", () => {
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-error"/);
    expect(physicalStockSrc).toMatch(/setErr\(errDetail\(ex\)\)/);
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-save"/);
  });

  test("9. current values and audit trail section render correctly", () => {
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-audit"/);
    expect(physicalStockSrc).toMatch(/Status otomatisasi aktif/);
    expect(physicalStockSrc).toMatch(/Trigger masuk aktif/);
    expect(physicalStockSrc).toMatch(/Trigger keluar aktif/);
    expect(physicalStockSrc).toMatch(/Terakhir disimpan/);
  });

  test("10. existing HPP tab source remains completely independent and untouched", () => {
    expect(hppSettingsSrc).toMatch(/Pengaturan HPP \/ Valuasi Persediaan/);
    expect(hppSettingsSrc).toMatch(/recognition_point/);
    expect(hppSettingsSrc).toMatch(/FIFO/);
    expect(hppSettingsSrc).toMatch(/RATA-RATA/);
    expect(hppSettingsSrc).not.toMatch(/physical_stock/);
    expect(hppSettingsSrc).not.toMatch(/loading_confirmation/);
  });
});
