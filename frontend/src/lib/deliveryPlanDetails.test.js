/**
 * @jest-environment node
 */
import {
  DEFAULT_PLAN_SORT,
  PLAN_DETAILS_PAGE_SIZE,
  buildDeliveryPlanDetailsSummary,
  buildDeliveryPlanListParams,
  fetchAllDeliveryPlanRows,
} from "./deliveryPlanDetails";

describe("deliveryPlanDetails", () => {
  test("default sort is tanggal_pengiriman DESC", () => {
    expect(DEFAULT_PLAN_SORT).toEqual({ sortBy: "tanggal_pengiriman", sortDir: "desc" });
    expect(PLAN_DETAILS_PAGE_SIZE).toBe(200);
  });

  test("list params include applied filters and max page size", () => {
    const params = buildDeliveryPlanListParams({
      filters: {
        q: " Alpha ",
        source: "manual",
        status: "hari_ini",
        date_from: "2026-10-24",
        date_to: "2026-10-27",
        unit: "pcs",
      },
      colFilters: { customer_name: { op: "contains", value: "X" } },
      page: 2,
    });
    expect(params).toMatchObject({
      page: 2,
      page_size: 200,
      q: "Alpha",
      source: "manual",
      status: "hari_ini",
      date_from: "2026-10-24",
      date_to: "2026-10-27",
      unit: "pcs",
      sort_by: "tanggal_pengiriman",
      sort_dir: "desc",
    });
    expect(params.hf).toEqual(["customer_name:contains:X"]);
  });

  test("summary groups newest date first with PO qty semantics (not SJ)", () => {
    const rows = [
      {
        id: "a",
        po_number: "PO/A",
        customer_name: "A",
        delivery_zone: "JT",
        tanggal_pengiriman: "2026-10-24",
        qty_total: 100,
        qty_delivered: 40,
        qty_remaining: 60,
      },
      {
        id: "b",
        po_number: "PO/B",
        customer_name: "B",
        delivery_zone: "JS",
        tanggal_pengiriman: "2026-10-27",
        qty_total: 50,
        qty_delivered: 0,
        qty_remaining: 50,
      },
      {
        id: "a2",
        po_number: "PO/A-dup-date",
        customer_name: "A2",
        delivery_zone: "",
        tanggal_pengiriman: "2026-10-27",
        qty_total: 10,
        qty_delivered: 10,
        qty_remaining: 0,
      },
    ];
    const summary = buildDeliveryPlanDetailsSummary(rows, {
      dateFrom: "2026-10-24",
      dateTo: "2026-10-27",
    });
    expect(summary.poCount).toBe(3);
    expect(summary.days.map((d) => d.date)).toEqual(["2026-10-27", "2026-10-24"]);
    expect(summary.qtyPesanan).toBe(160);
    expect(summary.qtyTerkirim).toBe(50);
    expect(summary.sisaQty).toBe(110);
    expect(summary.periodLabel).toMatch(/24/);
    expect(summary.days[0].rows).toHaveLength(2);
  });

  test("one PO counted once even if caller passed duplicates", () => {
    // Builder itself does not dedupe — fetchAll does. Summary counts rows given.
    const summary = buildDeliveryPlanDetailsSummary(
      [
        { id: "p1", po_number: "PO/1", tanggal_pengiriman: "2026-10-25", qty_total: 5, qty_delivered: 1, qty_remaining: 4 },
      ],
      {},
    );
    expect(summary.poCount).toBe(1);
    expect(summary.days[0].rows[0].poNumber).toBe("PO/1");
  });

  test("fetchAllDeliveryPlanRows walks every page with same filters and dedupes", async () => {
    const calls = [];
    const fetchPage = async (params) => {
      calls.push({ ...params });
      if (params.page === 1) {
        return {
          rows: [
            { id: "1", po_number: "PO/1", tanggal_pengiriman: "2026-10-27", qty_total: 1, qty_delivered: 0, qty_remaining: 1 },
            { id: "2", po_number: "PO/2", tanggal_pengiriman: "2026-10-26", qty_total: 2, qty_delivered: 0, qty_remaining: 2 },
          ],
          total: 3,
          total_pages: 2,
          totals: { total_po: 3, qty_total: 6, qty_delivered: 0, qty_remaining: 6 },
        };
      }
      return {
        rows: [
          { id: "2", po_number: "PO/2-dup", tanggal_pengiriman: "2026-10-26", qty_total: 2, qty_delivered: 0, qty_remaining: 2 },
          { id: "3", po_number: "PO/3", tanggal_pengiriman: "2026-10-24", qty_total: 3, qty_delivered: 0, qty_remaining: 3 },
        ],
        total: 3,
        total_pages: 2,
        totals: { total_po: 3, qty_total: 6, qty_delivered: 0, qty_remaining: 6 },
      };
    };
    const result = await fetchAllDeliveryPlanRows({
      fetchPage,
      filters: { date_from: "2026-10-24", date_to: "2026-10-27", q: "", source: "", status: "", unit: "" },
      colFilters: {},
    });
    expect(calls).toHaveLength(2);
    expect(calls[0].page_size).toBe(200);
    expect(calls[0].date_from).toBe("2026-10-24");
    expect(calls[1].date_from).toBe("2026-10-24");
    expect(calls[0].sort_dir).toBe("desc");
    expect(result.rows.map((r) => r.id)).toEqual(["1", "2", "3"]);
    expect(result.complete).toBe(true);
    expect(result.total).toBe(3);
  });

  test("fetchAll marks incomplete when unique rows fewer than total after all pages", async () => {
    const fetchPage = async () => ({
      rows: [{ id: "1", po_number: "PO/1", tanggal_pengiriman: "2026-10-25", qty_total: 1, qty_delivered: 0, qty_remaining: 1 }],
      total: 5,
      total_pages: 1,
    });
    const result = await fetchAllDeliveryPlanRows({
      fetchPage,
      filters: { q: "", source: "", status: "", date_from: "", date_to: "", unit: "" },
    });
    expect(result.complete).toBe(false);
    expect(result.rows).toHaveLength(1);
  });
});
