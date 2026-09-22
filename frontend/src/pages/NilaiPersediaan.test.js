import fs from "fs";
import path from "path";
import { formatRupiah } from "../lib/purchaseUi";

const source = fs.readFileSync(path.join(__dirname, "NilaiPersediaan.jsx"), "utf8");

describe("Nilai Persediaan read-only page", () => {
  test("formats the approved moving-average fixture in Indonesian Rupiah", () => {
    expect(formatRupiah(1066.67)).toBe("Rp 1.066,67");
    expect(formatRupiah(160000)).toBe("Rp 160.000");
  });

  test("renders canonical API values and all required statuses", () => {
    expect(source).toMatch(/Stock Fisik/);
    expect(source).toMatch(/HPP Rata-Rata/);
    expect(source).toMatch(/Nilai Persediaan/);
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
  });

  test("contains no stock, purchasing, or customer-pricing write calls", () => {
    expect(source).not.toMatch(/currentStockUpdate|createPurchase|updatePurchase|publishCustomerPricing/);
    expect(source).not.toMatch(/manual_hpp|hpp_snapshot|tier_prices/);
    expect(source).toMatch(/inventory_valuation\.export/);
    expect(source).not.toMatch(/current_stock\.(view|export)/);
  });

  test("has no manual opening or confirmation workflow", () => {
    expect(source).not.toMatch(/Atur HPP Awal/);
    expect(source).not.toMatch(/inventoryValuationOpeningPreview/);
    expect(source).not.toMatch(/confirmInventoryValuationOpening/);
    expect(source).not.toMatch(/Konfirmasi HPP Awal/);
    expect(source).not.toMatch(/window\.confirm/);
    expect(source).toMatch(/HPP Published \/ Awal/);
    expect(source).toMatch(/HPP Rata-Rata \/ OK/);
  });
});
