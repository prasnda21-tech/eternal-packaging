import fs from "fs";
import path from "path";

describe("Surat Jalan card upload-only", () => {
  const src = fs.readFileSync(path.join(__dirname, "SuratJalanPanel.jsx"), "utf8");

  test("card is file upload only", () => {
    expect(src).toMatch(/Upload File Surat Jalan/);
    expect(src).toMatch(/surat-jalan-upload-button/);
    expect(src).toMatch(/kind: "surat_jalan"/);
    expect(src).toMatch(/ConfirmActionModal/);
    expect(src).not.toMatch(/surat-jalan-number-input/);
    expect(src).not.toMatch(/sj-qty-input/);
    expect(src).not.toMatch(/sj-lines-table/);
    expect(src).not.toMatch(/previewAccurateSuratJalan/);
    expect(src).not.toMatch(/sj-accurate-import/);
    expect(src).not.toMatch(/createSuratJalan/);
  });
});
