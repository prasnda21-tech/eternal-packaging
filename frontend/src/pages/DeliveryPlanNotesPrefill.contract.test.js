/**
 * @jest-environment node
 */
import fs from "fs";
import path from "path";

test("Delivery Plan projects Keterangan and Zona columns from PO fields", () => {
  const page = fs.readFileSync(path.join(__dirname, "DeliveryPlanPage.jsx"), "utf8");
  expect(page).toMatch(/key:\s*"notes"/);
  expect(page).toMatch(/label:\s*"Keterangan"/);
  expect(page).toMatch(/key:\s*"delivery_zone"/);
  expect(page).toMatch(/label:\s*"Zona"/);
  expect(page).toMatch(/DeliveryPlanDateRangeField/);
  expect(page).not.toMatch(/type="date"/);
  expect(page).not.toMatch(/dp-filter-date-from/);
  expect(page).toMatch(/dp-notes-cell/);
});

test("Faktur create prefills notes from PO preview with race + manual guards", () => {
  const src = fs.readFileSync(path.join(__dirname, "FakturPenjualan.jsx"), "utf8");
  expect(src).toMatch(/previewReqRef/);
  expect(src).toMatch(/notesManualRef/);
  expect(src).toMatch(/notesPrefillPoRef/);
  expect(src).toMatch(/setInvoiceNotes\(String\(data\.notes/);
  expect(src).toMatch(/notesManualRef\.current = true/);
  expect(src).toMatch(/className="ux90-input ux90-notes-multiline"/);
  expect(src).toMatch(/data-testid="invoice-notes-input"/);
  expect(src.indexOf("<textarea")).toBeGreaterThan(-1);
  expect(src.indexOf("<textarea")).toBeLessThan(src.indexOf('data-testid="invoice-notes-input"'));
});

test("Pengiriman create prefills notes from delivery summary", () => {
  const src = fs.readFileSync(path.join(__dirname, "PengirimanPesanan.jsx"), "utf8");
  expect(src).toMatch(/poSummaryReqRef/);
  expect(src).toMatch(/notes: String\(data\.notes \|\| ""\)\.trim\(\)/);
  expect(src).toMatch(/expedition: data\.expedition \|\| "Internal fleet"/);
  expect(src).toMatch(/data-testid="input-delivery-notes"/);
  expect(src).toMatch(/ux90-notes-multiline/);
  expect(src).toMatch(/<textarea[\s\S]*data-testid="input-delivery-notes"/);
});

test("compact multiline notes CSS keeps viewport-safe height", () => {
  const css = fs.readFileSync(path.join(__dirname, "ux90SalesCreate.css"), "utf8");
  expect(css).toMatch(/ux90-notes-multiline/);
  expect(css).toMatch(/max-height:\s*72px/);
  expect(css).toMatch(/white-space:\s*pre-wrap/);
});
