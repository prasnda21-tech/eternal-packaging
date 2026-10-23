import {
  PURCHASE_TERM_PRESETS,
  addDaysYmd,
  termPresetFromDays,
  paymentTermLabel,
  dueDateLabel,
  canEditPurchaseInvoicePaymentTerms,
} from "./purchaseUi";

describe("purchase invoice payment terms helpers", () => {
  test("presets include COD and Net values", () => {
    expect(PURCHASE_TERM_PRESETS.map((p) => p.id)).toEqual(["COD", "NET7", "NET14", "NET30", "CUSTOM"]);
  });

  test("addDaysYmd and labels", () => {
    expect(addDaysYmd("2026-10-01", 0)).toBe("2026-10-01"); // COD
    expect(addDaysYmd("2026-10-01", 7)).toBe("2026-10-08");
    expect(addDaysYmd("2026-10-01", 14)).toBe("2026-10-15");
    expect(addDaysYmd("2026-10-01", 30)).toBe("2026-10-31");
    expect(addDaysYmd("2026-01-30", 2)).toBe("2026-02-01"); // month boundary, no TZ shift
    expect(termPresetFromDays(14)).toBe("NET14");
    expect(termPresetFromDays(9)).toBe("CUSTOM");
    expect(paymentTermLabel(0)).toBe("COD");
    expect(paymentTermLabel(30)).toBe("Net 30");
    expect(dueDateLabel(null)).toBe("Belum ditentukan");
    expect(dueDateLabel("2026-11-01")).toBe("2026-11-01");
  });

  test("todayYmd follows Asia/Jakarta near UTC midnight", () => {
    const { getWibTodayString } = require("./wibCalendarDate");
    const justAfterWibMidnight = new Date("2026-10-21T17:30:00.000Z");
    expect(getWibTodayString(justAfterWibMidnight)).toBe("2026-10-22");
    expect(justAfterWibMidnight.toISOString().slice(0, 10)).toBe("2026-10-21");
  });

  test("edit gate respects permission, LUNAS, and cancelled", () => {
    expect(canEditPurchaseInvoicePaymentTerms({ payment_status: "BELUM_DIBAYAR" }, true)).toBe(true);
    expect(canEditPurchaseInvoicePaymentTerms({ payment_status: "DIBAYAR_SEBAGIAN" }, true)).toBe(true);
    expect(canEditPurchaseInvoicePaymentTerms({ payment_status: "LUNAS" }, true)).toBe(false);
    expect(canEditPurchaseInvoicePaymentTerms({ payment_status: "BELUM_DIBAYAR", cancelled: true }, true)).toBe(false);
    expect(canEditPurchaseInvoicePaymentTerms({ payment_status: "BELUM_DIBAYAR", can_edit_payment_terms: false }, true)).toBe(false);
    expect(canEditPurchaseInvoicePaymentTerms({ payment_status: "BELUM_DIBAYAR" }, false)).toBe(false);
  });
});
