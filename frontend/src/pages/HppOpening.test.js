const fs = require("fs");
const path = require("path");

describe("HppOpening page", () => {
  const src = fs.readFileSync(path.join(__dirname, "HppOpening.jsx"), "utf8");

  test("exposes download + import + preview confirm flow", () => {
    expect(src).toMatch(/hpp-opening-download-template/);
    expect(src).toMatch(/hpp-opening-import-button/);
    expect(src).toMatch(/hpp-opening-import-preview/);
    expect(src).toMatch(/hpp-opening-import-confirm/);
    expect(src).toMatch(/hppOpeningExportTemplate/);
    expect(src).toMatch(/hppOpeningImportPreview/);
    expect(src).toMatch(/hppOpeningImportConfirm/);
  });

  test("preview columns include Nilai Awal and status codes", () => {
    expect(src).toMatch(/Nilai Awal/);
    expect(src).toMatch(/MISSING_COST/);
    expect(src).toMatch(/INVALID_COST/);
    expect(src).toMatch(/SKU_NOT_FOUND/);
    expect(src).toMatch(/DUPLICATE/);
    expect(src).toMatch(/ALREADY_INITIALIZED/);
  });

  test("does not expose Nilai Persediaan final menu", () => {
    expect(src).not.toMatch(/Nilai Persediaan/);
  });
});
