export function formatRupiah(num) {
  const n = Number(num) || 0;
  return "Rp " + n.toLocaleString("id-ID");
}

export function todayYmd() {
  return new Date().toISOString().slice(0, 10);
}

export function newIdempotencyKey() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function errDetail(ex) {
  const det = ex?.response?.data?.detail;
  if (typeof det === "object" && det) {
    if (Array.isArray(det.errors) && det.errors.length) {
      return [det.message, ...det.errors].filter(Boolean).join(" · ");
    }
    return det.message || JSON.stringify(det);
  }
  return det || ex?.message || "Terjadi kesalahan";
}

/** User-facing load error — avoid raw stack / transport noise. */
export function friendlyLoadError(ex, fallback = "Tidak dapat memuat data. Periksa koneksi server lalu coba lagi.") {
  if (!ex) return fallback;
  if (!ex.response) return fallback;
  const detail = errDetail(ex);
  if (!detail || /network|failed to fetch|econnrefused|timeout/i.test(String(detail))) {
    return fallback;
  }
  return detail;
}

/** Compact receiving status label for operators. */
export function shortReceivingStatus(status) {
  const s = String(status || "").trim().toUpperCase();
  if (s === "SEBAGIAN DITERIMA") return "SEBAGIAN";
  if (s === "BELUM DITERIMA") return "BELUM DITERIMA";
  if (s === "DITERIMA") return "DITERIMA";
  return status || "—";
}

/** Map purchasing status text to existing pill styles. */
export function purchaseStatusPillClass(status) {
  const s = String(status || "").toUpperCase();
  if (!s) return "pill xs pill-muted";
  if (s.includes("BATAL") || s.includes("CANCEL")) {
    return "pill xs pill-cancel";
  }
  if (
    s === "DIBAYAR"
    || s.includes("LUNAS")
    || s === "DITERIMA"
    || s.includes("SELESAI")
    || (s.includes("DITAGIH") && !s.includes("BELUM") && !s.includes("SEBAGIAN"))
  ) {
    return "pill xs pill-ok";
  }
  if (s === "DIAJUKAN" || s.includes("SEBAGIAN") || s === "DP" || s.includes("DP ")) {
    return "pill xs";
  }
  if (
    s.includes("BELUM")
    || s.includes("TERBUKA")
    || s.includes("KOSONG")
    || s === "BELUM DIAJUKAN"
  ) {
    return "pill xs pill-muted";
  }
  return "pill xs";
}

export function receiptSourceBadge(source) {
  const s = String(source || "").toLowerCase();
  if (s === "manual") return { label: "MANUAL", className: "pill xs" };
  if (s === "po" || s === "goods_receipt" || s === "purchase_goods_receipt") {
    return { label: "PO", className: "pill xs pill-ok" };
  }
  return { label: "ACCURATE", className: "pill xs pill-muted" };
}

/** List-row item summary for multi-line purchase documents (UI only). */
export function purchaseLineSummary(doc) {
  const lines = doc?.lines || [];
  if (!lines.length) return "—";
  const first = lines[0].item_name || "Item";
  if (lines.length === 1) return first;
  return `${first} +${lines.length - 1}`;
}

/** Human label for invoice_source stored on purchase invoices. */
export function invoiceSourceLabel(source) {
  const s = String(source || "").toLowerCase();
  if (s === "receipt" || s === "penerimaan" || s === "goods_receipt") return "Penerimaan";
  if (s === "purchase_order" || s === "po" || s === "pesanan") return "Pesanan";
  return source ? String(source) : "—";
}

/** Ignore row activation when the event started on interactive controls. */
export function isPurchaseRowInteractiveTarget(target) {
  if (!target || typeof target.closest !== "function") return false;
  return Boolean(
    target.closest(
      "button, a, input, select, textarea, label, .purchase-row-menu, .purchase-row-menu-popover, .purchase-more-menu",
    ),
  );
}

/** Single-click row opens Detail (ignores interactive targets). */
export function handlePurchaseRowClick(event, openDetail) {
  if (!event || typeof openDetail !== "function") return;
  if (isPurchaseRowInteractiveTarget(event.target)) return;
  openDetail();
}

/** @deprecated Prefer handlePurchaseRowClick — kept for older call sites. */
export function handlePurchaseRowDoubleClick(event, openDetail) {
  handlePurchaseRowClick(event, openDetail);
}

/** Enter / Space opens Detail when row is focused. */
export function handlePurchaseRowKeyDown(event, openDetail) {
  if (!event || typeof openDetail !== "function") return;
  if (isPurchaseRowInteractiveTarget(event.target)) return;
  const key = event.key;
  if (key !== "Enter" && key !== " ") return;
  event.preventDefault();
  openDetail();
}

/** Shared props for clickable purchasing list rows. */
export function purchaseRowInteractionProps(openDetail, { testId, selected = false } = {}) {
  return {
    className: `is-row-clickable${selected ? " is-row-selected" : ""}`,
    role: "button",
    tabIndex: 0,
    "data-testid": testId,
    onClick: (e) => handlePurchaseRowClick(e, openDetail),
    onKeyDown: (e) => handlePurchaseRowKeyDown(e, openDetail),
  };
}

/** Resolve nested/flat actor name for activity history. Legacy docs may lack identity. */
export function actorDisplayName(actor, flatName) {
  if (actor && typeof actor === "object") {
    const n = String(actor.name || actor.email || "").trim();
    if (n) return n;
  }
  const flat = String(flatName || "").trim();
  if (flat) return flat;
  if (typeof actor === "string" && actor.trim()) return actor.trim();
  return "Tidak tersedia";
}

/** True when document has a real business update after create (not just stamp_create mirror). */
export function hasPurchaseDocBeenUpdated(doc) {
  if (!doc) return false;
  const createdAt = String(doc.created_at || "").trim();
  const updatedAt = String(doc.updated_at || "").trim();
  if (updatedAt && createdAt && updatedAt !== createdAt) return true;
  const createdById = String(doc.created_by?.id || doc.created_by_user_id || "").trim();
  const updatedById = String(doc.updated_by?.id || doc.updated_by_user_id || "").trim();
  if (updatedById && createdById && updatedById !== createdById) return true;
  const createdName = actorDisplayName(doc.created_by, doc.created_by_name);
  const updatedName = actorDisplayName(doc.updated_by, doc.updated_by_name);
  if (
    updatedName !== "Tidak tersedia"
    && createdName !== "Tidak tersedia"
    && updatedName !== createdName
    && updatedAt
    && createdAt
    && updatedAt !== createdAt
  ) {
    return true;
  }
  return false;
}

/** Format ISO timestamps for purchasing activity history (local locale). */
export function formatPurchaseDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString("id-ID", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Local calendar YYYY-MM-DD (browser local), for due-status display. */
export function localTodayYmd() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Format YYYY-MM-DD for purchasing UI. */
export function formatPurchaseDate(value) {
  if (!value) return "—";
  const raw = String(value).trim().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return String(value);
  const [y, m, d] = raw.split("-");
  return `${Number(d)} ${["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"][Number(m) - 1] || m} ${y}`;
}

/**
 * Derived invoice due display status.
 * paymentStatus LUNAS wins; missing due_date → BELUM ADA JATUH TEMPO.
 */
export function invoiceDueDisplayStatus(invoice, today = localTodayYmd()) {
  const pay = String(invoice?.payment_status || "").toUpperCase();
  if (pay.includes("LUNAS") || pay === "DIBAYAR") return "LUNAS";
  if (pay.includes("BATAL")) return "DIBATALKAN";
  const due = String(invoice?.due_date || "").trim().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(due)) return "BELUM ADA JATUH TEMPO";
  const day = String(today || localTodayYmd()).slice(0, 10);
  if (day < due) return "BELUM JATUH TEMPO";
  if (day === due) return "JATUH TEMPO HARI INI";
  return "TERLAMBAT";
}

export function dueStatusPillClass(status) {
  const s = String(status || "").toUpperCase();
  if (s === "LUNAS") return "pill xs pill-ok";
  if (s === "TERLAMBAT") return "pill xs pill-cancel";
  if (s === "JATUH TEMPO HARI INI") return "pill xs";
  if (s === "BELUM JATUH TEMPO") return "pill xs pill-muted";
  return "pill xs pill-muted";
}

/** Short label for missing due date. */
export function dueDateLabel(dueDate) {
  if (!dueDate) return "Belum ditentukan";
  return formatPurchaseDate(dueDate);
}

/** Build lifecycle stages from purchasing documents (display only). */
export function buildPurchaseLifecycle({
  order,
  receipt,
  invoice,
  payment,
} = {}) {
  const stages = [];
  if (order) {
    const qty = Number(
      order?.receiving_progress?.ordered_qty
      ?? order?.lines?.reduce((s, l) => s + Number(l.ordered_qty || 0), 0)
      ?? 0,
    );
    stages.push({
      key: "pesanan",
      label: "Pesanan",
      done: Boolean(order?.id || order?.po_number),
      primary: order?.po_number || "—",
      secondary: qty ? `${qty} PCS` : formatPurchaseDate(order?.order_date),
      date: order?.order_date || order?.created_at,
      href: order?.id ? `/pembelian/pesanan-pembelian?id=${encodeURIComponent(order.id)}` : null,
    });
  }
  if (receipt || order) {
    const received = Number(
      order?.receiving_progress?.received_qty
      ?? receipt?.lines?.reduce((s, l) => s + Number(l.received_qty || 0), 0)
      ?? 0,
    );
    const ordered = Number(order?.receiving_progress?.ordered_qty || 0);
    const done = Boolean(receipt?.id) || received > 0;
    stages.push({
      key: "penerimaan",
      label: "Penerimaan",
      done,
      primary: receipt?.receipt_number || (done ? shortReceivingStatus(order?.receiving_status) : "Belum"),
      secondary: ordered ? `${received} / ${ordered}` : (received ? String(received) : "—"),
      date: receipt?.receipt_date || receipt?.created_at,
      href: receipt?.id
        ? `/pembelian/penerimaan-barang?id=${encodeURIComponent(receipt.id)}`
        : (order?.id ? `/pembelian/penerimaan-barang?purchase_order_id=${encodeURIComponent(order.id)}` : null),
    });
  }
  if (invoice || order) {
    const done = Boolean(invoice?.id) || String(order?.invoice_status || "").includes("DIFAKTUR");
    stages.push({
      key: "faktur",
      label: "Faktur",
      done,
      primary: invoice?.invoice_number || (done ? (order?.invoice_status || "Difaktur") : "Belum"),
      secondary: invoice ? formatRupiah(invoice.grand_total) : "—",
      date: invoice?.invoice_date || invoice?.created_at,
      href: invoice?.id
        ? `/pembelian/faktur-pembelian?invoice_id=${encodeURIComponent(invoice.id)}`
        : null,
    });
  }
  if (payment || invoice) {
    const done = Boolean(payment?.id) || Number(invoice?.paid_total || 0) > 0
      || String(invoice?.payment_status || "").toUpperCase() === "LUNAS"
      || String(payment?.workflow_status || "").includes("DIAJUKAN");
    const payLabel = payment?.payment_number
      || (String(invoice?.payment_status || "").toUpperCase() === "LUNAS"
        ? "Lunas"
        : (payment?.workflow_status || invoice?.payment_status || "Belum"));
    stages.push({
      key: "pembayaran",
      label: "Pembayaran",
      done,
      primary: payLabel,
      secondary: payment
        ? formatRupiah(payment.amount)
        : (invoice ? formatRupiah(invoice.paid_total || 0) : "—"),
      date: payment?.paid_at || payment?.submitted_at || payment?.created_at,
      href: payment?.id
        ? `/pembelian/pembayaran-pembelian?payment_id=${encodeURIComponent(payment.id)}`
        : null,
    });
  }
  return stages;
}

/** Frontend-only payment amount check. Backend remains authoritative. */
export function validatePurchasePaymentAmount(amount, outstanding) {
  const raw = String(amount ?? "").trim();
  if (raw === "") return "Jumlah pembayaran wajib diisi.";
  const n = Number(raw);
  if (!Number.isFinite(n)) return "Jumlah pembayaran tidak valid.";
  if (n <= 0) return "Jumlah pembayaran harus lebih besar dari 0.";
  const out = Number(outstanding);
  if (Number.isFinite(out) && n > out + 1e-9) {
    return `Jumlah pembayaran melebihi sisa tagihan (${formatRupiah(out)}).`;
  }
  return "";
}
