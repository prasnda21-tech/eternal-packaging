import fs from "fs";
import path from "path";
import { formatMasterItemSearchError } from "./MasterItemSearchSelect";

const src = fs.readFileSync(path.join(__dirname, "MasterItemSearchSelect.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "masterItemSearchSelect.css"), "utf8");
const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const modal = fs.readFileSync(path.join(__dirname, "LineProductReplaceControl.jsx"), "utf8");
const fullPage = fs.readFileSync(path.join(__dirname, "../pages/MasterBarangJasa.jsx"), "utf8");

describe("MasterItemSearchSelect picker wiring", () => {
  test("uses lightweight picker API with picker=true", () => {
    expect(src).toMatch(/api\.masterItemsPicker/);
    expect(src).toMatch(/page_size: MASTER_ITEM_SEARCH_PAGE_SIZE/);
    expect(api).toMatch(/masterItemsPicker:/);
    expect(api).toMatch(/picker:\s*true/);
  });

  test("full Barang/Jasa page still uses the heavy list API", () => {
    expect(fullPage).toMatch(/api\.masterItems\(/);
    expect(fullPage).not.toMatch(/masterItemsPicker/);
    expect(fullPage).not.toMatch(/picker:\s*true/);
  });

  test("Ubah Item PO modal does not change save or qty wiring", () => {
    expect(modal).toMatch(/api\.replaceSalesOrderLineProduct/);
    expect(modal).toMatch(/MasterItemSearchSelect/);
    expect(modal).not.toMatch(/masterItemsPicker/);
    expect(modal).not.toMatch(/replaceSalesOrderLineProduct.*masterItems/);
  });

  test("in-modal suggestion portal stacks above the modal and still selects", () => {
    expect(modal).toMatch(/className="modal"/);
    expect(modal).toMatch(/<MasterItemSearchSelect/);
    expect(src).toMatch(/closest\?\.\("\.modal"\)/);
    expect(src).toMatch(/mis-list-over-modal/);
    expect(src).toMatch(/onClick=\{\(\) => selectItem\(item\)\}/);
    expect(css).toMatch(/\.mis-list\.mis-list-over-modal\s*\{[^}]*z-index:\s*90/);
    expect(css).toMatch(/\.mis-list\s*\{[^}]*z-index:\s*65/);
  });

  test("search still renders items from response.items", () => {
    expect(src).toMatch(/setItems\(response\.items \|\| \[\]\)/);
    expect(src).toMatch(/view === "results" && items\.map/);
  });

  test("search error does not call save or change qty", () => {
    expect(src).toMatch(/setError\(formatMasterItemSearchError\(err\)\)/);
    expect(src).not.toMatch(/replaceSalesOrderLineProduct/);
    expect(src).not.toMatch(/setQuantity/);
  });

  test("502 maps to a short retry message", () => {
    expect(formatMasterItemSearchError({
      response: { status: 502, data: {} },
      message: "Request failed with status code 502",
    })).toBe("Gagal memuat daftar barang. Coba lagi.");
  });
});
