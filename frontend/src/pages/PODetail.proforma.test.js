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

test("Proforma row stays in the Order Masuk document list", () => {
  const docs = dokumenPanel(detail);
  expect(docs.indexOf("<ProformaInvoiceDocRow")).toBeGreaterThan(docs.indexOf("Surat Jalan"));
  expect(docs.indexOf("<ProformaInvoiceDocRow")).toBeLessThan(docs.indexOf("po-doc-actionbar"));
  expect(docs).toMatch(/showProformaRow/);
  expect(detail).toMatch(/proformaRowVisible/);
  expect(detail).toMatch(/hidden=\{!onOrderMasuk\}/);
  const row = fs.readFileSync(
    path.join(__dirname, "../components/poDetail/ProformaInvoiceDocRow.jsx"),
    "utf8",
  );
  expect(row).toMatch(/className="ghost sm"/);
  expect(row).toMatch(/Lihat Proforma/);
  expect(row).not.toMatch(/po-action-primary/);
});

test("Buat Faktur still navigates to invoice creation", () => {
  const docs = dokumenPanel(detail);
  expect(docs).toMatch(/data-testid="btn-buat-faktur-penjualan"/);
  expect(docs).toMatch(/className="po-action-primary"/);
  expect(docs).toMatch(/\/penjualan\/faktur-penjualan\?po_id=\$\{order\.id\}&action=create/);
  expect(docs).toMatch(/Buat Faktur/);
  const actionBar = docs.slice(docs.indexOf("po-doc-actionbar"));
  expect(actionBar).not.toMatch(/ProformaInvoiceDocRow|btn-lihat-proforma/);
});

test("Proforma visibility follows availability and ignores invoice status fields", () => {
  const access = fs.readFileSync(path.join(__dirname, "../lib/proformaInvoiceAccess.js"), "utf8");
  const row = fs.readFileSync(
    path.join(__dirname, "../components/poDetail/ProformaInvoiceDocRow.jsx"),
    "utf8",
  );
  expect(access).not.toMatch(/financial_status|accounting_status|payment_status/);
  expect(row).not.toMatch(/financial_status|accounting_status|payment_status/);
  expect(detail).toMatch(/available: Boolean\(proformaTarget\?\.invoice_id\)/);
  expect(detail).not.toMatch(/proformaTarget\?\.financial_status|proformaTarget\?\.accounting_status|proformaTarget\?\.payment_status/);
});

test("Lihat Proforma uses the read-only PDF helper", () => {
  expect(detail).toMatch(/openProformaInvoicePdf\(proformaTarget\.invoice_id/);
  expect(detail).toMatch(/download: api\.downloadProformaInvoice/);
  expect(detail).toMatch(/api\.getOrderProforma\(order\.id\)/);
  expect(api).toMatch(/getOrderProforma: \(orderId\) => client\.get\(`\/orders\/\$\{encodeURIComponent\(orderId\)\}\/proforma`\)/);
  expect(api).toMatch(/downloadProformaInvoice: \(id\) => client\.get\(`\/sales-invoices\/\$\{encodeURIComponent\(id\)\}\/proforma`, \{ responseType: "blob" \}\)/);
});
