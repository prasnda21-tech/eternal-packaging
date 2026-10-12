import fs from "fs";
import path from "path";
import { shouldHideLegacyManualAccounting } from "../lib/salesLifecycleUi";

const read = (rel) => fs.readFileSync(path.join(__dirname, rel), "utf8");

describe("Phase 3 — capability-gated one-save UI", () => {
  const faktur = read("FakturPenjualan.jsx");
  const penerimaan = read("PenerimaanPenjualan.jsx");
  const api = read("../lib/api.js");

  test("API exposes lifecycle capability endpoints; pages do not guess flags", () => {
    expect(api).toMatch(/salesInvoiceLifecycleCapability:/);
    expect(api).toMatch(/salesReceiptLifecycleCapability:/);
    expect(faktur).toMatch(/salesInvoiceLifecycleCapability/);
    expect(penerimaan).toMatch(/salesReceiptLifecycleCapability/);
    expect(faktur).toMatch(/shouldHideLegacyManualAccounting/);
    expect(penerimaan).toMatch(/shouldHideLegacyManualAccounting/);
    expect(faktur).not.toMatch(/auto_sales_invoice_lifecycle_enabled/);
    expect(penerimaan).not.toMatch(/auto_sales_receipt_lifecycle_enabled/);
    expect(faktur).not.toMatch(/sales_invoice_edit_revision_enabled/);
    expect(penerimaan).not.toMatch(/sales_receipt_edit_revision_enabled/);
  });

  test("manual Finalisasi/Post/Reverse gated by capability", () => {
    expect(faktur).toMatch(/!hideLegacyManualAccounting/);
    expect(faktur).toMatch(/btn-finalize-invoice/);
    expect(faktur).toMatch(/btn-post-accounting/);
    expect(faktur).toMatch(/btn-reverse-accounting/);
    expect(penerimaan).toMatch(/showLegacyPostReverse/);
    expect(faktur).toMatch(/setCreateLifecycleCapability\(null\)/);
    expect(penerimaan).toMatch(/setReceiptLifecycleCapability\(null\)/);
    expect(shouldHideLegacyManualAccounting({ legacy_manual_actions_required: false })).toBe(true);
    expect(shouldHideLegacyManualAccounting({ legacy_manual_actions_required: true })).toBe(false);
    expect(shouldHideLegacyManualAccounting(null)).toBe(false);
  });

  test("invoice detail Batalkan/Edit use business-cancel and edit_revision gates not legacy can_cancel alone", () => {
    expect(faktur).toMatch(/canShowInvoiceCancelUi/);
    expect(faktur).toMatch(/canShowInvoiceEditUi/);
    expect(faktur).toMatch(/canCancelDetailBusiness/);
    expect(faktur).toMatch(/canReviseInvoice/);
    expect(faktur).toMatch(/businessCancelSalesInvoice/);
    expect(faktur).toMatch(/btn-edit-invoice/);
    expect(faktur).toMatch(/btn-cancel-invoice/);
  });

  test("five-tab and clickable row contracts remain wired", () => {
    expect(faktur).toMatch(/salesFiveTabListParams|normalizeSalesListTab/);
    expect(penerimaan).toMatch(/normalizeSalesListTab|salesFiveTab/);
    expect(faktur).toMatch(/clickableDocumentRowProps/);
    expect(penerimaan).toMatch(/clickableDocumentRowProps/);
  });
});
