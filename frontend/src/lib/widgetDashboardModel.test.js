import {
  chartWithheld,
  failedModel,
  formatLongDate,
  mapCash,
  mapCustomerRank,
  mapDeliveryQty,
  mapProduction,
  mapProducts,
  mapPurchases,
  mapReconciliations,
  mapSales,
  mapSchedule,
  mapSettlement,
  mapShipToday,
  mapToday,
} from "./widgetDashboardModel";
import { shouldApplyDashboardResponse } from "./financialDashboard";
import { WIDGET_IDS } from "../components/widgetDashboard/widgetDashboardCatalog";

test("catalog has the twenty widgets", () => {
  expect(WIDGET_IDS).toHaveLength(20);
  expect(new Set(WIDGET_IDS).size).toBe(20);
});

test("today average stays unavailable unless the set is complete", () => {
  expect(mapToday({ status: "complete", amount: 8000, invoice_count: 4 }, "4 Okt 2026").average).toBe(2000);
  expect(mapToday({ status: "incomplete", amount: 8000, invoice_count: 4 }, "4 Okt 2026").average).toBeNull();
  expect(mapToday({ status: "zero", amount: 0, invoice_count: 2 }, "4 Okt 2026").average).toBeNull();
  expect(mapToday({ status: "error", amount: null, invoice_count: null }, "4 Okt 2026").status).toBe("error");
});

test("customer rank and products keep the service rows", () => {
  const rank = mapCustomerRank({
    status: "incomplete",
    groups: [{ key: "cust-1", label: "Kedai", amount: 6500, invoice_count: 2 }],
    detail: "tanpa identitas",
  });
  expect(rank.rows[0]).toMatchObject({ key: "cust-1", amount: 6500, invoices: 2 });
  const products = mapProducts({
    status: "complete",
    items: [{ key: "a", label: "Lima", amount: 1 }],
    all_items: [{ key: "a", label: "Lima", amount: 1 }, { key: "b", label: "Enam", amount: 2 }],
  });
  expect(products.rows.map((row) => row.name)).toEqual(["Lima", "Enam"]);
});

test("settlement bars use full invoice value, not the remaining balance", () => {
  const mapped = mapSettlement({
    status: "complete",
    settled_grand_total: 500,
    settled_count: 1,
    open_grand_total: 1200,
    open_outstanding: 800,
    open_count: 2,
  });
  expect(mapped.scale).toBe("total");
  expect(mapped.settledGrand).toBe(500);
  expect(mapped.openGrand).toBe(1200);
  expect(mapped.openGrand).not.toBe(800);
  expect(mapSettlement({ status: "complete", open_outstanding: 800 }).status).toBe("unavailable");
});

test("sales share stays absent unless the service sends it", () => {
  const incomplete = mapSales({
    status: "incomplete",
    total_omzet: 7300,
    groups: [{ label: "Belum ditetapkan", omzet: 7300, invoice_count: 3, share: null }],
  });
  expect(incomplete.rows[0].share).toBeNull();
  expect(incomplete.rows[0].amount).toBe(7300);
  const complete = mapSales({
    status: "complete",
    total_omzet: 1000,
    groups: [{ label: "Ayu", omzet: 1000, invoice_count: 1, share: 100 }],
  });
  expect(complete.rows[0].share).toBe(100);
});

test("product share comes from the service and is not rebuilt from the top five", () => {
  const products = mapProducts({
    status: "incomplete",
    identified_amount: 5700,
    items: [{ key: "a", label: "Utama", amount: 4500, share: 78.9 }],
    all_items: [
      { key: "a", label: "Utama", amount: 4500, share: 78.9 },
      { key: "b", label: "Kecil", amount: 50, share: 0.9 },
    ],
  });
  expect(products.rows.map((row) => row.share)).toEqual([78.9, 0.9]);
  expect(products.status).toBe("incomplete");
});

test("purchase bars are invoice values and failure has no amount", () => {
  const body = mapPurchases({
    status: "complete",
    amount: 230,
    paid_amount: 100,
    unpaid_amount: 130,
    paid_count: 1,
    unpaid_count: 2,
  });
  expect(body.settledGrand + body.openOutstanding).toBe(230);
  expect(failedModel({ response: { status: 500, data: { detail: "Gagal" } } })).toEqual({ status: "error", detail: "Gagal" });
  expect(failedModel({ response: { status: 403 } }).status).toBe("forbidden");
  expect(failedModel({}).amount).toBeUndefined();
});

test("cash keeps internal transfers out of the net figure", () => {
  const cash = mapCash({
    status: "complete",
    cash_in: 30000,
    cash_out: 65000,
    net_change: -35000,
    internal_transfer_volume: 10000,
  });
  expect(cash.net).toBe(-35000);
  expect(cash.cashIn).toBe(30000);
  expect(cash.internal).toBe(10000);
  expect(chartWithheld([cash.cashIn, cash.net])).toBe(true);
});

test("schedule uses service status and effective date", () => {
  const mapped = mapSchedule({
    rows: [
      { id: "1", status: "terlambat", customer_name: "Terlambat", order_number: "PO-1", tanggal_pengiriman: "2026-10-01", created_at: "2026-10-04" },
      { id: "2", status: "terjadwal", customer_name: "Buffer", order_number: "PO-2", tanggal_pengiriman: "2026-09-01" },
      { id: "3", status: "terjadwal", customer_name: "Besok", order_number: "PO-3", tanggal_pengiriman: "2026-10-06" },
      { id: "4", status: "hari_ini", customer_name: "Hari ini", order_number: "PO-4", tanggal_pengiriman: "2026-10-04" },
    ],
  }, "2026-10-04");
  expect(mapped.late.rows.map((row) => row.order)).toEqual(["PO-1"]);
  expect(mapped.next.rows.map((row) => row.order)).toEqual(["PO-3"]);
  expect(formatLongDate("2026-10-04")).toBe("4 Okt 2026");
});

test("ship today and production do not invent totals from a cut list", () => {
  const today = mapShipToday({
    shipments_today: { date: "2026-10-04", total: 9, has_more: true, preview: [{ customer: "A", po_number: "PO-1", tanggal_pengiriman: "2026-10-04" }] },
  });
  expect(today.status).toBe("incomplete");
  expect(today.total).toBe(9);
  expect(today.rows).toHaveLength(1);
  const production = mapProduction({
    rows: [
      { id: "p1", po_number: "PO-9", customer: "Nusantara", work_date: "2026-10-06", operators: [{ line_name: "Lunch Box" }] },
      { id: "p2", po_number: "PO-8", customer: "Lama", work_date: "2026-10-01", operators: [] },
    ],
  }, "2026-10-04");
  expect(production.rows.map((row) => row.order)).toEqual(["PO-9"]);
  expect(production.rows[0].name).toBe("Lunch Box");
  expect(production.rows[0].date).toBe("6 Okt 2026");
});

test("reconciliation empty is not a matched zero and finalized keeps a difference", () => {
  expect(mapReconciliations({ status: "empty", items: [] }).status).toBe("empty");
  const row = mapReconciliations({
    status: "complete",
    items: [{ id: "1", cash_bank_name: "Kas BCA", number: "RK-1", reconciliation_date: "2026-10-04", status: "finalized", difference: 500 }],
  }).rows[0];
  expect(row.statusLabel).toBe("Finalized");
  expect(row.amount).toBe(500);
  expect(row.date).toBe("4 Okt 2026");
  const both = mapReconciliations({
    status: "complete",
    items: [{ id: "1", cash_bank_name: "Kas BCA", number: "RK-1", reconciliation_date: "2026-10-04", status: "draft", difference: 5 }],
    pairs: [{ id: "p1", cash_bank_name: "Kas Uji", date: "2026-10-20", direction: "in", amount: 999, journal_line_id: "secret" }],
  });
  expect(both.pairs).toHaveLength(1);
  expect(both.pairs[0].name).toBe("Kas Uji");
  expect(both.pairs[0].amount).toBeUndefined();
  expect(both.rows).toHaveLength(1);
});

test("delivery qty keeps today overdue and units separate", () => {
  const model = mapDeliveryQty({
    status: "complete",
    days: 7,
    today: "2026-10-24",
    series: [
      {
        unit: "PCS",
        today_qty: 60,
        today_po_count: 2,
        overdue_qty: 15,
        overdue_po_count: 1,
        points: [
          { date: "2026-10-24", qty: 60, po_count: 2 },
          { date: "2026-10-25", qty: 10, po_count: 1 },
          { date: "2026-10-26", qty: 17000, po_count: 6 },
        ],
      },
      {
        unit: "tanpa_satuan",
        today_qty: 7,
        today_po_count: 1,
        overdue_qty: 0,
        overdue_po_count: 0,
        points: [{ date: "2026-10-24", qty: 7, po_count: 1 }],
      },
    ],
    notes: ["Qty tanpa satuan tidak dijumlahkan dan tidak dianggap pcs."],
  });
  expect(model.todayLabel).toBe("24 Okt 2026");
  expect(model.series[0]).toMatchObject({ label: "pcs", todayQty: 60, overdueQty: 15, todayPoCount: 2 });
  expect(model.series[1].label).toBe("tanpa satuan");
  expect(model.series[0].points[1]).toMatchObject({
    label: "25 Okt 2026",
    shortLabel: "25 Okt",
    tipLabel: "Minggu, 25 Oktober 2026",
    poCount: 1,
  });
  expect(model.series[0].points[2]).toMatchObject({
    tipLabel: "Senin, 26 Oktober 2026",
    qty: 17000,
    poCount: 6,
  });
});

test("a later request id wins", () => {
  expect(shouldApplyDashboardResponse(1, 2)).toBe(false);
  expect(shouldApplyDashboardResponse(2, 2)).toBe(true);
});
