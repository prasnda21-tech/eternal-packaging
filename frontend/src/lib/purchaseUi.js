import { addWibCalendarDays, getWibTodayString } from "./wibCalendarDate";

export function formatRupiah(num) {
  const n = Number(num) || 0;
  return "Rp " + n.toLocaleString("id-ID");
}

/** Prefer Asia/Jakarta calendar day for purchasing date defaults (Owner timezone). */
export function todayYmd() {
  return getWibTodayString();
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

/** Purchase invoice payment-term presets (Accurate-style Net N / COD). */
export const PURCHASE_TERM_PRESETS = [
  { id: "COD", label: "COD", days: 0 },
  { id: "NET7", label: "Net 7", days: 7 },
  { id: "NET14", label: "Net 14", days: 14 },
  { id: "NET30", label: "Net 30", days: 30 },
  { id: "CUSTOM", label: "Custom", days: null },
];

/** Add whole calendar days to YYYY-MM-DD without UTC midnight shift (WIB-safe). */
export function addDaysYmd(startYmd, days) {
  return addWibCalendarDays(startYmd, days);
}

export function termPresetFromDays(days) {
  const n = Number(days);
  if (!Number.isFinite(n)) return "CUSTOM";
  const hit = PURCHASE_TERM_PRESETS.find((p) => p.days !== null && p.days === n);
  return hit ? hit.id : "CUSTOM";
}

export function paymentTermLabel(days) {
  if (days === null || days === undefined || days === "") return "—";
  const n = Number(days);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "COD";
  const hit = PURCHASE_TERM_PRESETS.find((p) => p.days === n);
  return hit ? hit.label : `Net ${n}`;
}

export function dueDateLabel(dueDate) {
  if (!dueDate) return "Belum ditentukan";
  return String(dueDate).slice(0, 10);
}

const PURCHASE_MONTHS_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

/** Display-only Indonesian calendar date: 2026-10-22 → "22 Okt 2026". */
export function formatPurchaseDateId(value) {
  const ymd = String(value || "").trim().slice(0, 10);
  const m = ymd.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return value ? String(value) : "—";
  const day = Number(m[3]);
  const month = Number(m[2]);
  if (!Number.isFinite(day) || month < 1 || month > 12) return ymd;
  return `${day} ${PURCHASE_MONTHS_ID[month - 1]} ${m[1]}`;
}

/**
 * Umur Faktur in whole calendar days from invoice_date to today (Asia/Jakarta).
 * Minimum 0. Does not use due_date.
 */
export function invoiceAgeDays(invoiceDate, today = todayYmd()) {
  const inv = String(invoiceDate || "").trim().slice(0, 10);
  const tod = String(today || "").trim().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(inv) || !/^\d{4}-\d{2}-\d{2}$/.test(tod)) return null;
  const [y1, m1, d1] = inv.split("-").map(Number);
  const [y2, m2, d2] = tod.split("-").map(Number);
  const a = Date.UTC(y1, m1 - 1, d1);
  const b = Date.UTC(y2, m2 - 1, d2);
  return Math.max(0, Math.floor((b - a) / 86400000));
}

export function invoiceAgeLabel(invoiceDate, today = todayYmd()) {
  const days = invoiceAgeDays(invoiceDate, today);
  if (days === null) return "—";
  return `${days} hari`;
}

export function canEditPurchaseInvoicePaymentTerms(invoice, hasEditPerm) {
  if (!hasEditPerm || !invoice) return false;
  if (invoice.cancelled === true) return false;
  if (invoice.can_edit_payment_terms === false) return false;
  const status = String(invoice.payment_status || "").toUpperCase();
  if (status === "LUNAS") return false;
  return true;
}

/** Master YGY picker — match by code (then Yogyakarta name). Never invents ids. */
export function pickYgyBranchFromMaster(branches) {
  const rows = Array.isArray(branches) ? branches : [];
  const byCode = rows.find(
    (b) => String(b?.code || "").trim().toUpperCase() === "YGY" && b?.active !== false,
  );
  if (byCode) return byCode;
  return rows.find(
    (b) => /yogyakarta/i.test(String(b?.name || "")) && b?.active !== false,
  ) || null;
}

/**
 * Display Cabang Pembelian from snapshot/master fields.
 * Opaque branch_id alone → "—" (do not surface raw master ids).
 */
export function purchaseBranchLabel(doc) {
  if (!doc || typeof doc !== "object") return "—";
  const code = String(doc.branch_code || doc.code || "").trim();
  const name = String(doc.branch_name || doc.name || "").trim();
  if (!code && !name) return "—";
  if (name && code) return `${name} (${code})`;
  return name || code;
}

/** Legacy PO: Lengkapi Cabang only when branch missing, not cancelled, and permitted. */
export function canShowLegacyPoBranchComplete(detail, canEditBranch) {
  if (!detail || !canEditBranch) return false;
  if (detail.cancelled === true) return false;
  return !String(detail.branch_id || "").trim();
}

/** Active master branches with id for legacy PO completion select. */
export function filterActiveBranchesForPoComplete(items) {
  return (Array.isArray(items) ? items : []).filter(
    (row) => row && row.active !== false && String(row.id || "").trim(),
  );
}

/** Create-form Cabang messages — distinct from legacy detail "—". */
export const PO_CREATE_BRANCH = {
  loading: "Memuat cabang...",
  missing: "Cabang YGY tidak tersedia atau tidak dapat diakses",
  forbidden: "Anda tidak memiliki akses melihat cabang",
  error: "Gagal memuat data cabang",
};

/**
 * Map branches API outcome → PO create-form label.
 * Never invents YGY; empty success list ≠ production-empty claim.
 */
export function resolveCreatePoBranchLabel(outcome) {
  if (!outcome || outcome.kind === "loading") return PO_CREATE_BRANCH.loading;
  if (outcome.kind === "ok") {
    const ygy = pickYgyBranchFromMaster(outcome.items || []);
    if (ygy) return purchaseBranchLabel(ygy);
    return PO_CREATE_BRANCH.missing;
  }
  if (outcome.kind === "http" && Number(outcome.status) === 403) {
    return PO_CREATE_BRANCH.forbidden;
  }
  return PO_CREATE_BRANCH.error;
}

/** Ignore stale/unmounted branch fetches; return null when result must not apply. */
export function applyCreatePoBranchFetchResult(gen, currentGen, cancelled, outcome) {
  if (cancelled || gen !== currentGen) return null;
  return resolveCreatePoBranchLabel(outcome);
}

/**
 * Display Gudang from snapshot fields. Opaque warehouse_id alone → "—".
 */
export function purchaseWarehouseLabel(doc) {
  if (!doc || typeof doc !== "object") return "—";
  const code = String(doc.warehouse_code || "").trim();
  const name = String(doc.warehouse_name || "").trim();
  if (!code && !name) return "—";
  if (name && code) return `${name} (${code})`;
  return name || code;
}

/**
 * Invoice create/detail warehouse label.
 * From PO (early / before GR): always "—" when no warehouse snapshot.
 */
export function purchaseInvoiceWarehouseLabel(doc, invoiceSource) {
  const src = String(invoiceSource || doc?.invoice_source || "").toLowerCase();
  const fromPo = src === "purchase_order" || src === "po" || src === "pesanan";
  if (fromPo) {
    const hasWh = String(
      doc?.warehouse_id || doc?.warehouse_code || doc?.warehouse_name || "",
    ).trim();
    if (!hasWh) return "—";
  }
  return purchaseWarehouseLabel(doc);
}

/** LOC-C2: legacy PO without branch_id — never invent YGY on the client. */
export const LEGACY_PO_BRANCH_REQUIRED_MSG =
  "Lengkapi Cabang PO terlebih dahulu sebelum membuat penerimaan barang.";

/**
 * Warehouse choices for PO-linked GR: filter by PO.branch_id only.
 * branches/warehouses must already be access-scoped by API (permission).
 * Does not invent YGY; empty/missing PO branch → blocked.
 */
export function goodsReceiptWarehouseChoiceForPo(branches, warehouses, poBranchId) {
  const branchId = String(poBranchId || "").trim();
  if (!branchId) {
    return {
      rows: [],
      warehouseId: "",
      blocked: true,
      explicit: false,
      reason: "legacy_branch_missing",
    };
  }
  const allowedBranchIds = new Set(
    (Array.isArray(branches) ? branches : [])
      .filter((row) => row?.id && row.active !== false)
      .map((row) => row.id),
  );
  if (!allowedBranchIds.has(branchId)) {
    return {
      rows: [],
      warehouseId: "",
      blocked: true,
      explicit: false,
      reason: "branch_forbidden",
    };
  }
  const rows = (Array.isArray(warehouses) ? warehouses : []).filter((row) => (
    Boolean(row?.id)
    && row.active !== false
    && row?.is_operational !== false
    && String(row.branch_id || "") === branchId
  ));
  if (rows.length === 1) {
    return {
      rows,
      warehouseId: rows[0].id,
      blocked: false,
      explicit: false,
      reason: "",
    };
  }
  if (!rows.length) {
    return {
      rows,
      warehouseId: "",
      blocked: true,
      explicit: false,
      reason: "no_warehouse",
    };
  }
  return {
    rows,
    warehouseId: "",
    blocked: false,
    explicit: true,
    reason: "",
  };
}

export function goodsReceiptWarehouseBlockMessage(choice) {
  const reason = String(choice?.reason || "");
  if (reason === "legacy_branch_missing") return LEGACY_PO_BRANCH_REQUIRED_MSG;
  if (reason === "branch_forbidden") {
    return "Anda tidak memiliki akses gudang pada cabang PO ini.";
  }
  if (reason === "no_warehouse") {
    return "Penerimaan tidak dapat disimpan: belum ada gudang aktif pada cabang PO.";
  }
  if (choice?.explicit) return "Pilih gudang sebelum menyimpan penerimaan.";
  if (choice?.blocked) {
    return "Penerimaan tidak dapat disimpan: belum ada gudang aktif.";
  }
  return "";
}

/** Stable signature for one receive intent (qty/date/warehouse/po). */
export function purchaseGoodsReceiptPayloadSignature(payload) {
  if (!payload || typeof payload !== "object") return "";
  const lines = (Array.isArray(payload.lines) ? payload.lines : [])
    .map((line) => ({
      purchase_order_line_id: String(line?.purchase_order_line_id || ""),
      received_qty: Number(line?.received_qty),
    }))
    .filter((line) => line.purchase_order_line_id && Number.isFinite(line.received_qty) && line.received_qty > 0)
    .sort((a, b) => a.purchase_order_line_id.localeCompare(b.purchase_order_line_id));
  return JSON.stringify({
    purchase_order_id: String(payload.purchase_order_id || ""),
    receipt_date: String(payload.receipt_date || ""),
    warehouse_id: String(payload.warehouse_id || ""),
    lines,
  });
}

/** Deep-clone + normalize GR create body fields used for identity. */
export function normalizePurchaseGoodsReceiptPayload(payload) {
  const lines = (Array.isArray(payload?.lines) ? payload.lines : [])
    .map((line) => ({
      purchase_order_line_id: String(line?.purchase_order_line_id || ""),
      received_qty: Number(line?.received_qty),
    }))
    .filter((line) => line.purchase_order_line_id && Number.isFinite(line.received_qty) && line.received_qty > 0);
  return {
    purchase_order_id: String(payload?.purchase_order_id || ""),
    receipt_date: String(payload?.receipt_date || ""),
    warehouse_id: String(payload?.warehouse_id || ""),
    lines,
  };
}

/**
 * Immutable snapshot taken before the first create request.
 * Retry must POST this body + key — never rebuild from live form.
 */
export function freezePurchaseGoodsReceiptRequest({ idempotency_key, payload }) {
  const frozenPayload = normalizePurchaseGoodsReceiptPayload(payload);
  const key = String(idempotency_key || "").trim();
  return {
    idempotency_key: key,
    payload: frozenPayload,
    signature: purchaseGoodsReceiptPayloadSignature(frozenPayload),
    frozen_at: Date.now(),
  };
}

/** Exact POST body for same-key retry from a frozen snapshot. */
export function buildRetryPurchaseGoodsReceiptBody(frozen) {
  if (!frozen?.idempotency_key || !frozen?.payload) {
    throw new Error("Frozen GR recovery identity missing.");
  }
  return {
    ...normalizePurchaseGoodsReceiptPayload(frozen.payload),
    idempotency_key: String(frozen.idempotency_key),
  };
}

export const GR_RECOVERY_HOLD_KINDS = ["recoverable", "uncertain", "ambiguous"];

export function shouldHoldGrRecoveryIdentity(parsed) {
  return GR_RECOVERY_HOLD_KINDS.includes(String(parsed?.kind || ""));
}

const GR_RECOVERY_STORAGE_KEY = "eternal.pur.gr_receive_recovery.v1";
let grRecoveryMemory = null;

/** In-memory + sessionStorage only (not localStorage; no secrets). */
export function saveGrRecoveryIdentity(state) {
  grRecoveryMemory = state || null;
  try {
    if (typeof sessionStorage !== "undefined") {
      if (state) sessionStorage.setItem(GR_RECOVERY_STORAGE_KEY, JSON.stringify(state));
      else sessionStorage.removeItem(GR_RECOVERY_STORAGE_KEY);
    }
  } catch {
    /* sessionStorage may be unavailable — memory still holds identity for SPA session */
  }
  return grRecoveryMemory;
}

export function loadGrRecoveryIdentity() {
  if (grRecoveryMemory?.frozen?.idempotency_key) return grRecoveryMemory;
  try {
    if (typeof sessionStorage !== "undefined") {
      const raw = sessionStorage.getItem(GR_RECOVERY_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed?.frozen?.idempotency_key) {
          grRecoveryMemory = parsed;
          return parsed;
        }
      }
    }
  } catch {
    /* ignore corrupt session blob */
  }
  return null;
}

export function clearGrRecoveryIdentity() {
  return saveGrRecoveryIdentity(null);
}

/** Test/helper: reset module memory without touching sessionStorage quirks. */
export function __resetGrRecoveryIdentityForTests() {
  grRecoveryMemory = null;
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.removeItem(GR_RECOVERY_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * Live cancel gate from GET /purchase-goods-receipts/:id body.receipt.
 * Fail closed when can_cancel is missing or false.
 */
export function evaluateLiveReceiptCancelEligibility(receipt) {
  if (!receipt || typeof receipt !== "object") {
    return { allowed: false, reason: "Data penerimaan tidak tersedia dari server.", receipt: null };
  }
  const status = String(receipt.status || "").toUpperCase();
  if (status === "CANCELLED" || receipt.cancelled === true) {
    return { allowed: false, reason: "Penerimaan sudah dibatalkan.", receipt };
  }
  if (String(receipt.accounting_status || "").toLowerCase() === "posted") {
    return { allowed: false, reason: "Penerimaan sudah diposting accounting.", receipt };
  }
  if (receipt.can_cancel === true) {
    return { allowed: true, reason: "", receipt };
  }
  if (receipt.can_cancel === false) {
    return {
      allowed: false,
      reason: receipt.cancel_block_reason || "Penerimaan tidak dapat dibatalkan.",
      receipt,
    };
  }
  return {
    allowed: false,
    reason: "Status pembatalan tidak dapat dipastikan dari server.",
    receipt,
  };
}

/**
 * Parse create GR API errors for recovery UI.
 * Never invites a new idempotency key for recovery / uncertain failures.
 * Hold kinds keep same-key retry available when a frozen identity exists.
 */
export function parsePurchaseGoodsReceiptCreateError(ex) {
  const status = Number(ex?.response?.status) || 0;
  const detail = ex?.response?.data?.detail;
  const obj = (detail && typeof detail === "object" && !Array.isArray(detail)) ? detail : null;
  const code = String(obj?.code || "").trim();
  const receiptId = String(obj?.receipt_id || "").trim();
  const recoverable = obj?.recoverable === true;
  const baseMessage = obj?.message
    || (typeof detail === "string" ? detail : "")
    || ex?.message
    || "Terjadi kesalahan";

  if (code === "IDEMPOTENCY_CANCELLED") {
    return {
      kind: "idempotency_cancelled",
      code,
      receipt_id: receiptId,
      recoverable: false,
      message: baseMessage,
      status,
      inviteNewKey: false,
      showRetrySameKey: false,
      mayProbeCancel: Boolean(receiptId),
    };
  }
  if (recoverable && receiptId) {
    return {
      kind: "recoverable",
      code: code || "FAILED",
      receipt_id: receiptId,
      recoverable: true,
      message: baseMessage,
      status,
      inviteNewKey: false,
      showRetrySameKey: true,
      mayProbeCancel: Boolean(receiptId),
    };
  }
  if (!ex?.response) {
    return {
      kind: "uncertain",
      code: "",
      receipt_id: receiptId,
      recoverable: false,
      message: "Status penerimaan tidak dapat dipastikan. Stok mungkin sudah terposting. Jangan buat penerimaan baru dengan kunci baru — lanjutkan transaksi yang sama.",
      status: 0,
      inviteNewKey: false,
      showRetrySameKey: true,
      mayProbeCancel: Boolean(receiptId),
    };
  }
  if (obj && receiptId && obj.recoverable == null) {
    return {
      kind: "ambiguous",
      code: code || "FAILED",
      receipt_id: receiptId,
      recoverable: false,
      message: "Penerimaan mungkin sudah tercatat, tetapi status stok/HPP belum jelas. Jangan buat penerimaan baru. Lanjutkan kunci yang sama atau periksa riwayat.",
      status,
      inviteNewKey: false,
      showRetrySameKey: true,
      mayProbeCancel: Boolean(receiptId),
    };
  }
  return {
    kind: "generic",
    code,
    receipt_id: receiptId,
    recoverable: false,
    message: baseMessage,
    status,
    inviteNewKey: false,
    showRetrySameKey: false,
    mayProbeCancel: false,
  };
}

export function purchaseGoodsReceiptRecoveryPanelMessage(parsed) {
  if (!parsed) return "";
  if (parsed.kind === "recoverable") {
    const mid = parsed.receipt_id ? ` (ID: ${parsed.receipt_id})` : "";
    return `Penerimaan mungkin sudah tercatat${mid}. Stok bisa sudah terposting; perhitungan HPP/cutover gagal. Ulangi transaksi yang sama (kunci idempotensi sama) — jangan buat penerimaan baru. ${parsed.message || ""}`.trim();
  }
  if (parsed.kind === "idempotency_cancelled") {
    const mid = parsed.receipt_id ? ` (ID: ${parsed.receipt_id})` : "";
    return `Kunci idempotensi merujuk penerimaan yang sudah dibatalkan${mid}. Jangan mengulang kunci ini untuk penerimaan baru. ${parsed.message || ""}`.trim();
  }
  return parsed.message || "";
}
