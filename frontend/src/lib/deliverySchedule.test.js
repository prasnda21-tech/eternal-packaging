import {
  DELIVERY_SCHEDULE_STATUS_OPTIONS,
  EMPTY_DELIVERY_FILTERS,
  EMPTY_DELIVERY_SORT,
  DELIVERY_SORT_KEYS,
  buildDeliveryDetailsSummary,
  countActiveDeliveryFilters,
  cycleDeliverySort,
  dateKey,
  formatScheduleQty,
  matchesDeliveryScheduleFilters,
  normalizeDeliveryScheduleFilters,
  normalizeDeliveryScheduleStatus,
  orderIdsForSelectedScheduleKeys,
  rescheduleConfirmLabel,
  scheduleRowQtyParts,
  scheduleRowSelectionKey,
  sortDeliveryRows,
  wibTodayKey,
} from "./deliverySchedule";
import { locationSetupStatusLabel } from "./deliveryRouteBridge";
import { STATUS_LOKASI_SORT_DUMMY_ROWS } from "./deliveryScheduleStatusLokasiFixtures";

describe("delivery schedule filters", () => {
  const row = {
    order_number: "PO/2026/A",
    customer_name: "PT Contoh",
    effective_delivery_date: "2026-08-30",
    status: "terjadwal",
    driver_name: "Budi",
    percent_delivered: 50,
    percent_ready_to_ship: 75,
  };

  test("AND filters for po customer status and date range", () => {
    expect(matchesDeliveryScheduleFilters(row, { po: "PO/2026", customer: "contoh", status: "terjadwal", dateFrom: "2026-08-30", dateTo: "2026-08-30" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { po: "ZZ" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { status: "terkirim" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { dateFrom: "2026-09-01" })).toBe(false);
  });

  test("combined q search matches PO OR customer; other filters stay AND", () => {
    expect(matchesDeliveryScheduleFilters(row, { q: "PO/2026" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { q: "contoh" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { q: "ZZ-NONE" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { q: "PO/2026", status: "terjadwal" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { q: "PO/2026", status: "terkirim" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { q: "contoh", dateFrom: "2026-08-30", dateTo: "2026-08-30" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { q: "contoh", dateFrom: "2026-09-01" })).toBe(false);
  });

  test("Sunday delivery date remains filterable (manual/SJ date, not auto working-day)", () => {
    const sundayRow = { ...row, effective_delivery_date: "2026-08-30", status: "terjadwal" };
    expect(matchesDeliveryScheduleFilters(sundayRow, { dateFrom: "2026-08-30", dateTo: "2026-08-30" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(sundayRow, { dateFrom: "2026-08-31", dateTo: "2026-08-31" })).toBe(false);
  });

  test("inclusive date range keeps both endpoints", () => {
    expect(matchesDeliveryScheduleFilters(row, { dateFrom: "2026-08-30", dateTo: "2026-08-30" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(
      { ...row, effective_delivery_date: "2026-10-13" },
      { dateFrom: "2026-10-12", dateTo: "2026-10-15" },
    )).toBe(true);
    expect(matchesDeliveryScheduleFilters(
      { ...row, effective_delivery_date: "2026-10-11" },
      { dateFrom: "2026-10-12", dateTo: "2026-10-15" },
    )).toBe(false);
    expect(matchesDeliveryScheduleFilters(
      { ...row, effective_delivery_date: "2026-10-16" },
      { dateFrom: "2026-10-12", dateTo: "2026-10-15" },
    )).toBe(false);
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

  test("Tanggal Hari Ini (hari_ini) matches effective_delivery_date == today WIB, including terkirim", () => {
    const today = "2026-09-19";
    const hariIni = { status: "hari_ini" };
    const nara = { order_number: "PO/2026/09/14/000011", customer_name: "NARA", effective_delivery_date: "2026-09-19", status: "hari_ini" };
    const pureve = { order_number: "PO/2026/09/16/000010", customer_name: "PUREVE", effective_delivery_date: "2026-09-19", status: "terkirim" };
    const songolas = { order_number: "PO/2026/09/14/000019", customer_name: "SONGOLAS", effective_delivery_date: "2026-09-19", status: "terkirim" };
    const limo = { order_number: "PO/2026/09/17/000015", customer_name: "LIMO", effective_delivery_date: "2026-09-21", status: "terjadwal" };
    const late = { order_number: "PO/YEST", customer_name: "Late", effective_delivery_date: "2026-09-18", status: "terlambat" };
    const todayScheduled = { order_number: "PO/TODAY-SCHED", customer_name: "Sched", effective_delivery_date: "2026-09-19", status: "terjadwal" };
    const bufferToday = {
      order_number: "PO/BUF",
      customer_name: "Buffer",
      effective_delivery_date: "2026-09-19",
      status: "terjadwal",
      buffer_stock: true,
    };

    expect(matchesDeliveryScheduleFilters(nara, hariIni, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(pureve, hariIni, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(songolas, hariIni, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(todayScheduled, hariIni, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(bufferToday, hariIni, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(limo, hariIni, today)).toBe(false);
    expect(matchesDeliveryScheduleFilters(late, hariIni, today)).toBe(false);

    expect(matchesDeliveryScheduleFilters(pureve, { status: "terkirim" }, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(nara, { status: "terkirim" }, today)).toBe(false);
    expect(matchesDeliveryScheduleFilters(late, { status: "terlambat" }, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(pureve, { status: "terlambat" }, today)).toBe(false);

    expect(matchesDeliveryScheduleFilters(nara, { ...hariIni, po: "000011" }, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(pureve, { ...hariIni, po: "000011" }, today)).toBe(false);
    expect(matchesDeliveryScheduleFilters(nara, { ...hariIni, customer: "nara" }, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(pureve, { ...hariIni, dateFrom: "2026-09-19", dateTo: "2026-09-19" }, today)).toBe(true);
    expect(matchesDeliveryScheduleFilters(pureve, { ...hariIni, dateFrom: "2026-09-20" }, today)).toBe(false);
  });

  test("Terlambat uses row.status — Buffer Stock past date stays out; normal overdue stays in", () => {
    const bufferPast = {
      order_number: "PO/BUF-PAST",
      customer_name: "Buffer",
      effective_delivery_date: "2026-09-01",
      status: "terjadwal",
      buffer_stock: true,
    };
    const overdue = {
      order_number: "PO/LATE",
      customer_name: "Cust",
      effective_delivery_date: "2026-09-01",
      status: "terlambat",
    };
    expect(matchesDeliveryScheduleFilters(bufferPast, { status: "terlambat" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(overdue, { status: "terlambat" })).toBe(true);
  });

  test("status options hide Belum Dijadwalkan and rename Tanggal Hari Ini; legacy normalizes", () => {
    expect(DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => o.key)).toEqual([
      "semua", "terjadwal", "hari_ini", "terlambat", "terkirim",
    ]);
    expect(DELIVERY_SCHEDULE_STATUS_OPTIONS.find((o) => o.key === "hari_ini").label).toBe("Tanggal Hari Ini");
    expect(DELIVERY_SCHEDULE_STATUS_OPTIONS.some((o) => o.key === "belum_dijadwalkan")).toBe(false);
    expect(normalizeDeliveryScheduleStatus("belum_dijadwalkan")).toBe("semua");
    expect(normalizeDeliveryScheduleStatus("hari_ini")).toBe("hari_ini");
    expect(normalizeDeliveryScheduleStatus("unknown_x")).toBe("semua");
    expect(normalizeDeliveryScheduleFilters({
      status: "belum_dijadwalkan",
      dateFrom: "2026-10-15",
      dateTo: "2026-10-12",
    })).toEqual({
      ...EMPTY_DELIVERY_FILTERS,
      status: "semua",
      dateFrom: "2026-10-12",
      dateTo: "2026-10-15",
    });
  });

  test("wibTodayKey uses Asia/Jakarta not UTC midnight", () => {
    expect(wibTodayKey(new Date("2026-09-18T17:01:00.000Z"))).toBe("2026-09-19");
    expect(wibTodayKey(new Date("2026-09-18T16:59:00.000Z"))).toBe("2026-09-18");
  });

  test("wibTodayKey ignores host timezone offset", () => {
    const original = Date.prototype.getTimezoneOffset;
    try {
      Date.prototype.getTimezoneOffset = function wibHostOffset() { return -420; };
      expect(wibTodayKey(new Date("2026-09-18T17:01:00.000Z"))).toBe("2026-09-19");
      Date.prototype.getTimezoneOffset = function utcHostOffset() { return 0; };
      expect(wibTodayKey(new Date("2026-09-18T17:01:00.000Z"))).toBe("2026-09-19");
    } finally {
      Date.prototype.getTimezoneOffset = original;
    }
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

  test("Status Lokasi ASC/DESC uses canonical coords not address", () => {
    const dummy = STATUS_LOKASI_SORT_DUMMY_ROWS;
    expect(dummy.length).toBeGreaterThanOrEqual(3);
    expect(locationSetupStatusLabel(dummy[0])).toBe("Belum Diatur");
    expect(locationSetupStatusLabel(dummy[1])).toBe("Sudah Diatur");
    expect(locationSetupStatusLabel(dummy[2])).toBe("Belum Diatur");
    expect(dummy[2].address).toBeTruthy();

    const asc = sortDeliveryRows(dummy, { key: DELIVERY_SORT_KEYS.location_status, direction: "asc" });
    expect(asc.map((r) => locationSetupStatusLabel(r))).toEqual([
      "Belum Diatur",
      "Belum Diatur",
      "Sudah Diatur",
      "Sudah Diatur",
    ]);
    expect(asc.map((r) => r.order_number)).toEqual([
      "DUMMY-PO-001",
      "DUMMY-PO-003",
      "DUMMY-PO-002",
      "DUMMY-PO-004",
    ]);

    const desc = sortDeliveryRows(dummy, { key: DELIVERY_SORT_KEYS.location_status, direction: "desc" });
    expect(desc.map((r) => locationSetupStatusLabel(r))).toEqual([
      "Sudah Diatur",
      "Sudah Diatur",
      "Belum Diatur",
      "Belum Diatur",
    ]);
    expect(desc.map((r) => r.order_number)).toEqual([
      "DUMMY-PO-002",
      "DUMMY-PO-004",
      "DUMMY-PO-001",
      "DUMMY-PO-003",
    ]);
  });

  test("Status Lokasi sort leaves other-column sort unchanged", () => {
    expect(sortDeliveryRows(rows, { key: "customer", direction: "asc" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(cycleDeliverySort(EMPTY_DELIVERY_SORT, DELIVERY_SORT_KEYS.location_status))
      .toEqual({ key: "location_status", direction: "asc" });
  });
});

describe("Status Lokasi dummy fixtures", () => {
  test("exports at least 3 task-owned dummy POs with required location cases", () => {
    expect(STATUS_LOKASI_SORT_DUMMY_ROWS.length).toBeGreaterThanOrEqual(3);
    expect(STATUS_LOKASI_SORT_DUMMY_ROWS.map((r) => r.order_number)).toEqual([
      "DUMMY-PO-001",
      "DUMMY-PO-002",
      "DUMMY-PO-003",
      "DUMMY-PO-004",
    ]);
    expect(locationSetupStatusLabel(STATUS_LOKASI_SORT_DUMMY_ROWS[0])).toBe("Belum Diatur");
    expect(locationSetupStatusLabel(STATUS_LOKASI_SORT_DUMMY_ROWS[1])).toBe("Sudah Diatur");
    expect(STATUS_LOKASI_SORT_DUMMY_ROWS[2].address).toBeTruthy();
    expect(locationSetupStatusLabel(STATUS_LOKASI_SORT_DUMMY_ROWS[2])).toBe("Belum Diatur");
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

describe("schedule SJ selection identity", () => {
  test("keys by shipment_id so multi-SJ on one PO stay independent", () => {
    const a = { id: "ord-1", shipment_id: "sh-1" };
    const b = { id: "ord-1", shipment_id: "sh-2" };
    expect(scheduleRowSelectionKey(a)).toBe("sh-1");
    expect(scheduleRowSelectionKey(b)).toBe("sh-2");
    expect(scheduleRowSelectionKey(a)).not.toBe(scheduleRowSelectionKey(b));
  });

  test("Optimasi order ids are unique orders from selected SJ keys only", () => {
    const rows = [
      { id: "ord-1", shipment_id: "sh-1" },
      { id: "ord-1", shipment_id: "sh-2" },
      { id: "ord-2", shipment_id: "sh-3" },
    ];
    expect(orderIdsForSelectedScheduleKeys(rows, new Set(["sh-1", "sh-2"]))).toEqual(["ord-1"]);
    expect(orderIdsForSelectedScheduleKeys(rows, new Set(["sh-2", "sh-3"]))).toEqual(["ord-1", "ord-2"]);
    expect(orderIdsForSelectedScheduleKeys(rows, new Set(["sh-3"]))).toEqual(["ord-2"]);
  });
});

describe("Rincian Pengiriman qty aggregation", () => {
  test("single day multi-SJ totals from authoritative qty_sj without double PO qty", () => {
    const rows = [
      {
        id: "ord-1",
        order_number: "PO-1",
        customer_name: "A",
        shipment_id: "sh-1",
        sj_label: "SJ-001",
        effective_delivery_date: "2026-10-23",
        status_label: "Terjadwal",
        qty_sj: 50000,
        qty_total: 80000,
      },
      {
        id: "ord-1",
        order_number: "PO-1",
        customer_name: "A",
        shipment_id: "sh-2",
        sj_label: "SJ-002",
        effective_delivery_date: "2026-10-23",
        status_label: "Terjadwal",
        qty_sj: 30000,
        qty_total: 80000,
      },
    ];
    const summary = buildDeliveryDetailsSummary(rows, { dateFrom: "2026-10-23", dateTo: "2026-10-23" });
    expect(summary.sjCount).toBe(2);
    expect(summary.grandTotals).toEqual([{ unit: "", qty: 80000 }]);
    expect(summary.grandTotalLabel).toBe(formatScheduleQty(80000, ""));
    expect(summary.days).toHaveLength(1);
    expect(summary.days[0].rows.map((r) => r.qty)).toEqual([50000, 30000]);
    // Must not use PO qty_total twice (80000*2).
    expect(summary.grandTotals[0].qty).not.toBe(160000);
  });

  test("multi-day grouping and inclusive range semantics are caller-filtered", () => {
    const rows = [
      { id: "a", shipment_id: "1", sj_label: "SJ-A", order_number: "PO-A", customer_name: "X", effective_delivery_date: "2026-10-12", qty_sj: 100, status_label: "Terjadwal" },
      { id: "b", shipment_id: "2", sj_label: "SJ-B", order_number: "PO-B", customer_name: "Y", effective_delivery_date: "2026-10-15", qty_sj: 250, status_label: "Terjadwal" },
      { id: "c", shipment_id: "3", sj_label: "SJ-C", order_number: "PO-C", customer_name: "Z", effective_delivery_date: "2026-10-16", qty_sj: 999, status_label: "Terjadwal" },
    ];
    const inRange = rows.filter((r) => matchesDeliveryScheduleFilters(r, { dateFrom: "2026-10-12", dateTo: "2026-10-15" }));
    const summary = buildDeliveryDetailsSummary(inRange, { dateFrom: "2026-10-12", dateTo: "2026-10-15" });
    expect(summary.sjCount).toBe(2);
    expect(summary.days.map((d) => d.date)).toEqual(["2026-10-12", "2026-10-15"]);
    expect(summary.grandTotals[0].qty).toBe(350);
    expect(summary.days.find((d) => d.date === "2026-10-16")).toBeFalsy();
  });

  test("mixed units are not summed across satuan", () => {
    const row = {
      id: "ord",
      shipment_id: "sh",
      sj_label: "SJ-M",
      order_number: "PO-M",
      customer_name: "Mix",
      effective_delivery_date: "2026-10-23",
      status_label: "Terjadwal",
      qty_sj: 150,
      sj_item_lines: [
        { qty: 100, unit: "pcs" },
        { qty: 5, unit: "box" },
      ],
    };
    expect(scheduleRowQtyParts(row)).toEqual([
      { unit: "pcs", qty: 100 },
      { unit: "box", qty: 5 },
    ]);
    const summary = buildDeliveryDetailsSummary([row], {});
    expect(summary.grandTotals).toEqual([
      { unit: "pcs", qty: 100 },
      { unit: "box", qty: 5 },
    ]);
    expect(summary.grandTotalLabel).toContain("pcs");
    expect(summary.grandTotalLabel).toContain("box");
  });

  test("empty filtered set yields zero totals", () => {
    const summary = buildDeliveryDetailsSummary([], { dateFrom: "2026-10-01", dateTo: "2026-10-02" });
    expect(summary.empty).toBe(true);
    expect(summary.sjCount).toBe(0);
    expect(summary.grandTotalLabel).toBe(formatScheduleQty(0, ""));
  });

  test("missing qty_sj treated as 0; never fabricates from percent", () => {
    const row = {
      id: "ord",
      shipment_id: "sh",
      sj_label: "SJ-Z",
      order_number: "PO-Z",
      customer_name: "Z",
      effective_delivery_date: "2026-10-23",
      percent_delivered: 50,
      qty_total: 1000,
    };
    expect(scheduleRowQtyParts(row)).toEqual([{ unit: "", qty: 0 }]);
  });
});
