import {
  bumpIdempotencyIntent,
  canBusinessCancelInvoice,
  canBusinessCancelReceipt,
  canBusinessEditInvoice,
  canBusinessEditReceipt,
  canShowInvoiceCancelUi,
  canShowInvoiceEditUi,
  canShowReceiptCancelUi,
  canShowReceiptEditUi,
  isReceiptMutationUiEnabled,
  RECEIPT_MUTATION_UI_ENABLED,
  receiptDocumentBlocksMutation,
  interpretReceiptUpdateResponse,
  canRecoverAutoAccounting,
  clearIdempotencyIntent,
  ensureIdempotencyIntent,
  interpretAutoLifecycleResponse,
  interpretBusinessCancelResponse,
  salesDocumentLifecyclePresentation,
  safeLifecycleReason,
  shouldHideLegacyManualAccounting,
  legacyManualActionsRequired,
} from "./salesLifecycleUi";

describe("salesLifecycleUi", () => {
  test("idempotency key is stable for the same intent and rotates on bump", () => {
    const ref = { current: null };
    const first = ensureIdempotencyIntent(ref, "create|po-1", { prefix: "inv" });
    const retry = ensureIdempotencyIntent(ref, "create|po-1", { prefix: "inv" });
    expect(retry).toBe(first);
    const nextEdit = bumpIdempotencyIntent(ref, "edit|inv-1|2", { prefix: "inv" });
    expect(nextEdit).not.toBe(first);
    expect(ensureIdempotencyIntent(ref, "edit|inv-1|2", { prefix: "inv" })).toBe(nextEdit);
  });

  test("cancel permissions are opt-in only", () => {
    const hasPerm = (p) => p === "sales.invoice.delete";
    expect(canBusinessCancelInvoice(hasPerm)).toBe(false);
    expect(canBusinessCancelReceipt((p) => p === "sales.receipt.delete")).toBe(false);
    expect(canBusinessCancelInvoice((p) => p === "sales.invoice.cancel")).toBe(true);
    expect(canBusinessCancelReceipt((p) => p === "sales.receipt.cancel")).toBe(true);
    expect(canBusinessEditReceipt((p) => p === "sales.receipt.create")).toBe(false);
    expect(canBusinessEditReceipt((p) => p === "sales.receipt.edit")).toBe(true);
  });

  test("receipt Edit/Cancel UI requires opt-in perm + capability + eligibility", () => {
    expect(RECEIPT_MUTATION_UI_ENABLED).toBe(true);
    expect(isReceiptMutationUiEnabled()).toBe(true);
    expect(canBusinessEditReceipt((p) => p === "sales.receipt.edit")).toBe(true);
    expect(canBusinessCancelReceipt((p) => p === "sales.receipt.cancel")).toBe(true);

    const hasEdit = (p) => p === "sales.receipt.edit";
    const hasCancel = (p) => p === "sales.receipt.cancel";
    const readyCap = {
      edit_revision: { available: true },
      business_cancel: { available: true },
      document_pending_or_failed: false,
    };
    const unposted = { edit_capability: { editable: true, mode: "unposted" } };
    const posted = { edit_capability: { editable: true, mode: "posted_revision" } };

    expect(canShowReceiptEditUi(hasEdit, { capability: readyCap, document: unposted })).toBe(true);
    expect(canShowReceiptEditUi(hasEdit, { capability: { edit_revision: { available: false } }, document: posted })).toBe(false);
    expect(canShowReceiptEditUi(() => false, { capability: readyCap, document: unposted })).toBe(false);
    expect(canShowReceiptCancelUi(hasCancel, { capability: readyCap, document: null })).toBe(true);
    expect(canShowReceiptCancelUi(hasCancel, { capability: { business_cancel: { available: false } }, document: null })).toBe(false);
    expect(canShowReceiptCancelUi(hasCancel, { capability: null, document: null })).toBe(false);
    expect(receiptDocumentBlocksMutation({ auto_accounting_status: "pending" }, readyCap)).toBe(true);
    expect(receiptDocumentBlocksMutation({ financial_status: "active" }, readyCap)).toBe(false);
  });

  test("Direktur authorized + posted eligible shows Edit/Batalkan; cancel progress none does not hide", () => {
    const hasDirektur = (p) => p === "sales.receipt.edit" || p === "sales.receipt.cancel";
    const readyCap = {
      edit_revision: { available: true },
      business_cancel: { available: true },
      // Legacy misclassification: idle "none" was reported as pending.
      document_pending_or_failed: true,
    };
    const postedEligible = {
      financial_status: "active",
      business_cancel_progress: "none",
      auto_accounting_status: "complete",
      edit_capability: { editable: true, mode: "posted_revision" },
    };
    expect(receiptDocumentBlocksMutation(postedEligible, readyCap)).toBe(false);
    expect(canShowReceiptEditUi(hasDirektur, { capability: readyCap, document: postedEligible })).toBe(true);
    expect(canShowReceiptCancelUi(hasDirektur, { capability: readyCap, document: postedEligible })).toBe(true);

    expect(canShowReceiptEditUi(() => false, { capability: readyCap, document: postedEligible })).toBe(false);
    expect(canShowReceiptCancelUi(() => false, { capability: readyCap, document: postedEligible })).toBe(false);

    const overrideFalse = (p) => false;
    expect(canShowReceiptEditUi(overrideFalse, { capability: readyCap, document: postedEligible })).toBe(false);

    expect(receiptDocumentBlocksMutation(
      { ...postedEligible, business_cancel_progress: "pending" },
      readyCap,
    )).toBe(true);
    expect(canShowReceiptCancelUi(hasDirektur, {
      capability: readyCap,
      document: { ...postedEligible, business_cancel_progress: "pending" },
    })).toBe(false);
  });

  test("invoice Edit/Batalkan: eligible finalized no receipts; deny perm; deny with receipts", () => {
    const hasAll = (p) => p === "sales.invoice.edit" || p === "sales.invoice.cancel";
    const readyCap = {
      edit_revision: { available: true },
      business_cancel: { available: true },
      document_pending_or_failed: true,
    };
    const eligible = {
      financial_status: "finalized",
      business_cancel_progress: "none",
      auto_accounting_status: "complete",
      active_receipt_count: 0,
      receipt_count: 0,
      receipts: [],
      can_cancel: false, // legacy unposted-only gate — must not block business-cancel UI
    };
    expect(canBusinessEditInvoice(hasAll)).toBe(true);
    expect(canShowInvoiceEditUi(hasAll, { capability: readyCap, document: eligible })).toBe(true);
    expect(canShowInvoiceCancelUi(hasAll, { capability: readyCap, document: eligible })).toBe(true);

    expect(canShowInvoiceEditUi(() => false, { capability: readyCap, document: eligible })).toBe(false);
    expect(canShowInvoiceCancelUi(() => false, { capability: readyCap, document: eligible })).toBe(false);

    expect(canShowInvoiceEditUi(hasAll, {
      capability: readyCap,
      document: { ...eligible, active_receipt_count: 1, receipts: [{ id: "r1" }] },
    })).toBe(false);
    expect(canShowInvoiceEditUi(hasAll, {
      capability: { edit_revision: { available: false }, business_cancel: { available: true } },
      document: eligible,
    })).toBe(false);
    expect(canShowInvoiceCancelUi(hasAll, {
      capability: readyCap,
      document: { ...eligible, business_cancel_progress: "pending" },
    })).toBe(false);
  });

  test("receipt update response requires financial_complete for posted revision", () => {
    expect(interpretReceiptUpdateResponse({
      mode: "unposted",
      receipt: { id: "1" },
      message: "ok",
    }).ok).toBe(true);
    expect(interpretReceiptUpdateResponse({
      mode: "posted_revision",
      financial_complete: false,
      message: "pending",
      receipt: { id: "1" },
    }).ok).toBe(false);
    expect(interpretReceiptUpdateResponse({
      mode: "posted_revision",
      financial_complete: true,
      receipt: { id: "1" },
    }).ok).toBe(true);
  });

  test("auto-lifecycle response requires financial_complete for success", () => {
    expect(interpretAutoLifecycleResponse({
      invoice: { id: "1" },
      message: "Draft ok",
    }).mode).toBe("legacy");
    const pending = interpretAutoLifecycleResponse({}, {
      httpErrorBody: {
        invoice: { id: "1" },
        financial_complete: false,
        auto_accounting_status: "pending",
        message: "Menunggu jurnal",
      },
    });
    expect(pending.ok).toBe(false);
    expect(pending.status.label).toBe("Pending");
    expect(pending.message).toMatch(/Menunggu jurnal|belum selesai/i);
    const done = interpretAutoLifecycleResponse({
      invoice: { id: "1" },
      financial_complete: true,
      auto_accounting_status: "complete",
      message: "Posted",
    });
    expect(done.ok).toBe(true);
  });

  test("verified invoice cancellation returns ok", () => {
    const res = interpretBusinessCancelResponse({
      invoice: { id: "inv-1", business_cancel_progress: "complete" },
      outcome: "cancelled",
      message: "Faktur Penjualan dibatalkan",
      list_class: "cancelled",
    });
    expect(res.ok).toBe(true);
    expect(res.pending).toBe(false);
    expect(res.failed).toBe(false);
    expect(res.message).toMatch(/dibatalkan/i);
  });

  test("verified receipt cancellation returns ok", () => {
    const res = interpretBusinessCancelResponse({
      receipt: { id: "rcpt-1", business_cancel_progress: "complete" },
      outcome: "cancelled",
      message: "Penerimaan Penjualan dibatalkan",
      list_class: "cancelled",
    });
    expect(res.ok).toBe(true);
    expect(res.pending).toBe(false);
    expect(res.failed).toBe(false);
  });

  test("HTTP 200 with Pending stays pending", () => {
    const res = interpretBusinessCancelResponse({
      invoice: { id: "inv-1", business_cancel_progress: "pending" },
      outcome: "cancelled",
      list_class: "cancelled",
      message: "Menunggu reverse",
    });
    expect(res.ok).toBe(false);
    expect(res.pending).toBe(true);
    expect(res.failed).toBe(false);
    expect(res.message).toMatch(/Menunggu reverse|masih diproses/i);
  });

  test("HTTP 200 with Failed stays failed", () => {
    const res = interpretBusinessCancelResponse({
      receipt: { id: "rcpt-1", business_cancel_progress: "failed" },
      list_class: "cancelled",
      message: "Reverse gagal",
    });
    expect(res.ok).toBe(false);
    expect(res.pending).toBe(false);
    expect(res.failed).toBe(true);
    expect(res.message).toMatch(/Reverse gagal|gagal/i);
  });

  test("contradictory response does not return ok", () => {
    expect(interpretBusinessCancelResponse({
      outcome: "cancelled",
      list_class: "active",
      invoice: { id: "inv-1" },
    }).ok).toBe(false);
    expect(interpretBusinessCancelResponse({
      outcome: "error",
      list_class: "cancelled",
      financial_complete: true,
    }).ok).toBe(false);
    expect(interpretBusinessCancelResponse({
      outcome: "cancelled",
      list_class: "cancelled",
      financial_complete: false,
    }).ok).toBe(false);
    expect(interpretBusinessCancelResponse({}).ok).toBe(false);
  });

  test("nested completed status recognized when valid", () => {
    const res = interpretBusinessCancelResponse({
      invoice: {
        id: "inv-2",
        business_cancel_progress: "complete",
        list_class: "cancelled",
      },
    });
    expect(res.ok).toBe(true);
    expect(res.document.id).toBe("inv-2");
  });

  test("pending/failed retains idempotency key until verified completion", () => {
    const ref = { current: null };
    const key = ensureIdempotencyIntent(ref, "cancel-invoice|inv-1", { prefix: "inv-cancel" });
    const pending = interpretBusinessCancelResponse({
      business_cancel_progress: "pending",
      invoice: { id: "inv-1" },
    });
    expect(pending.ok).toBe(false);
    expect(ensureIdempotencyIntent(ref, "cancel-invoice|inv-1", { prefix: "inv-cancel" })).toBe(key);
    const failed = interpretBusinessCancelResponse({
      business_cancel_progress: "failed",
      invoice: { id: "inv-1" },
    });
    expect(failed.ok).toBe(false);
    expect(ensureIdempotencyIntent(ref, "cancel-invoice|inv-1", { prefix: "inv-cancel" })).toBe(key);
    const done = interpretBusinessCancelResponse({
      outcome: "cancelled",
      list_class: "cancelled",
      invoice: { id: "inv-1", business_cancel_progress: "complete" },
    });
    expect(done.ok).toBe(true);
    clearIdempotencyIntent(ref);
    const next = ensureIdempotencyIntent(ref, "cancel-invoice|inv-1", { prefix: "inv-cancel" });
    expect(next).not.toBe(key);
  });

  test("business cancel 403/409 fails closed without implying delete success", () => {
    const flagOff = interpretBusinessCancelResponse({}, {
      statusCode: 409,
      httpErrorBody: "Pembatalan bisnis faktur belum diaktifkan.",
    });
    expect(flagOff.ok).toBe(false);
    expect(flagOff.flagOff).toBe(true);
    expect(flagOff.message).toMatch(/belum diaktifkan/i);

    const forbidden = interpretBusinessCancelResponse({}, {
      statusCode: 403,
      httpErrorBody: "Forbidden",
    });
    expect(forbidden.ok).toBe(false);
    expect(forbidden.flagOff).toBe(false);
    expect(forbidden.failed).toBe(true);
  });

  test("lifecycle presentation maps pending/batal and hides sensitive reasons", () => {
    expect(salesDocumentLifecyclePresentation({
      business_cancel_progress: "pending",
      business_cancel_message: "Menunggu reverse",
    }).label).toBe("Pending");
    expect(salesDocumentLifecyclePresentation({
      list_tab: "cancelled",
      business_cancel_progress: "complete",
    }).label).toBe("Batal");
    expect(safeLifecycleReason({ message: "journal_id=abc token=xyz" })).toBe("");
    expect(canRecoverAutoAccounting(
      { auto_accounting_status: "failed" },
      { hasPerm: (p) => p === "sales.invoice.create", kind: "invoice" },
    )).toBe(true);
    expect(canRecoverAutoAccounting(
      { auto_accounting_status: "failed" },
      { hasPerm: () => false, kind: "invoice" },
    )).toBe(false);
  });

  test("legacy manual accounting hidden only when backend capability READY", () => {
    expect(legacyManualActionsRequired(null)).toBe(true);
    expect(shouldHideLegacyManualAccounting(undefined)).toBe(false);
    expect(shouldHideLegacyManualAccounting({ legacy_manual_actions_required: true })).toBe(false);
    expect(shouldHideLegacyManualAccounting({
      legacy_manual_actions_required: false,
      auto_create_post: { available: true },
    })).toBe(true);
    // Capability failure / 403 / absent field must preserve legacy workflows.
    expect(shouldHideLegacyManualAccounting(null)).toBe(false);
    expect(shouldHideLegacyManualAccounting({})).toBe(false);
    expect(shouldHideLegacyManualAccounting({ auto_create_post: { available: true } })).toBe(false);
  });
});
