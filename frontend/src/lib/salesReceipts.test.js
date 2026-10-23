import {
  filterEligibleInvoices,
  resolvePreselectedInvoiceId,
  computeReceiptPreview,
  formatCustomerCreditBlockedReason,
} from "./salesReceipts";

describe("Sales Receipts Eligibility & Preselection Domain Suite", () => {
  describe("filterEligibleInvoices - Exact 5 Strict Business Cases", () => {
    const invoicePositive = {
      id: "inv-pos-1",
      invoice_number: "INV-POS-1",
      po_id: "po-1",
      po_number: "PO-1",
      customer: "Customer Positif",
      financial_status: "finalized",
      grand_total: 500000,
      paid_total: 200000,
      outstanding_amount: 300000,
      payment_status: "DP",
    };

    const invoiceCancelled = {
      id: "inv-cancelled",
      invoice_number: "INV-CANCELLED",
      financial_status: "cancelled",
      grand_total: 500000,
      paid_total: 0,
      outstanding_amount: 0,
    };

    const invoiceZeroPaid = {
      id: "inv-zero-lunas",
      invoice_number: "INV-ZERO-LUNAS",
      po_id: "po-2",
      po_number: "PO-2",
      customer: "Customer Lunas",
      grand_total: 500000,
      paid_total: 500000,
      outstanding_amount: 0,
      payment_status: "LUNAS",
    };

    test("CASE 1: invoice outstanding > 0, queryInvoiceId tidak ada -> eligible", () => {
      const eligible = filterEligibleInvoices([invoicePositive]);
      expect(eligible).toHaveLength(1);
      expect(eligible[0].id).toBe("inv-pos-1");
      expect(eligible[0].outstanding_amount).toBe(300000);
    });

    test("CASE 2: invoice outstanding = 0, queryInvoiceId tidak ada -> NOT eligible", () => {
      const eligible = filterEligibleInvoices([invoiceZeroPaid]);
      expect(eligible).toHaveLength(0);
    });

    test("CASE 3: invoice outstanding = 0, queryInvoiceId MATCH invoice.id -> HARUS NOT eligible (NO BYPASS)", () => {
      const eligible = filterEligibleInvoices([invoiceZeroPaid]);
      expect(eligible).toHaveLength(0);

      // Preselection must also reject the ineligible invoice
      const preselected = resolvePreselectedInvoiceId(eligible, "inv-zero-lunas");
      expect(preselected).toBe("");
    });

    test("CASE 4: invoice outstanding > 0, queryInvoiceId MATCH invoice.id -> eligible and preselectable", () => {
      const eligible = filterEligibleInvoices([invoicePositive]);
      expect(eligible).toHaveLength(1);
      expect(eligible[0].id).toBe("inv-pos-1");

      const preselected = resolvePreselectedInvoiceId(eligible, "inv-pos-1");
      expect(preselected).toBe("inv-pos-1");
    });

    test("CASE 5b: cancelled invoice is never eligible even if outstanding > 0", () => {
      const eligible = filterEligibleInvoices([{ ...invoiceCancelled, outstanding_amount: 500000 }]);
      expect(eligible).toHaveLength(0);
    });

    test("CASE 5c: draft invoice is never eligible even if outstanding > 0", () => {
      const eligible = filterEligibleInvoices([{ ...invoicePositive, financial_status: "draft" }]);
      expect(eligible).toHaveLength(0);
    });

    test("CASE 5: mixed list (A: outstanding > 0, B: outstanding = 0), queryInvoiceId = B -> A eligible, B NOT eligible, preselect empty", () => {
      const mixed = [invoicePositive, invoiceZeroPaid];
      const eligible = filterEligibleInvoices(mixed);
      expect(eligible).toHaveLength(1);
      expect(eligible[0].id).toBe("inv-pos-1");

      // Attempt to preselect invoice B (outstanding = 0) must resolve to empty string
      const preselected = resolvePreselectedInvoiceId(eligible, "inv-zero-lunas");
      expect(preselected).toBe("");
    });
  });

  describe("resolvePreselectedInvoiceId", () => {
    const eligibleList = [
      { id: "inv-1", outstanding_amount: 100000 },
      { id: "inv-2", outstanding_amount: 250000 },
    ];

    test("returns matching id when queryInvoiceId is among eligible invoices", () => {
      expect(resolvePreselectedInvoiceId(eligibleList, "inv-2")).toBe("inv-2");
    });

    test("returns empty string when queryInvoiceId is not in eligible invoices", () => {
      expect(resolvePreselectedInvoiceId(eligibleList, "inv-unknown")).toBe("");
    });

    test("returns empty string when queryInvoiceId is falsy", () => {
      expect(resolvePreselectedInvoiceId(eligibleList, "")).toBe("");
      expect(resolvePreselectedInvoiceId(eligibleList, null)).toBe("");
    });
  });

  describe("computeReceiptPreview", () => {
    const sampleInv = {
      id: "inv-preview-1",
      grand_total: 1000000,
      paid_total: 200000,
      outstanding_amount: 800000,
    };

    test("computes DP state when paying partial amount", () => {
      const res = computeReceiptPreview(sampleInv, 300000);
      expect(res.amount).toBe(300000);
      expect(res.newPaid).toBe(500000);
      expect(res.newOutstanding).toBe(500000);
      expect(res.newState).toBe("DP");
      expect(res.isOverpayment).toBe(false);
    });

    test("computes LUNAS state when paying full remaining amount", () => {
      const res = computeReceiptPreview(sampleInv, 800000);
      expect(res.amount).toBe(800000);
      expect(res.newPaid).toBe(1000000);
      expect(res.newOutstanding).toBe(0);
      expect(res.newState).toBe("LUNAS");
      expect(res.isOverpayment).toBe(false);
    });

    test("flags isOverpayment when payment exceeds outstanding balance", () => {
      const res = computeReceiptPreview(sampleInv, 900000);
      expect(res.isOverpayment).toBe(true);
      expect(res.amount).toBe(900000);
      expect(res.outstandingBefore).toBe(800000);
      expect(res.allocated).toBe(800000);
      expect(res.customerCredit).toBe(100000);
      expect(res.newOutstanding).toBe(0);
    });

    test("handles null or undefined invoice gracefully", () => {
      expect(computeReceiptPreview(null, 100000)).toBeNull();
    });
  });

  describe("formatCustomerCreditBlockedReason — display-only copy", () => {
    test("maps identity / cross-invoice saldo block to operator-friendly warning", () => {
      const raw =
        "Identitas pelanggan tidak dapat dibuktikan. Saldo tidak dapat dipakai lintas faktur.";
      const out = formatCustomerCreditBlockedReason(raw);
      expect(out).toMatch(/Saldo pelanggan belum dapat digunakan/);
      expect(out).toMatch(/lanjutkan pembayaran sesuai faktur/);
      expect(out).not.toMatch(/tidak dapat dibuktikan/);
    });

    test("passes through unknown blocked reasons unchanged", () => {
      expect(formatCustomerCreditBlockedReason("Saldo belum tersedia sebelum penerimaan diposting."))
        .toBe("Saldo belum tersedia sebelum penerimaan diposting.");
    });

    test("empty reason yields empty string (no false warning)", () => {
      expect(formatCustomerCreditBlockedReason("")).toBe("");
      expect(formatCustomerCreditBlockedReason(null)).toBe("");
    });
  });
});
