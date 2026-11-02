import fs from "fs";
import path from "path";
import {
  LOOKUP_MULTI_DEFAULT_PAGE_SIZE,
  LOOKUP_MULTI_MAX_RENDERED,
  normalizeLookupMultiFetchResult,
} from "./LookupMultiSearchSelect";

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
    expect(src).toMatch(/selectedIds = useMemo/);
    expect(src).toMatch(/setQuery\(e\.target\.value\)/);
    expect(src).not.toMatch(/onChange\?\.\(\[\]\)/);
  });

  test("does not re-fetch when selectedOptions/knownOptions change (no request loop)", () => {
    // Primary async effect must NOT list selectedOptions in its dependency array.
    expect(src).toMatch(/Intentionally omit selectedOptions/);
    expect(src).toMatch(/AbortController/);
    expect(src).toMatch(/LOOKUP_MULTI_MAX_RENDERED/);
    expect(src).toMatch(/load-more/);
    expect(src).toMatch(/LOOKUP_MULTI_QUERY_CACHE_TTL_MS/);
    // Cap DOM rows
    expect(LOOKUP_MULTI_MAX_RENDERED).toBeLessThanOrEqual(50);
    expect(LOOKUP_MULTI_DEFAULT_PAGE_SIZE).toBeLessThanOrEqual(50);
  });

  test("normalizeLookupMultiFetchResult supports array and paged payloads", () => {
    const fromArr = normalizeLookupMultiFetchResult(
      Array.from({ length: 40 }, (_, i) => ({ id: String(i) })),
      { page: 1, pageSize: 40 },
    );
    expect(fromArr.items).toHaveLength(40);
    expect(fromArr.hasMore).toBe(true);

    const fromPage = normalizeLookupMultiFetchResult(
      { items: [{ id: "a" }], hasMore: true, page: 2 },
      { page: 1, pageSize: 40 },
    );
    expect(fromPage.page).toBe(2);
    expect(fromPage.hasMore).toBe(true);
    expect(fromPage.items).toHaveLength(1);
  });

  test("PengirimanPesanan uses paginated multi-PO fetch without uncapped preload", () => {
    const kirim = fs.readFileSync(path.join(__dirname, "../pages/PengirimanPesanan.jsx"), "utf8");
    expect(kirim).toMatch(/LookupMultiSearchSelect/);
    expect(kirim).toMatch(/testId="multi-po-lookup"/);
    expect(kirim).toMatch(/fetchOptions=\{fetchMultiPoOptions\}/);
    expect(kirim).toMatch(/values=\{selectedPoIds\}/);
    expect(kirim).toMatch(/page_size:\s*pageSize|page_size:\s*MULTI_PO_LOOKUP_PAGE_SIZE|page_size:\s*pageSize/);
    expect(kirim).toMatch(/MULTI_PO_LOOKUP_PAGE_SIZE\s*=\s*40/);
    expect(kirim).toMatch(/do not preload all aktif POs/);
    expect(kirim).not.toMatch(/listSalesOrders\(\{\s*status:\s*"aktif"\s*\}\)/);
    expect(kirim).not.toMatch(/\.slice\(0,\s*40\)/);
  });
});
