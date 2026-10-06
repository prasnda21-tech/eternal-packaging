const fs = require("fs");
const path = require("path");

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

function dokumenPanel(source) {
  return source.slice(
    source.indexOf('data-testid="po-doc-panel-dokumen"'),
    source.indexOf('data-testid="po-doc-panel-riwayat"'),
  );
}

test("Cetak PO calls the Eternal PDF and leaves the source PDF button", () => {
  const docs = dokumenPanel(detail);
  expect(docs).toMatch(/data-testid="po-doc-eternal-pdf"/);
  expect(docs).toMatch(/Cetak PO/);
  expect(docs).toMatch(/hasPerm\("po\.view"\)/);
  expect(docs).toMatch(/onClick=\{openEternalPoPdf\}/);
  expect(docs).toMatch(/data-testid="po-doc-source-pdf"/);
  expect(docs).toMatch(/setSourcePdfOpen\(true\)/);
  expect(detail).toMatch(/api\.downloadSalesOrderPdf\(order\.id\)/);
  expect(detail).toMatch(/URL\.createObjectURL\(blob\)/);
  expect(detail).toMatch(/window\.open\(url, "_blank"\)/);
  expect(api).toMatch(
    /downloadSalesOrderPdf: \(id\) => client\.get\(`\/orders\/\$\{encodeURIComponent\(id\)\}\/pdf`, \{ responseType: "blob" \}\)/,
  );
});
