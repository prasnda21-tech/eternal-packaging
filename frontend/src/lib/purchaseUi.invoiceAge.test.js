import { formatPurchaseDateId, invoiceAgeDays, invoiceAgeLabel } from "./purchaseUi";

describe("purchase invoice list age + date display", () => {
  test("invoiceAgeDays uses invoice_date vs Asia/Jakarta today and floors at 0", () => {
    expect(invoiceAgeDays("2026-10-20", "2026-10-22")).toBe(2);
    expect(invoiceAgeDays("2026-10-22", "2026-10-22")).toBe(0);
    expect(invoiceAgeDays("2026-10-25", "2026-10-22")).toBe(0);
    expect(invoiceAgeDays("", "2026-10-22")).toBeNull();
    expect(invoiceAgeLabel("2026-10-20", "2026-10-22")).toBe("2 hari");
  });

  test("formatPurchaseDateId renders Indonesian short month", () => {
    expect(formatPurchaseDateId("2026-10-22")).toBe("22 Okt 2026");
    expect(formatPurchaseDateId("2026-11-21")).toBe("21 Nov 2026");
  });
});
