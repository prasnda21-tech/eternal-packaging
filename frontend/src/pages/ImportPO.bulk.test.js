import fs from "fs";
import path from "path";

describe("Import PO Accurate multi Sales Order", () => {
  const src = fs.readFileSync(path.join(__dirname, "ImportPO.jsx"), "utf8");

  test("preview bulk keeps successful rows and shows per-SO reason", () => {
    expect(src).toMatch(/accurateImportPreviewBulk/);
    expect(src).toMatch(/bulk-preview-modal/);
    expect(src).toMatch(/user_message \|\| it\.error/);
    expect(src).toMatch(/bulk-preview-error-\$\{it\.so_number\}/);
    expect(src).toMatch(/Siap/);
  });

  test("Retry Gagal only refetches failed Sales Orders", () => {
    expect(src).toMatch(/doRetryFailed/);
    expect(src).toMatch(/bulk-preview-retry-failed/);
    expect(src).toMatch(/Retry Gagal/);
    expect(src).toMatch(/filter\(\(i\) => !i\.ok\)/);
    expect(src).toMatch(/accurateImportPreviewBulk\(failed\)/);
    expect(src).toMatch(/incoming\.get\(row\.so_number\) \|\| row/);
    expect(src).toMatch(/bulkPreviewSummary\(merged\)/);
  });

  test("import still only sends ready non-duplicate rows", () => {
    expect(src).toMatch(/filter\(\(i\) => i\.ok && !i\.duplicate\)/);
    expect(src).toMatch(/accurateImportConfirmBulk\(numbers\)/);
  });
});
