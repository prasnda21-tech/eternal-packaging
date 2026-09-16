import { STAGES } from "./constants";
import { dateKey } from "./shipmentStatus";
import { resolveTimelineChip } from "./timelineChip";

export const EMPTY_STATUS_PO_FILTERS = { po: "", customer: "", status: "", dateFrom: "", dateTo: "" };

export const STATUS_PO_VIEWS = {
  active: "PO Aktif",
  urgent: "PO Urgent",
  due_today: "Deadline Hari Ini",
  today: "Deadline Hari Ini",
  h1: "H-1 Pengiriman",
  h6: "Deadline ≤ 6 Jam",
  overdue: "Overdue",
  design: "Menunggu Approval Design",
  payment: "Menunggu Approval Payment",
  hold: "HOLD",
  completed: "Selesai (Terkirim)",
  work_today: "Pengerjaan Hari Ini",
  delivery_today: "Pengiriman Hari Ini",
};

export const STATUS_PO_COLUMNS = ["No PO", "Tanggal", "Pelanggan", "Item Line", "Status Saat Ini", "Progress", "Aksi"];

export function statusPoViewLabel(view) {
  return STATUS_PO_VIEWS[view] || "Semua PO";
}

/** Effective workflow status from evidence — not idx < current_stage. */
export function effectivePoStatus(row) {
  if (row?.cancelled) return "Cancelled";
  if (row?.status) return row.status;
  const order = row?.order || row;
  const current = Number(order?.current_stage || 0);
  let currentLabel = null;
  for (let idx = STAGES.length - 1; idx >= 0; idx -= 1) {
    const chip = resolveTimelineChip(idx, order, current);
    if (chip.state === "current") {
      currentLabel = chip.label && chip.label !== STAGES[idx]
        ? `${STAGES[idx]} · ${chip.label}`
        : STAGES[idx];
      break;
    }
    if (!currentLabel && (chip.state === "done" || chip.state === "bypass")) {
      currentLabel = STAGES[idx];
    }
  }
  return currentLabel || order?.status_label || STAGES[current] || "—";
}

export function deliveredPercentLabel(pct) {
  const n = Number(pct);
  if (!Number.isFinite(n) || n <= 0) return "0%";
  return `${Math.round(n)}%`;
}

export function statusPoFilterOptions(rows, selected) {
  const set = new Set();
  for (const r of rows || []) {
    if (r?.status) set.add(r.status);
  }
  if (selected) set.add(selected);
  return [...set].sort((a, b) => a.localeCompare(b, "id"));
}

/** Same AND + partial text pattern as Status Pengiriman (`matchesShipFilters`). */
export function matchesStatusPoFilters(row, filters) {
  const po = (filters?.po || "").trim().toLowerCase();
  const customer = (filters?.customer || "").trim().toLowerCase();
  const status = (filters?.status || "").trim();
  const dateFrom = (filters?.dateFrom || "").trim();
  const dateTo = (filters?.dateTo || "").trim();
  if (po && !(row?.po_number || "").toLowerCase().includes(po)) return false;
  if (customer && !(row?.customer || "").toLowerCase().includes(customer)) return false;
  if (status && (row?.status || "") !== status) return false;
  const key = dateKey(row?.order_date);
  if (dateFrom && (!key || key < dateFrom)) return false;
  if (dateTo && (!key || key > dateTo)) return false;
  return true;
}
