import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "LookupMultiSearchSelect.jsx"), "utf8");

describe("LookupMultiSearchSelect contract", () => {
  test("reuses facet filter + master list placement and portals the list", () => {
    expect(src).toMatch(/filterFacetOptions/);
    expect(src).toMatch(/placeMasterItemListBox/);
    expect(src).toMatch(/createPortal/);
    expect(src).toMatch(/LOOKUP_MULTI_SEARCH_DEBOUNCE_MS/);
    expect(src).toMatch(/aria-multiselectable="true"/);
    expect(src).toMatch(/ArrowDown/);
    expect(src).toMatch(/Escape/);
    expect(src).toMatch(/Backspace/);
    expect(src).toMatch(/fetchOptions/);
    expect(src).toMatch(/isOptionDisabled/);
    expect(src).toMatch(/selectedCountLabel/);
    expect(src).toMatch(/PO terpilih/);
  });

  test("keeps selected ids stable across query changes", () => {
    expect(src).toMatch(/selectedSet\.has\(id\)/);
    expect(src).toMatch(/emit\(\[\.\.\.selectedIds, id\]\)/);
    expect(src).toMatch(/setKnownOptions/);
    expect(src).toMatch(/ux90-lookup-chip/);
    expect(src).toMatch(/data-testid=\{\`\$\{testId\}-chips\`\}/);
    expect(src).toMatch(/data-testid=\{\`\$\{testId\}-count\`\}/);
  });

  test("PengirimanPesanan wires multi-PO lookup without silent 40-cap", () => {
    const kirim = fs.readFileSync(path.join(__dirname, "../pages/PengirimanPesanan.jsx"), "utf8");
    expect(kirim).toMatch(/LookupMultiSearchSelect/);
    expect(kirim).toMatch(/testId="multi-po-lookup"/);
    expect(kirim).toMatch(/fetchOptions=\{fetchMultiPoOptions\}/);
    expect(kirim).toMatch(/values=\{selectedPoIds\}/);
    expect(kirim).not.toMatch(/\.slice\(0,\s*40\)/);
  });
});
