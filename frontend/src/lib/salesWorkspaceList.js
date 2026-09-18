export const SALES_PAGE_SIZES = [10, 15, 25, 50];
export const SALES_DEFAULT_PAGE_SIZE = 15;

export function paginateRows(rows, page, pageSize) {
  const list = Array.isArray(rows) ? rows : [];
  const size = Number(pageSize) > 0 ? Number(pageSize) : SALES_DEFAULT_PAGE_SIZE;
  const total = list.length;
  const pageCount = Math.max(1, Math.ceil(total / size) || 1);
  const safePage = Math.min(Math.max(1, Number(page) || 1), pageCount);
  const start = (safePage - 1) * size;
  return {
    rows: list.slice(start, start + size),
    page: safePage,
    pageSize: size,
    pageCount,
    total,
    start: total === 0 ? 0 : start + 1,
    end: Math.min(start + size, total),
  };
}

export function formatDeliveryStatusLabel(raw, cancelled = false) {
  if (cancelled) return "Dibatalkan";
  const s = String(raw || "BELUM DIKIRIM").toUpperCase();
  if (s.includes("BATAL")) return "Dibatalkan";
  if (s.includes("SELESAI")) return "Selesai";
  if (s.includes("SEBAGIAN")) return "Sebagian";
  if (s.includes("BELUM")) return "Belum Dikirim";
  return raw || "Belum Dikirim";
}

export function invoiceListMatchesFilter(inv, filter) {
  const status = String(inv?.payment_status || "").toUpperCase();
  const outstanding = Number(inv?.outstanding_amount || 0);
  const due = inv?.due_date || inv?.dueDate || "";
  if (!filter || filter === "all") return true;
  if (filter === "unpaid") return outstanding > 0 && status !== "DP" && status !== "LUNAS";
  if (filter === "partial") return status === "DP" || (outstanding > 0 && Number(inv?.paid_total || 0) > 0);
  if (filter === "paid") return status === "LUNAS" || outstanding <= 0;
  if (filter === "overdue") {
    if (outstanding <= 0 || !due) return false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const dueDate = new Date(due);
    if (Number.isNaN(dueDate.getTime())) return false;
    dueDate.setHours(0, 0, 0, 0);
    return dueDate < today;
  }
  return true;
}

export function receiptListMatchesFilter(receipt, filter) {
  if (!filter || filter === "all") return true;
  const state = String(receipt?.payment_state || "").toUpperCase();
  if (filter === "LUNAS") return state === "LUNAS";
  if (filter === "DP") return state === "DP" || !state;
  return true;
}

export function deliveryDateInRange(row, from, to) {
  if (!from && !to) return true;
  const raw = row?.delivery_date || "";
  if (!raw) return false;
  const day = String(raw).slice(0, 10);
  if (from && day < from) return false;
  if (to && day > to) return false;
  return true;
}
