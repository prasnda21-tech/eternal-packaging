import { dateKey, matchesDeliveryScheduleFilters, rescheduleConfirmLabel } from "./deliverySchedule";

describe("delivery schedule filters", () => {
  const row = {
    order_number: "PO/2026/A",
    customer_name: "PT Contoh",
    effective_delivery_date: "2026-08-30",
    status: "belum_dijadwalkan",
  };

  test("AND filters for po customer status and date range", () => {
    expect(matchesDeliveryScheduleFilters(row, { po: "PO/2026", customer: "contoh", status: "belum_dijadwalkan", dateFrom: "2026-08-30", dateTo: "2026-08-30" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { po: "ZZ" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { status: "terkirim" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { dateFrom: "2026-09-01" })).toBe(false);
  });

  test("reset empty filters keep all rows", () => {
    expect(matchesDeliveryScheduleFilters(row, { dateFrom: "", dateTo: "", po: "", customer: "", status: "semua" })).toBe(true);
  });
});

describe("dateKey", () => {
  test("reads ISO datetime as date only", () => {
    expect(dateKey("2026-08-29T00:00:00+07:00")).toBe("2026-08-29");
    expect(dateKey("")).toBe("");
  });
});

describe("reschedule confirm label", () => {
  test("request vs set-date wording", () => {
    expect(rescheduleConfirmLabel(true)).toBe("Konfirmasi Jadwal Ulang");
    expect(rescheduleConfirmLabel(false)).toBe("Konfirmasi Permintaan");
  });
});
