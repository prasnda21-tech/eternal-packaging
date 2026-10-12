import fs from "fs";
import path from "path";
import {
  canBusinessEditReceipt,
  canShowReceiptCancelUi,
  canShowReceiptEditUi,
  interpretReceiptUpdateResponse,
} from "../lib/salesLifecycleUi";

const read = (rel) => fs.readFileSync(path.join(__dirname, rel), "utf8");

describe("Package C — receipt edit UI contracts", () => {
  const page = read("PenerimaanPenjualan.jsx");
  const api = read("../lib/api.js");

  test("Direktur posted eligible detail shows Edit and Batalkan testids", () => {
    expect(page).toMatch(/btn-edit-receipt/);
    expect(page).toMatch(/btn-cancel-receipt-detail/);
    expect(page).toMatch(/canShowReceiptEditUi/);
    expect(page).toMatch(/canShowReceiptCancelUi/);
    const hasDirektur = (p) => p === "sales.receipt.edit" || p === "sales.receipt.cancel";
    const cap = {
      edit_revision: { available: true },
      business_cancel: { available: true },
      document_pending_or_failed: true,
    };
    const doc = {
      business_cancel_progress: "none",
      auto_accounting_status: "complete",
      edit_capability: { editable: true, mode: "posted_revision" },
    };
    expect(canShowReceiptEditUi(hasDirektur, { capability: cap, document: doc })).toBe(true);
    expect(canShowReceiptCancelUi(hasDirektur, { capability: cap, document: doc })).toBe(true);
  });

  test("updateSalesReceipt wired; edit is opt-in only; no flag guessing", () => {
    expect(api).toMatch(/updateSalesReceipt:/);
    expect(page).toMatch(/canShowReceiptEditUi/);
    expect(page).toMatch(/updateSalesReceipt/);
    expect(page).toMatch(/btn-edit-receipt/);
    expect(page).toMatch(/btn-save-receipt-edit/);
    expect(page).toMatch(/edit_capability/);
    expect(page).toMatch(/interpretReceiptUpdateResponse/);
    expect(page).not.toMatch(/sales_receipt_edit_revision_enabled/);
    expect(canBusinessEditReceipt((p) => p === "sales.receipt.edit")).toBe(true);
    expect(canBusinessEditReceipt((p) => p === "sales.receipt.create")).toBe(false);
    expect(interpretReceiptUpdateResponse({
      mode: "posted_revision",
      financial_complete: true,
      receipt: { id: "r1" },
    }).ok).toBe(true);
  });

  test("locked invoice identity remains non-editable in edit form", () => {
    expect(page).toMatch(/Faktur .* terkunci/);
    expect(page).toMatch(/detailEditMode/);
    expect(page).toMatch(/receipt-edit-form/);
  });

  test("edit save: capability gate, busy guard, pending keeps form, same-key retry", () => {
    expect(page).toMatch(/if \(editBusy\) return/);
    expect(page).toMatch(/disabled=\{editBusy\}/);
    expect(page).toMatch(/ensureIdempotencyIntent\(editIdempotencyRef/);
    expect(page).toMatch(/edit_capability/);
    expect(page).toMatch(/if \(!interpreted\.ok\)/);
    expect(page).toMatch(/setEditErr\(interpreted\.message\)/);
    const failBlock = page.slice(
      page.indexOf("if (!interpreted.ok)"),
      page.indexOf("clearIdempotencyIntent(editIdempotencyRef)"),
    );
    expect(failBlock).not.toMatch(/setDetailEditMode\(false\)/);
    expect(failBlock).not.toMatch(/setEditSuccess/);
    expect(interpretReceiptUpdateResponse({
      mode: "posted_revision",
      financial_complete: false,
      message: "pending",
      receipt: { id: "r1", amount: 100 },
    }).ok).toBe(false);
  });
});
