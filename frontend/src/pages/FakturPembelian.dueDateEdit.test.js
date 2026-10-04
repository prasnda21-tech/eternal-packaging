import fs from "fs";
import path from "path";
import { dueDateLabel, invoiceDueDisplayStatus } from "../lib/purchaseUi";

describe("invoice due date edit UI contracts", () => {
  test("create/detail labels distinguish due_date from payment target", () => {
    expect(dueDateLabel(null)).toBe("Belum ditentukan");
    expect(dueDateLabel("")).toBe("Belum ditentukan");
    expect(dueDateLabel("2026-10-05")).toBe("5 Okt 2026");
  });

  test("due status matrix remains display-only", () => {
    expect(invoiceDueDisplayStatus({ due_date: null, payment_status: "BELUM DIBAYAR" })).toBe(
      "BELUM ADA JATUH TEMPO",
    );
    expect(
      invoiceDueDisplayStatus(
        { due_date: "2099-01-01", payment_status: "BELUM DIBAYAR" },
        "2026-09-28",
      ),
    ).toBe("BELUM JATUH TEMPO");
    expect(
      invoiceDueDisplayStatus(
        { due_date: "2026-09-28", payment_status: "BELUM DIBAYAR" },
        "2026-09-28",
      ),
    ).toBe("JATUH TEMPO HARI INI");
    expect(
      invoiceDueDisplayStatus(
        { due_date: "2026-09-01", payment_status: "BELUM DIBAYAR" },
        "2026-09-28",
      ),
    ).toBe("TERLAMBAT");
    expect(
      invoiceDueDisplayStatus(
        { due_date: "2026-09-01", payment_status: "LUNAS" },
        "2026-09-28",
      ),
    ).toBe("LUNAS");
  });

  test("api and faktur page wire due-date-only edit", () => {
    const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
    expect(apiSrc).toMatch(/updatePurchaseInvoiceDueDate:/);
    expect(apiSrc).toMatch(/\/purchase-invoices\/\$\{encodeURIComponent\(id\)\}\/due-date/);

    const pageSrc = fs.readFileSync(path.join(__dirname, "./FakturPembelian.jsx"), "utf8");
    expect(pageSrc).toMatch(/Tanggal Jatuh Tempo/);
    expect(pageSrc).toMatch(/btn-ubah-jatuh-tempo/);
    expect(pageSrc).toMatch(/updatePurchaseInvoiceDueDate/);
    expect(pageSrc).toMatch(/data-testid="invoice-due-date-input"/);

    const modalSrc = fs.readFileSync(path.join(__dirname, "./PurchasePaymentAjukanModal.jsx"), "utf8");
    expect(modalSrc).toMatch(/payment-ajukan-invoice-due-date/);
    expect(modalSrc).toMatch(/dueDateLabel\(invoice\.due_date\)/);
    expect(modalSrc).toMatch(/requested_payment_date:/);
    expect(modalSrc).not.toMatch(/updatePurchaseInvoiceDueDate/);
  });
});
