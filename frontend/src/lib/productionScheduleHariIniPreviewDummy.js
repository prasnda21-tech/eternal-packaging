/** Local preview-only dummy rows for Jadwal Produksi date-filter "Hari Ini". Never used in production builds. */

import { productionTodayKey, toDateKey } from "./productionScheduleFilters";

export const HARI_INI_PREVIEW_DUMMY_COUNT = 3;
export const HARI_INI_PREVIEW_DUMMY_POS = Object.freeze(["PO-HI-D1", "PO-HI-D2", "PO-HI-D3"]);

export function shiftProductionDateKey(ymd, days) {
  const key = toDateKey(ymd);
  if (!key) return null;
  const [y, m, d] = key.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + Number(days || 0)));
  const yy = dt.getUTCFullYear();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(dt.getUTCDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

export function isProductionScheduleHariIniPreviewHost(win = (typeof window !== "undefined" ? window : undefined)) {
  if (process.env.NODE_ENV !== "development") return false;
  if (!win || !win.location) return false;
  return win.location.hostname === "localhost" && String(win.location.port) === "3003";
}

function dummyRow({ id, po, customer, workDate, deadline, operatorName, urgent = false }) {
  return {
    id,
    po_number: po,
    customer,
    outlet: "Preview Outlet",
    deadline: `${deadline}T00:00:00+07:00`,
    delivery_deadline_effective: `${deadline}T00:00:00+07:00`,
    delivery_deadline_source: "manual",
    delivery_deadline_status: "ok",
    urgent,
    work_date: workDate,
    current_stage: 8,
    status_final: "Operator",
    total_quantity: 1000,
    print_qty_total: 1000,
    operator_assigned_qty: 1000,
    design_status: "Approved",
    payment_status: "Approved",
    packing_status: "Belum",
    operators: [
      {
        split_id: `${id}-op`,
        line_idx: 0,
        line_id: `${id}-line`,
        line_name: "Cup",
        operator_name: operatorName,
        quantity: 1000,
        qty_completed: 0,
        work_date: workDate,
        line_printable: true,
      },
    ],
  };
}

export function buildProductionScheduleHariIniPreviewDummyRows(now = new Date()) {
  const today = productionTodayKey(now);
  const yesterday = shiftProductionDateKey(today, -1);
  const tomorrow = shiftProductionDateKey(today, 1);
  return [
    dummyRow({
      id: "hari-ini-dummy-1",
      po: HARI_INI_PREVIEW_DUMMY_POS[0],
      customer: "Dummy Hari Ini 1",
      workDate: today,
      deadline: today,
      operatorName: "Fauzan",
    }),
    dummyRow({
      id: "hari-ini-dummy-2",
      po: HARI_INI_PREVIEW_DUMMY_POS[1],
      customer: "Dummy Hari Ini 2",
      workDate: today,
      deadline: tomorrow,
      operatorName: "Budi",
    }),
    dummyRow({
      id: "hari-ini-dummy-3",
      po: HARI_INI_PREVIEW_DUMMY_POS[2],
      customer: "Dummy Hari Ini 3",
      workDate: yesterday,
      deadline: today,
      operatorName: "Cici",
    }),
  ];
}

export function mergeHariIniPreviewDummyRows(rows, now = new Date(), win) {
  if (!isProductionScheduleHariIniPreviewHost(win)) return Array.isArray(rows) ? rows : [];
  const dummy = buildProductionScheduleHariIniPreviewDummyRows(now);
  const dummyIds = new Set(dummy.map((r) => r.po_number));
  const rest = (rows || []).filter((r) => !dummyIds.has(r.po_number));
  return [...dummy, ...rest];
}
