import { STAGES } from "./constants";
import { resolveTimelineChip } from "./timelineChip";

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

export const STATUS_PO_COLUMNS = ["No PO", "Tanggal", "Pelanggan", "Status", "Status Terkirim"];

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
