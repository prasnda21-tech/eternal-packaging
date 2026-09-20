import fs from "fs";
import path from "path";

const pageSrc = fs.readFileSync(path.join(__dirname, "PenerimaanBarang.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

describe("Penerimaan Barang accounting UI", () => {
  test("canonical PO receipt shows compact accounting status and journal amount", () => {
    expect(pageSrc).toMatch(/function ReceiptAccountingBlock/);
    expect(pageSrc).toMatch(/Accounting/);
    expect(pageSrc).toMatch(/Nilai Persediaan/);
    expect(pageSrc).toMatch(/accountingLabel/);
    expect(pageSrc).toMatch(/Not Posted/);
    expect(pageSrc).toMatch(/Posted/);
    expect(pageSrc).toMatch(/Reversed/);
    expect(pageSrc).toMatch(/testIdPrefix}-accounting-status-/);
    expect(pageSrc).toMatch(/testIdPrefix=\"po-history\"/);
    expect(pageSrc).toMatch(/testIdPrefix=\"po-detail\"/);
  });

  test("Post Accounting is permission-gated and only for not_posted canonical receipts", () => {
    expect(pageSrc).toMatch(/hasPerm\("purchase\.receipt\.accounting\.post"\)/);
    expect(pageSrc).toMatch(/btn-post-gr-accounting-/);
    expect(pageSrc).toMatch(/status === "not_posted"/);
    expect(pageSrc).toMatch(/can_post_accounting === false/);
    expect(pageSrc).toMatch(/accounting_post_block_reason/);
    expect(pageSrc).toMatch(/Post Accounting Penerimaan Barang\?/);
    expect(pageSrc).toMatch(/Dr Persediaan/);
    expect(pageSrc).toMatch(/Purchase Clearing \/ GRNI/);
    expect(pageSrc).toMatch(/gr-post-partial-warning/);
  });

  test("Reverse Accounting requires reason and explains stock cancel", () => {
    expect(pageSrc).toMatch(/hasPerm\("purchase\.receipt\.accounting\.reverse"\)/);
    expect(pageSrc).toMatch(/btn-reverse-gr-accounting-/);
    expect(pageSrc).toMatch(/gr-reverse-reason-input/);
    expect(pageSrc).toMatch(/Alasan reversal wajib diisi minimal 3 karakter/);
    expect(pageSrc).toMatch(/mengembalikan stock fisik/);
  });

  test("posted receipt disables direct cancel in favor of Reverse Accounting", () => {
    expect(pageSrc).toMatch(/accounting_status === "posted"/);
    expect(pageSrc).toMatch(/Gunakan Reverse Accounting\./);
    expect(pageSrc).toMatch(/btn-batalkan-receipt-/);
    expect(pageSrc).toMatch(/btn-batalkan-po-receipt-/);
  });

  test("recovery-required warning offers Retry Reverse Accounting", () => {
    expect(pageSrc).toMatch(/accounting_recovery_required === true/);
    expect(pageSrc).toMatch(/Reversal Accounting belum selesai\. Diperlukan pemulihan\./);
    expect(pageSrc).toMatch(/btn-retry-reverse-gr-accounting-/);
    expect(pageSrc).toMatch(/Retry Reverse Accounting/);
  });

  test("Accurate and manual projection rows have no Post Accounting button", () => {
    const fragmentStart = pageSrc.indexOf("function FragmentRow");
    const fragmentSrc = pageSrc.slice(fragmentStart);
    expect(fragmentStart).toBeGreaterThan(-1);
    expect(fragmentSrc).not.toMatch(/btn-post-gr-accounting-/);
    expect(fragmentSrc).not.toMatch(/Post Accounting/);
    expect(pageSrc).toMatch(/row\.kind === "po"/);
    expect(pageSrc).toMatch(/canCancel=\{canReceivePo && row\.kind === "manual"\}/);
  });

  test("API helpers target canonical purchase-goods-receipts only", () => {
    expect(apiSrc).toMatch(/postPurchaseGoodsReceiptAccounting/);
    expect(apiSrc).toMatch(/reversePurchaseGoodsReceiptAccounting/);
    expect(apiSrc).toMatch(/getPurchaseGoodsReceiptAccountingReadiness/);
    expect(apiSrc).toMatch(/\/purchase-goods-receipts\/\$\{encodeURIComponent\(id\)\}\/post-accounting/);
    expect(apiSrc).toMatch(/\/purchase-goods-receipts\/\$\{encodeURIComponent\(id\)\}\/reverse-accounting/);
    expect(apiSrc).not.toMatch(/\/purchase-receipts\/\$\{encodeURIComponent\(id\)\}\/post-accounting/);
  });
});
