import {
  EMPTY_SHIP_FILTERS,
  EMPTY_SHIP_SORT,
  SHIP_SORT_KEYS,
  clampPercent,
  countActiveShipFilters,
  cycleShipSort,
  formatShipDate,
  matchesShipFilters,
  sortShipRows,
  terkirimTone,
} from "./shipmentStatus";
import {
  pruneSelection,
  selectAllVisible,
  toggleId,
  visibleRowIds,
} from "./rowSelection";

describe("Status Pengiriman display", () => {
  test("50% terkirim is mid/yellow band", () => {
    expect(clampPercent(50)).toBe(50);
    expect(terkirimTone(50)).toBe("mid");
  });

  test("75% ready uses clamp", () => {
    expect(clampPercent(75)).toBe(75);
  });

  test("0% stays empty", () => {
    expect(clampPercent(0)).toBe(0);
    expect(terkirimTone(0)).toBe("empty");
  });

  test("over 100 is capped visually", () => {
    expect(clampPercent(120)).toBe(100);
    expect(terkirimTone(120)).toBe("full");
  });

  test("missing date is dash", () => {
    expect(formatShipDate(null)).toBe("-");
    expect(formatShipDate("")).toBe("-");
  });

  test("order date displays dd/mm/yy", () => {
    expect(formatShipDate("2026-08-26")).toBe("26/08/26");
  });
});

const baseRow = {
  id: "1",
  customer_name: "Kedai Kopi Senja",
  order_number: "PO-7788",
  order_date: "2026-08-20",
  delivery_date: "2026-08-26",
  percent_delivered: 50,
  percent_ready_to_ship: 75,
};

describe("Status Pengiriman header filters", () => {
  test("customer partial case-insensitive", () => {
    expect(matchesShipFilters(baseRow, { customer: "kopi" })).toBe(true);
    expect(matchesShipFilters(baseRow, { customer: "XYZ" })).toBe(false);
  });

  test("no PO partial", () => {
    expect(matchesShipFilters(baseRow, { po: "778" })).toBe(true);
    expect(matchesShipFilters(baseRow, { po: "999" })).toBe(false);
  });

  test("legacy single order/delivery date still works", () => {
    expect(matchesShipFilters(baseRow, { customer: "senja", po: "PO-77", orderDate: "2026-08-20", deliveryDate: "2026-08-26" })).toBe(true);
    expect(matchesShipFilters(baseRow, { orderDate: "2026-08-21" })).toBe(false);
    expect(matchesShipFilters(baseRow, { deliveryDate: "2026-08-25" })).toBe(false);
  });

  test("order date range", () => {
    expect(matchesShipFilters(baseRow, { orderDateFrom: "2026-08-01", orderDateTo: "2026-08-31" })).toBe(true);
    expect(matchesShipFilters(baseRow, { orderDateFrom: "2026-08-21", orderDateTo: "2026-08-31" })).toBe(false);
  });

  test("delivery date range and presence", () => {
    expect(matchesShipFilters(baseRow, { deliveryDateFrom: "2026-08-26", deliveryDateTo: "2026-08-26" })).toBe(true);
    expect(matchesShipFilters(baseRow, { deliveryDatePresence: "has" })).toBe(true);
    expect(matchesShipFilters(baseRow, { deliveryDatePresence: "none" })).toBe(false);
    expect(matchesShipFilters({ ...baseRow, delivery_date: null }, { deliveryDatePresence: "none" })).toBe(true);
    expect(matchesShipFilters({ ...baseRow, delivery_date: null }, { deliveryDateFrom: "2026-08-01" })).toBe(false);
  });

  test("cabang and gudang default Semua pass", () => {
    expect(matchesShipFilters(baseRow, { cabang: "", gudang: "" })).toBe(true);
  });

  test("terkirim uses canonical percent_delivered", () => {
    expect(matchesShipFilters(baseRow, { terkirim: "partial" })).toBe(true);
    expect(matchesShipFilters(baseRow, { terkirim: "none" })).toBe(false);
    expect(matchesShipFilters({ ...baseRow, percent_delivered: 0 }, { terkirim: "none" })).toBe(true);
    expect(matchesShipFilters({ ...baseRow, percent_delivered: 100 }, { terkirim: "full" })).toBe(true);
    expect(matchesShipFilters({ ...baseRow, percent_delivered: 120 }, { terkirim: "full" })).toBe(true);
  });

  test("dapat dikirim uses canonical percent_ready_to_ship", () => {
    expect(matchesShipFilters(baseRow, { dapatDikirim: "partial" })).toBe(true);
    expect(matchesShipFilters({ ...baseRow, percent_ready_to_ship: 0 }, { dapatDikirim: "none" })).toBe(true);
    expect(matchesShipFilters({ ...baseRow, percent_ready_to_ship: 100 }, { dapatDikirim: "full" })).toBe(true);
  });

  test("multiple filters combine with AND", () => {
    expect(matchesShipFilters(baseRow, {
      customer: "kopi",
      po: "778",
      terkirim: "partial",
      dapatDikirim: "partial",
      orderDateFrom: "2026-08-20",
      orderDateTo: "2026-08-20",
    })).toBe(true);
    expect(matchesShipFilters(baseRow, {
      customer: "kopi",
      terkirim: "full",
    })).toBe(false);
  });

  test("active filter count ignores defaults", () => {
    expect(countActiveShipFilters(EMPTY_SHIP_FILTERS)).toBe(0);
    expect(countActiveShipFilters({ ...EMPTY_SHIP_FILTERS, customer: "a" })).toBe(1);
    expect(countActiveShipFilters({
      ...EMPTY_SHIP_FILTERS,
      customer: "a",
      po: "1",
      terkirim: "partial",
    })).toBe(3);
    expect(countActiveShipFilters({
      ...EMPTY_SHIP_FILTERS,
      orderDateFrom: "2026-08-01",
      orderDateTo: "2026-08-31",
    })).toBe(1);
  });
});

describe("Status Pengiriman header sort", () => {
  const rows = [
    { id: "b", customer_name: "Beta", order_number: "PO-10", order_date: "2026-08-20", delivery_date: "2026-08-26", percent_delivered: 50, percent_ready_to_ship: 75 },
    { id: "a", customer_name: "Alpha", order_number: "PO-2", order_date: "2026-08-10", delivery_date: null, percent_delivered: 0, percent_ready_to_ship: 100 },
    { id: "z", customer_name: "", order_number: "PO-9", order_date: null, delivery_date: "2026-08-16", percent_delivered: 100, percent_ready_to_ship: 0 },
    { id: "c", customer_name: "gamma", order_number: "PO-3", order_date: "2026-08-15", delivery_date: "2026-08-15", percent_delivered: 25, percent_ready_to_ship: 50 },
  ];

  test("cycle ASC → DESC → NONE", () => {
    const a1 = cycleShipSort(EMPTY_SHIP_SORT, SHIP_SORT_KEYS.customer);
    expect(a1).toEqual({ key: "customer", direction: "asc" });
    const a2 = cycleShipSort(a1, SHIP_SORT_KEYS.customer);
    expect(a2).toEqual({ key: "customer", direction: "desc" });
    const a3 = cycleShipSort(a2, SHIP_SORT_KEYS.customer);
    expect(a3).toEqual(EMPTY_SHIP_SORT);
  });

  test("switching column starts ASC and keeps one active key", () => {
    const s1 = cycleShipSort(EMPTY_SHIP_SORT, SHIP_SORT_KEYS.customer);
    const s2 = cycleShipSort(s1, SHIP_SORT_KEYS.po_number);
    expect(s2).toEqual({ key: "po_number", direction: "asc" });
  });

  test("customer ASC / DESC and empty last", () => {
    expect(sortShipRows(rows, { key: "customer", direction: "asc" }).map((r) => r.id)).toEqual(["a", "b", "c", "z"]);
    expect(sortShipRows(rows, { key: "customer", direction: "desc" }).map((r) => r.id)).toEqual(["c", "b", "a", "z"]);
  });

  test("third click NONE restores default order", () => {
    const sorted = sortShipRows(rows, { key: "customer", direction: "asc" });
    const none = sortShipRows(sorted, EMPTY_SHIP_SORT);
    expect(none.map((r) => r.id)).toEqual(sorted.map((r) => r.id));
    expect(sortShipRows(rows, EMPTY_SHIP_SORT).map((r) => r.id)).toEqual(["b", "a", "z", "c"]);
  });

  test("NO PESANAN natural ASC / DESC", () => {
    expect(sortShipRows(rows, { key: "po_number", direction: "asc" }).map((r) => r.order_number))
      .toEqual(["PO-2", "PO-3", "PO-9", "PO-10"]);
    expect(sortShipRows(rows, { key: "po_number", direction: "desc" }).map((r) => r.order_number))
      .toEqual(["PO-10", "PO-9", "PO-3", "PO-2"]);
  });

  test("TANGGAL oldest/newest and missing last", () => {
    expect(sortShipRows(rows, { key: "order_date", direction: "asc" }).map((r) => r.id)).toEqual(["a", "c", "b", "z"]);
    expect(sortShipRows(rows, { key: "order_date", direction: "desc" }).map((r) => r.id)).toEqual(["b", "c", "a", "z"]);
  });

  test("TGL PENGIRIMAN oldest/newest and missing last both directions", () => {
    expect(sortShipRows(rows, { key: "delivery_date", direction: "asc" }).map((r) => r.id)).toEqual(["c", "z", "b", "a"]);
    expect(sortShipRows(rows, { key: "delivery_date", direction: "desc" }).map((r) => r.id)).toEqual(["b", "z", "c", "a"]);
  });

  test("TERKIRIM numeric ASC/DESC uses percent_delivered", () => {
    expect(sortShipRows(rows, { key: "shipped", direction: "asc" }).map((r) => r.percent_delivered))
      .toEqual([0, 25, 50, 100]);
    expect(sortShipRows(rows, { key: "shipped", direction: "desc" }).map((r) => r.percent_delivered))
      .toEqual([100, 50, 25, 0]);
  });

  test("DAPAT DIKIRIM numeric ASC/DESC uses percent_ready_to_ship", () => {
    expect(sortShipRows(rows, { key: "shippable", direction: "asc" }).map((r) => r.percent_ready_to_ship))
      .toEqual([0, 50, 75, 100]);
    expect(sortShipRows(rows, { key: "shippable", direction: "desc" }).map((r) => r.percent_ready_to_ship))
      .toEqual([100, 75, 50, 0]);
  });

  test("stable equal-value ordering and same customer keeps independent PO rows", () => {
    const same = [
      { id: "1", customer_name: "PT Test Customer", order_number: "PO-A", percent_delivered: 10, percent_ready_to_ship: 0 },
      { id: "2", customer_name: "PT Test Customer", order_number: "PO-B", percent_delivered: 10, percent_ready_to_ship: 0 },
      { id: "3", customer_name: "RENEA COFFEE EXAMPLE", order_number: "PO-C", percent_delivered: 10, percent_ready_to_ship: 0 },
      { id: "4", customer_name: "RENEA COFFEE EXAMPLE", order_number: "PO-D", percent_delivered: 10, percent_ready_to_ship: 0 },
    ];
    const byPct = sortShipRows(same, { key: "shipped", direction: "asc" });
    expect(byPct.map((r) => r.id)).toEqual(["1", "2", "3", "4"]);
    const byCust = sortShipRows(same, { key: "customer", direction: "asc" });
    expect(byCust.filter((r) => r.customer_name === "PT Test Customer").map((r) => r.id)).toEqual(["1", "2"]);
    expect(byCust.filter((r) => r.customer_name === "RENEA COFFEE EXAMPLE").map((r) => r.id)).toEqual(["3", "4"]);
  });

  test("sort does not mutate source array or row identity", () => {
    const original = rows.slice();
    const sorted = sortShipRows(rows, { key: "customer", direction: "asc" });
    expect(rows).toEqual(original);
    expect(sorted[0]).toBe(rows[1]);
    expect(sorted).not.toBe(rows);
  });

  test("null customer name is safe", () => {
    const withNull = [
      { id: "1", customer_name: null, order_number: "A" },
      { id: "2", customer_name: "Zed", order_number: "B" },
    ];
    expect(() => sortShipRows(withNull, { key: "customer", direction: "asc" })).not.toThrow();
    expect(sortShipRows(withNull, { key: "customer", direction: "asc" }).map((r) => r.id)).toEqual(["2", "1"]);
  });
});

describe("Status Pengiriman filter + sort + select-all", () => {
  const rows = [
    { id: "1", customer_name: "Beta", order_number: "PO-1", order_date: "2026-08-10", delivery_date: null, percent_delivered: 0, percent_ready_to_ship: 0 },
    { id: "2", customer_name: "Alpha", order_number: "PO-2", order_date: "2026-08-20", delivery_date: "2026-08-26", percent_delivered: 100, percent_ready_to_ship: 50 },
    { id: "3", customer_name: "Charlie", order_number: "PO-3", order_date: "2026-08-15", delivery_date: "2026-08-16", percent_delivered: 40, percent_ready_to_ship: 100 },
  ];

  test("filter then sort work together", () => {
    const filtered = rows.filter((r) => matchesShipFilters(r, { terkirim: "partial" }));
    const sorted = sortShipRows(filtered, { key: "customer", direction: "asc" });
    expect(sorted.map((r) => r.id)).toEqual(["3"]);
  });

  test("Reset Filter defaults do not require clearing sort helpers", () => {
    const active = {
      ...EMPTY_SHIP_FILTERS,
      customer: "x",
      po: "y",
      terkirim: "partial",
      dapatDikirim: "full",
      deliveryDatePresence: "has",
    };
    expect(countActiveShipFilters(active)).toBe(5);
    expect(countActiveShipFilters(EMPTY_SHIP_FILTERS)).toBe(0);
    const sort = { key: "customer", direction: "desc" };
    expect(sortShipRows(rows, sort).map((r) => r.id)[0]).toBe("3");
  });

  test("select-all applies only to currently displayed filtered rows", () => {
    const filtered = rows.filter((r) => matchesShipFilters(r, { customer: "Alpha" }));
    const sorted = sortShipRows(filtered, { key: "customer", direction: "desc" });
    const ids = visibleRowIds(sorted);
    expect(ids).toEqual(["2"]);
    const selected = selectAllVisible(ids, new Set());
    expect([...selected]).toEqual(["2"]);
    expect(selected.has("1")).toBe(false);
    expect(selected.has("3")).toBe(false);
  });

  test("selection identity survives reorder", () => {
    let selected = toggleId(new Set(), "2");
    selected = toggleId(selected, "3");
    const reordered = sortShipRows(rows, { key: "customer", direction: "asc" });
    expect(selected.has("2")).toBe(true);
    expect(selected.has("3")).toBe(true);
    expect(reordered.map((r) => r.id)).toEqual(["2", "1", "3"]);
    const pruned = pruneSelection(selected, visibleRowIds(reordered));
    expect(pruned.has("2")).toBe(true);
    expect(pruned.has("3")).toBe(true);
  });

  test("hidden filtered rows are pruned from selection", () => {
    const allIds = visibleRowIds(rows);
    let selected = selectAllVisible(allIds, new Set());
    expect(selected.size).toBe(3);
    const filteredIds = visibleRowIds(rows.filter((r) => matchesShipFilters(r, { terkirim: "full" })));
    selected = pruneSelection(selected, filteredIds);
    expect([...selected]).toEqual(["2"]);
  });

  test("existing table percent values remain unchanged by sort/filter helpers", () => {
    const sorted = sortShipRows(rows, { key: "customer", direction: "asc" });
    expect(sorted.find((r) => r.id === "2").percent_delivered).toBe(100);
    expect(sorted.find((r) => r.id === "3").percent_ready_to_ship).toBe(100);
  });
});
