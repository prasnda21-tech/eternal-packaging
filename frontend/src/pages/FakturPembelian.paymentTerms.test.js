import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "FakturPembelian.jsx"), "utf8");
const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

describe("FakturPembelian PUR-INV-01 UI contracts", () => {
  test("create form exposes term presets and due date", () => {
    expect(page).toMatch(/invoice-term-presets/);
    expect(page).toMatch(/term-preset-\$\{p\.id\.toLowerCase\(\)\}/);
    expect(page).toMatch(/PURCHASE_TERM_PRESETS/);
    expect(page).toMatch(/invoice-due-date-input/);
    expect(page).toMatch(/payment_term_days/);
    expect(page).toMatch(/due_date: dueDate/);
  });

  test("list and detail show termin / jatuh tempo", () => {
    expect(page).toMatch(/Jatuh Tempo/);
    expect(page).toMatch(/detail-due-date/);
    expect(page).toMatch(/detail-payment-term/);
    expect(page).toMatch(/invoice-terms-history/);
    expect(page).toMatch(/btn-ubah-termin-jatuh-tempo/);
    expect(page).toMatch(/purchase_invoices\.edit/);
    expect(page).toMatch(/canEditPurchaseInvoicePaymentTerms/);
  });

  test("term preset always recomputes due; edit reason is required", () => {
    expect(page).toMatch(/setDueManual\(false\)/);
    expect(page).toMatch(/setDueDate\(addDaysYmd\(invoiceDate, preset\.days\)\)/);
    expect(page).toMatch(/Alasan perubahan wajib diisi/);
    expect(page).toMatch(/edit-terms-reason/);
    expect(page).toMatch(/getPurchaseInvoice\(detail\.id\)/);
  });

  test("api client patches payment-terms endpoint", () => {
    expect(api).toMatch(/updatePurchaseInvoicePaymentTerms/);
    expect(api).toMatch(/\/payment-terms/);
  });
});
