import fs from "fs";
import path from "path";
import { filterFacetOptions } from "./FacetSearchSelect";

const src = fs.readFileSync(path.join(__dirname, "LookupSearchSelect.jsx"), "utf8");

describe("LookupSearchSelect contract", () => {
  test("reuses facet filter + master list placement and portals the list", () => {
    expect(src).toMatch(/filterFacetOptions/);
    expect(src).toMatch(/placeMasterItemListBox/);
    expect(src).toMatch(/createPortal/);
    expect(src).toMatch(/LOOKUP_SEARCH_DEBOUNCE_MS/);
    expect(src).toMatch(/Tidak ada hasil yang cocok/);
    expect(src).toMatch(/ArrowDown/);
    expect(src).toMatch(/Escape/);
    expect(src).toMatch(/onChange\?\.\("", null\)/);
    expect(src).toMatch(/gettersRef/);
    expect(src).toMatch(/ux90-lookup-list/);
  });

  test("filterFacetOptions is case-insensitive substring", () => {
    const options = [
      { id: "1", label: "PO-2026 Andi" },
      { id: "2", label: "PO-2025 Budi" },
    ];
    const hits = filterFacetOptions(options, "andi", (o) => o.label);
    expect(hits).toHaveLength(1);
    expect(hits[0].id).toBe("1");
  });

  test("create forms wire LookupSearchSelect for PO / invoice / cash bank / sales", () => {
    const faktur = fs.readFileSync(path.join(__dirname, "../pages/FakturPenjualan.jsx"), "utf8");
    const kirim = fs.readFileSync(path.join(__dirname, "../pages/PengirimanPesanan.jsx"), "utf8");
    const terima = fs.readFileSync(path.join(__dirname, "../pages/PenerimaanPenjualan.jsx"), "utf8");
    const so = fs.readFileSync(path.join(__dirname, "../pages/PesananPenjualan.jsx"), "utf8");
    expect(faktur).toMatch(/LookupSearchSelect/);
    expect(faktur).toMatch(/testId="po-selector"/);
    expect(faktur).toMatch(/fetchOptions=\{fetchInvoicePoOptions\}/);
    expect(kirim).toMatch(/testId="select-po-for-delivery"/);
    expect(kirim).toMatch(/LookupMultiSearchSelect/);
    expect(kirim).toMatch(/testId="multi-po-lookup"/);
    expect(terima).toMatch(/testId="select-receipt-invoice"/);
    expect(terima).toMatch(/testId="select-receipt-cash-bank"/);
    expect(terima).toMatch(/overModal/);
    expect(so).toMatch(/testId="input-sales"/);
    expect(so).toMatch(/LookupSearchSelect/);
  });

  test("supports optional server fetchOptions and disabled options", () => {
    expect(src).toMatch(/fetchOptions = null/);
    expect(src).toMatch(/isOptionDisabled/);
    expect(src).toMatch(/getOptionDisabledReason/);
    expect(src).toMatch(/if \(!opt \|\| optionDisabled\(opt\)\) return/);
    expect(src).toMatch(/await fetchOptions\(query\)/);
  });
});
