import {
  EMPTY_DELIVERY_FILTERS,
  EMPTY_DELIVERY_SORT,
  countActiveDeliveryFilters,
  cycleDeliverySort,
  dateKey,
  matchesDeliveryScheduleFilters,
  rescheduleConfirmLabel,
  sortDeliveryRows,
} from "./deliverySchedule";

describe("delivery schedule filters", () => {
  const row = {
    order_number: "PO/2026/A",
    customer_name: "PT Contoh",
    effective_delivery_date: "2026-08-30",
    status: "belum_dijadwalkan",
    driver_name: "Budi",
    percent_delivered: 50,
    percent_ready_to_ship: 75,
  };

  test("AND filters for po customer status and date range", () => {
    expect(matchesDeliveryScheduleFilters(row, { po: "PO/2026", customer: "contoh", status: "belum_dijadwalkan", dateFrom: "2026-08-30", dateTo: "2026-08-30" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { po: "ZZ" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { status: "terkirim" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { dateFrom: "2026-09-01" })).toBe(false);
  });

  test("driver terkirim and dapat dikirim filters", () => {
    expect(matchesDeliveryScheduleFilters(row, { driver: "bud" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { driver: "andi" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { terkirim: "partial" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { terkirim: "full" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { dapatDikirim: "partial" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { dapatDikirim: "none" })).toBe(false);
  });

  test("reset empty filters keep all rows", () => {
    expect(matchesDeliveryScheduleFilters(row, { dateFrom: "", dateTo: "", po: "", customer: "", status: "semua" })).toBe(true);
  });

  test("active filter count ignores defaults", () => {
    expect(countActiveDeliveryFilters(EMPTY_DELIVERY_FILTERS)).toBe(0);
    expect(countActiveDeliveryFilters({ ...EMPTY_DELIVERY_FILTERS, customer: "a", terkirim: "partial" })).toBe(2);
  });
});

describe("delivery schedule header sort", () => {
  const rows = [
    { id: "b", customer_name: "Beta", order_number: "PO-10", effective_delivery_date: "2026-08-20", driver_name: "Zed", percent_delivered: 50, percent_ready_to_ship: 75 },
    { id: "a", customer_name: "Alpha", order_number: "PO-2", effective_delivery_date: null, driver_name: "", percent_delivered: 0, percent_ready_to_ship: 100 },
    { id: "c", customer_name: "gamma", order_number: "PO-3", effective_delivery_date: "2026-08-15", driver_name: "Andi", percent_delivered: 100, percent_ready_to_ship: 50 },
  ];

  test("cycle ASC → DESC → NONE and one active column", () => {
    const a1 = cycleDeliverySort(EMPTY_DELIVERY_SORT, "customer");
    expect(a1).toEqual({ key: "customer", direction: "asc" });
    expect(cycleDeliverySort(a1, "customer")).toEqual({ key: "customer", direction: "desc" });
    expect(cycleDeliverySort({ key: "customer", direction: "desc" }, "customer")).toEqual(EMPTY_DELIVERY_SORT);
    expect(cycleDeliverySort(a1, "po_number")).toEqual({ key: "po_number", direction: "asc" });
  });

  test("customer ASC/DESC", () => {
    expect(sortDeliveryRows(rows, { key: "customer", direction: "asc" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(sortDeliveryRows(rows, { key: "customer", direction: "desc" }).map((r) => r.id)).toEqual(["c", "b", "a"]);
  });

  test("PO natural ASC/DESC", () => {
    expect(sortDeliveryRows(rows, { key: "po_number", direction: "asc" }).map((r) => r.order_number))
      .toEqual(["PO-2", "PO-3", "PO-10"]);
  });

  test("delivery date missing last both directions", () => {
    expect(sortDeliveryRows(rows, { key: "effective_delivery_date", direction: "asc" }).map((r) => r.id)).toEqual(["c", "b", "a"]);
    expect(sortDeliveryRows(rows, { key: "effective_delivery_date", direction: "desc" }).map((r) => r.id)).toEqual(["b", "c", "a"]);
  });

  test("terkirim and dapat dikirim numeric sort", () => {
    expect(sortDeliveryRows(rows, { key: "shipped", direction: "asc" }).map((r) => r.percent_delivered)).toEqual([0, 50, 100]);
    expect(sortDeliveryRows(rows, { key: "shippable", direction: "desc" }).map((r) => r.percent_ready_to_ship)).toEqual([100, 75, 50]);
  });

  test("filter + sort compose and NONE restores filtered order", () => {
    const filtered = rows.filter((r) => matchesDeliveryScheduleFilters(r, { terkirim: "partial" }));
    const sorted = sortDeliveryRows(filtered, { key: "customer", direction: "asc" });
    expect(sorted.map((r) => r.id)).toEqual(["b"]);
    expect(sortDeliveryRows(filtered, EMPTY_DELIVERY_SORT).map((r) => r.id)).toEqual(["b"]);
  });

  test("same customer multiple PO remain independent", () => {
    const same = [
      { id: "1", customer_name: "PT Test Customer", order_number: "PO-A", percent_delivered: 10 },
      { id: "2", customer_name: "PT Test Customer", order_number: "PO-B", percent_delivered: 10 },
    ];
    expect(sortDeliveryRows(same, { key: "shipped", direction: "asc" }).map((r) => r.id)).toEqual(["1", "2"]);
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
