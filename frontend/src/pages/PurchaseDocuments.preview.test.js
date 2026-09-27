import fs from "fs";
import path from "path";

describe("purchase document preview", () => {
  const src = fs.readFileSync(path.join(__dirname, "PurchaseDocuments.jsx"), "utf8");

  test("PDF Buka uses the in-app viewer and Unduh stays a download", () => {
    expect(src).toMatch(/InAppDocumentViewer/);
    expect(src).toMatch(/looksPdf\(doc\)/);
    expect(src).toMatch(/fetchPurchaseDocumentBlob\(previewDoc\.id, \{ download, signal \}\)/);
    expect(src).toMatch(/title="Unduh"/);
    expect(src).toMatch(/a\.download = doc\.original_filename/);
    expect(src).toMatch(/window\.open\(url, "_blank", "noopener,noreferrer"\)/);
  });
});
