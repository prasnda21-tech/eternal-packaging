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

/** Replace or prepend one PO in the receive list from a fresh API order snapshot. */
export function mergePurchaseOrderIntoList(orders, order) {
  if (!order || !order.id) return Array.isArray(orders) ? orders : [];
  const rest = (Array.isArray(orders) ? orders : []).filter(
    (row) => String(row?.id || "") !== String(order.id),
  );
  return [order, ...rest];
}

/** Offer another goods receipt only while remaining receivable qty is positive. */
export function canOfferPurchaseReceive(order) {
  return Number(order?.receiving_progress?.remaining_qty) > 0;
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
export function purchasePaymentStatusLabel(status) {
  const raw = String(status || "").trim().toUpperCase();
  if (raw === "DP" || raw === "DIBAYAR_SEBAGIAN" || raw.includes("SEBAGIAN")) return "Dibayar Sebagian";
  if (raw === "BELUM_DIBAYAR" || raw === "BELUM DIBAYAR") return "Belum Dibayar";
  if (raw === "LUNAS") return "Lunas";
  if (raw === "DIBATALKAN") return "Dibatalkan";
  return status || "—";
}

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
