/** Kepala Produksi Priority Board — shared FE helpers (matches backend KPI keys). */

export const KP_KPI_ORDER = [
  "kirim-hari-ini",
  "terlambat",
  "urgent",
  "hold",
  "perlu-produksi",
  "sisa-produksi",
];

export const KP_KPI_META = {
  "kirim-hari-ini": {
    title: "Kirim Hari Ini",
    description: "PO dengan tanggal kirim efektif hari ini (WIB) yang belum fully delivered.",
    tone: "kpi-warn",
  },
  terlambat: {
    title: "Terlambat",
    description: "PO dengan tanggal kirim efektif sebelum hari ini (WIB), belum fully delivered.",
    tone: "kpi-danger",
  },
  urgent: {
    title: "Urgent",
    description: "PO dengan prioritas eksplisit URGENT (terpisah dari overdue).",
    tone: "kpi-danger",
  },
  hold: {
    title: "HOLD",
    description: "PO dengan HOLD aktif. Jadwalkan diblokir hingga eligible.",
    tone: "kpi-hold",
  },
  "perlu-produksi": {
    title: "Perlu Produksi",
    description: "PO cetak dengan sisa qty produksi valid > 0.",
    tone: "kpi-success",
  },
  "sisa-produksi": {
    title: "Total Sisa Produksi",
    description: "Jumlah sisa qty produksi per satuan (tidak digabung lintas unit).",
    tone: "kpi-success",
  },
};

export function kpDetailPath(kpi) {
  return `/tasks/kepala-produksi/prioritas/${kpi}`;
}

export function formatKpiCardValue(card) {
  if (!card) return "—";
  if (card.metric === "qty_by_unit") {
    if (card.display) return card.display;
    if (card.by_unit && typeof card.by_unit === "object") {
      const parts = Object.entries(card.by_unit).map(
        ([u, v]) => `${Number(v).toLocaleString("id-ID")} ${u}`,
      );
      return parts.length ? parts.join(", ") : "0";
    }
  }
  const n = card.count ?? card.value;
  if (n == null || n === "") return "—";
  return Number(n).toLocaleString("id-ID");
}

export function priorityBadgeClass(label) {
  const k = String(label || "").toUpperCase();
  if (k === "URGENT" || k === "OVERDUE") return "urgent-badge";
  if (k === "HOLD") return "pill sm pill-hold";
  if (k === "DUE TODAY" || k === "H-1") return "standard-badge prod-h1-badge";
  return "standard-badge";
}

export function formatDeliveryDate(row) {
  const d = row?.effective_delivery_date || (row?.delivery_deadline || "").toString().slice(0, 10);
  if (!d || d.length < 10) return "—";
  return d;
}

export function rowMatchesExtraFilters(row, { priority, hold, dateFrom, dateTo } = {}) {
  if (priority === "urgent" && !(row.explicit_urgent || row.urgent || row.priority === "urgent")) {
    return false;
  }
  if (priority === "overdue" && row.delivery_deadline_status !== "overdue") return false;
  if (priority === "normal") {
    const urgent = row.explicit_urgent || row.urgent || row.priority === "urgent";
    const overdue = row.delivery_deadline_status === "overdue";
    if (urgent || overdue || row.authoritative_hold || row.hold) return false;
  }
  if (hold === "yes" && !(row.authoritative_hold || row.hold)) return false;
  if (hold === "no" && (row.authoritative_hold || row.hold)) return false;
  const d = formatDeliveryDate(row);
  if (dateFrom && d !== "—" && d < dateFrom) return false;
  if (dateTo && d !== "—" && d > dateTo) return false;
  return true;
}

export function uniquePoCount(rows) {
  return new Set((rows || []).map((r) => r.order_id).filter(Boolean)).size;
}

export function paginateRows(rows, page, pageSize) {
  const size = Math.max(1, Number(pageSize) || 50);
  const p = Math.max(1, Number(page) || 1);
  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const safe = Math.min(p, pages);
  const start = (safe - 1) * size;
  return {
    page: safe,
    pageSize: size,
    total,
    pages,
    rows: rows.slice(start, start + size),
  };
}
