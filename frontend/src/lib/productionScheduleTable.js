/** Canonical Jadwal Produksi table columns — shared by desktop /produksi and mobile /m/schedule. */

import { FILTER_DEFAULTS } from "./productionScheduleFilters";

export const PRODUCTION_SCHEDULE_TABLE_COLS = [
  { key: "customer", label: "Customer", filter: "customer" },
  { key: "deadline", label: "Deadline", fit: true, filter: "deadline", sort: "deadline" },
  { key: "deadline_ship", label: "Deadline Pengiriman", testid: "prod-th-deadline-effective" },
  { key: "work_date", label: "Tgl Pengerjaan", testid: "prod-th-work-date", filter: "work_date", sort: "work_date" },
  { key: "prio", label: "Prio", fit: true, filter: "prio" },
  { key: "buffer", label: "Buffer", fit: true, testid: "prod-th-buffer", filter: "buffer" },
  { key: "po", label: "No PO", fit: true, filter: "po" },
  { key: "outlet", label: "Outlet" },
  { key: "qty", label: "Total Qty", num: true },
  { key: "qty_cetak", label: "Qty Cetak", num: true },
  { key: "operator", label: "Operator", filter: "operator" },
  { key: "design", label: "Design", fit: true },
  { key: "payment", label: "Payment", fit: true },
  { key: "hold", label: "HOLD", fit: true, filter: "hold" },
  { key: "screen", label: "Qty Screen", num: true, filter: "screener" },
  { key: "helper", label: "Persiapan", fit: true, filter: "helper" },
  { key: "packing", label: "Packing", fit: true, filter: "packing" },
  { key: "sj", label: "Surat Jalan", fit: true },
  { key: "driver", label: "Driver" },
  { key: "status", label: "Status Akhir", fit: true, filter: "status" },
];

export function productionDashboardRequestParams(filters = {}) {
  const f = { ...FILTER_DEFAULTS, ...filters };
  return {
    include_cancelled: Boolean(f.includeCancelled),
    include_completed: Boolean(f.includeCompleted),
  };
}
