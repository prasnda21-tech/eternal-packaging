import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "FakturPembelian.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "purchasePages.css"), "utf8");

describe("FakturPembelian approved mockup layout contracts", () => {
  test("continuous meta panel + 75/25 body + lower docs/activity/terms audit", () => {
    expect(page).toMatch(/fpb-final/);
    expect(page).toMatch(/fpb-meta-fields/);
    expect(page).toMatch(/fpb-lines-table/);
    expect(page).toMatch(/fpb-side/);
    expect(page).toMatch(/fpb-lower/);
    expect(page).toMatch(/fpb-terms-audit/);
    expect(page).toMatch(/Ringkasan Nilai/);
    expect(page).toMatch(/Info Lainnya/);
    expect(page).toMatch(/Riwayat Perubahan Termin/);
    expect(page).toMatch(/invoice-terms-history/);
    expect(page).toMatch(/PurchaseDocuments/);
    expect(page).toMatch(/PurchaseActivityHistory/);
    expect(page).toMatch(/visual_demo/);
    expect(page).toMatch(/fpb-actions-left/);
    expect(page).toMatch(/fpb-actions-right/);
    expect(page).not.toMatch(/fpb-rail/);
    expect(page).not.toMatch(/Biaya Lainnya/);
    expect(page).not.toMatch(/Tambah Barang/);
    expect(page).not.toMatch(/invoice-terms-schedule/);
    expect(page).not.toMatch(/invoice-terms-facts/);
    expect(css).toMatch(/\.purchase-page \.fpb-meta-fields/);
    expect(css).toMatch(/border-right:\s*1px solid/);
    expect(css).toMatch(/grid-template-columns:\s*minmax\(0,\s*3fr\)\s*minmax\(260px,\s*1fr\)/);
    expect(css).toMatch(/\.purchase-page \.fpb-lower/);
    expect(css).toMatch(/\.purchase-page \.fpb-terms-audit-table/);
    expect(css).toMatch(/var\(--ep-dark/);
  });

  test("list loader does not render above cached rows (soft back navigation)", () => {
    expect(page).toMatch(/loading && rows\.length === 0/);
    expect(page).toMatch(/invoices-loading-row/);
    expect(page).toMatch(/backToList/);
    expect(page).toMatch(/load\(\{ soft:/);
    expect(page).not.toMatch(/\{loading && <tr className="purchase-empty-row"><td colSpan=\{6\} className="muted small">Memuat/);
  });

  test("list table exposes exactly six Owner columns", () => {
    const start = page.indexOf('data-testid="invoices-table"');
    const end = page.indexOf("</table>", start);
    const table = page.slice(start, end);
    expect(table).toMatch(/No\. Faktur/);
    expect(table).toMatch(/Supplier/);
    expect(table).toMatch(/Umur Faktur/);
    expect(table).toMatch(/Jatuh Tempo/);
    expect(table).toMatch(/>Total</);
    expect(table).toMatch(/Sisa Tagihan/);
    expect(table).toMatch(/invoiceAgeLabel/);
    expect(table).not.toMatch(/Sumber/);
    expect(table).not.toMatch(/Referensi/);
    expect(table).not.toMatch(/Grand Total/);
    expect(table).not.toMatch(/cell-status/);
    expect(table).not.toMatch(/purchase-badge-early/);
    expect(table).not.toMatch(/Faktur mendahului barang/);
  });

  test("list spreadsheet grid styles use Eternal line token", () => {
    expect(css).toMatch(/table\.purchase-table-inv/);
    expect(css).toMatch(/border-right:\s*1px solid var\(--line/);
    expect(css).toMatch(/border-bottom:\s*1px solid var\(--line/);
    expect(css).toMatch(/tbody tr:hover td/);
  });

  test("list Total and Sisa Tagihan are centered (header + values, no !important-first)", () => {
    const start = page.indexOf('data-testid="invoices-table"');
    const end = page.indexOf("</table>", start);
    const table = page.slice(start, end);
    expect(table).toMatch(/<th className="cell-money">Total<\/th>/);
    expect(table).toMatch(/<th className="cell-money">Sisa Tagihan<\/th>/);
    expect(table).toMatch(/className="nowrap cell-money"[^>]*>\s*<span className="inv-money">/);
    expect(table).not.toMatch(/cell-money[^"]*"num"/);
    expect(table).not.toMatch(/className="num"/);
    expect(css).toMatch(/table\.purchase-table-inv[\s\S]*?td\.cell-money[\s\S]*?text-align:\s*center/);
    expect(css).toMatch(/table\.purchase-table-inv[\s\S]*?nth-child\(5\)[\s\S]*?text-align:\s*center/);
    expect(css).toMatch(/table\.purchase-table-inv[\s\S]*?nth-child\(6\)[\s\S]*?text-align:\s*center/);
    expect(css).toMatch(/td\.cell-money\s*>\s*\.inv-money[\s\S]*?text-align:\s*center/);
    const moneyBlock = css.match(
      /table\.purchase-table-inv thead th\.cell-money[\s\S]{0,800}?font-variant-numeric:\s*tabular-nums;/
    );
    expect(moneyBlock?.[0] || "").not.toMatch(/text-align:\s*center\s*!important/);
  });

  test("DOM order: Docs|Activity then full-width terms audit then secondary extra", () => {
    const lower = page.indexOf('className="fpb-lower"');
    const docs = page.indexOf('data-testid="detail-panel-docs"');
    const activity = page.indexOf('data-testid="detail-panel-history"');
    const terms = page.indexOf('data-testid="invoice-terms-panel"');
    const extra = page.indexOf('className="fpb-extra"');
    expect(lower).toBeGreaterThan(-1);
    expect(docs).toBeGreaterThan(lower);
    expect(activity).toBeGreaterThan(docs);
    expect(terms).toBeGreaterThan(activity);
    expect(extra).toBeGreaterThan(terms);
    // No duplicate outer Dokumen/Riwayat headings next to component titles
    const docsBlock = page.slice(docs, activity);
    expect(docsBlock).not.toMatch(/<h4>Dokumen<\/h4>/);
    const activityBlock = page.slice(activity, terms);
    expect(activityBlock).not.toMatch(/<h4>Riwayat Aktivitas<\/h4>/);
  });
});
