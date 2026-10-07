import {
  paymentVisibilityCode,
  paymentVisibilityLabel,
  PAYMENT_VISIBILITY_LABELS,
} from "./hrPayroll";

describe("payroll payment visibility labels", () => {
  test("maps active payment statuses for FINAL periods", () => {
    const period = { status: "FINAL" };
    expect(paymentVisibilityCode(null, period)).toBe("BELUM_DIBAYAR");
    expect(paymentVisibilityLabel(null, period)).toBe("Belum Dibayar");
    expect(paymentVisibilityCode({ status: "DRAFT" }, period)).toBe("MENUNGGU_PERSETUJUAN");
    expect(paymentVisibilityLabel({ status: "DRAFT" }, period)).toBe("Menunggu Persetujuan");
    expect(paymentVisibilityCode({ status: "APPROVED" }, period)).toBe("SIAP_DIPOSTING");
    expect(paymentVisibilityLabel({ status: "APPROVED" }, period)).toBe("Siap Diposting");
    expect(paymentVisibilityCode({ status: "PAID" }, period)).toBe("SUDAH_DIBAYAR");
    expect(paymentVisibilityLabel({ status: "PAID" }, period)).toBe("Sudah Dibayar");
  });

  test("prefers normalized payment_status when present", () => {
    expect(paymentVisibilityCode({ status: "DRAFT", payment_status: "SIAP_DIPOSTING" }, { status: "FINAL" }))
      .toBe("SIAP_DIPOSTING");
  });

  test("non-final periods have no payment visibility", () => {
    expect(paymentVisibilityCode(null, { status: "DRAFT" })).toBeNull();
    expect(paymentVisibilityLabel(null, { status: "REVIEW" })).toBe("");
  });

  test("exposes business labels without raw DRAFT APPROVED PAID as labels", () => {
    expect(Object.values(PAYMENT_VISIBILITY_LABELS)).toEqual([
      "Belum Dibayar",
      "Menunggu Persetujuan",
      "Siap Diposting",
      "Sudah Dibayar",
    ]);
  });
});
