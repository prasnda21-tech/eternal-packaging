import { TASK_NAV_PATHS } from "./navAccess";
import { workPanelPath } from "./workPanel";

export const DIVISION_TASK_PAGES = {
  designer: { id: "designer", title: "TUGAS DESIGNER", qtyCols: false, extra: "design", inputColLabel: "Yang Harus Diinput" },
  "kepala-produksi": {
    id: "kepala_produksi",
    title: "TUGAS KEPALA PRODUKSI",
    qtyCols: true,
    extra: "schedule",
    targetLabel: "Qty Item Cetak",
    doneLabel: "Sudah Dijadwalkan",
    remainLabel: "Belum Dijadwalkan",
    inputColLabel: "Yang Harus Dilakukan",
  },
  screener: { id: "screener", title: "Tugas Screener", qtyCols: true },
  helper: { id: "helper", title: "Tugas Helper", qtyCols: true, targetLabel: "Target Helper", doneLabel: "Prepared" },
  operator: { id: "operator", title: "Tugas Operator", qtyCols: true, targetLabel: "Qty Dijadwalkan", doneLabel: "Qty Cetak Aktual" },
  packing: { id: "packing", title: "Tugas Packing", qtyCols: true, targetLabel: "Qty Bisa Dipacking", doneLabel: "Sudah Packing" },
  "surat-jalan": { id: "surat_jalan", title: "Tugas Surat Jalan", qtyCols: false, extra: "sj" },
  loading: { id: "loading", title: "Tugas Loading", qtyCols: true, targetLabel: "Qty Bisa Loading", doneLabel: "Sudah Loading" },
  delivery: { id: "terkirim", title: "Tugas Driver / Terkirim", qtyCols: false, extra: "driver" },
};

export function taskWorkHref(task) {
  const href = workPanelPath(task.order_id, task.division);
  if (task.division === "kepala_produksi" && task.line_idx != null && task.line_idx !== "") {
    return `${href}&line=${encodeURIComponent(task.line_idx)}`;
  }
  return href;
}

export function formatIdQty(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  return v.toLocaleString("id-ID");
}

export function statusTone(code) {
  if (code === "selesai") return "pill-ok";
  if (code === "siap") return "pill-warn";
  if (code === "sebagian") return "pill-muted";
  return "pill-muted";
}

export function taskMatchesQuery(task, q) {
  const s = (q || "").trim().toLowerCase();
  if (!s) return true;
  return [task.order_number, task.customer_name, task.item, task.assigned_user_name]
    .filter(Boolean)
    .some((v) => String(v).toLowerCase().includes(s));
}

export function taskMatchesStatus(task, status) {
  if (!status || status === "semua") return true;
  return task.status === status;
}

export { TASK_NAV_PATHS };
