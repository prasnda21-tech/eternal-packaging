export const WORKFLOW_LABELS = [
  "Permintaan",
  "Pesanan",
  "Barang diterima",
  "Tagihan Supplier",
  "Pembayaran",
];

export function dash(value) {
  if (value === 0) return "0";
  if (value == null) return "—";
  const text = String(value).trim();
  return text ? text : "—";
}

export function formatLongDate(value) {
  if (!value) return "—";
  const raw = String(value).slice(0, 10);
  const date = new Date(`${raw}T00:00:00`);
  if (Number.isNaN(date.getTime())) return dash(value);
  return date.toLocaleDateString("id-ID", { day: "numeric", month: "long", year: "numeric" });
}

export function actorName(doc, preferred) {
  return dash(
    preferred
    || doc?.requested_by
    || doc?.created_by?.name
    || doc?.created_by_name
    || doc?.submitted_by_name
  );
}

export function workflowSteps(states) {
  return WORKFLOW_LABELS.map((label, index) => ({
    label,
    state: states[index] || "next",
  }));
}

function up(value) {
  return String(value || "").trim().toUpperCase();
}

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function requestStatusSentence(status) {
  const raw = String(status || "").toUpperCase();
  if (raw === "DIBATALKAN") return "Dibatalkan";
  if (raw === "SELESAI DIPESAN") return "Sudah dibuat Pesanan Pembelian";
  if (raw === "SEBAGIAN DIPESAN") return "Sebagian sudah dibuat Pesanan Pembelian";
  if (raw === "TERBUKA" || raw === "KOSONG" || !raw) return "Menunggu Pesanan";
  return status;
}

/**
 * Timeline follows fields on the document returned by the existing API.
 * The open page does not advance a stage by itself.
 * Pembayaran becomes the active step only after a payment has started.
 */
export function transactionSteps(doc) {
  const source = doc || {};
  const status = up(source.status);
  const receiving = up(source.receiving_status);
  const invoice = up(source.invoice_status);
  const pay = up(source.payment_status || source.invoice_payment_status);
  const workflow = up(source.workflow_status);
  const settlement = up(source.settlement_status);
  const prStatuses = ["TERBUKA", "KOSONG", "SEBAGIAN DIPESAN", "SELESAI DIPESAN", "DIBATALKAN"];
  const isRequest = prStatuses.includes(status) && !source.po_number && !source.invoice_number && !source.payment_number;
  const hasRequest = Boolean(source.request_id || source.request_number) || isRequest;
  const hasOrder = Boolean(source.po_number || source.purchase_order_id || source.order_date)
    || status === "SEBAGIAN DIPESAN"
    || status === "SELESAI DIPESAN"
    || Boolean(receiving)
    || Boolean(invoice);

  const progress = source.receiving_progress || {};
  let ordered = num(progress.ordered_qty);
  let received = num(progress.received_qty);
  if (!receiving && !ordered && Array.isArray(source.lines) && source.lines.some((line) => line.remaining_receivable_qty != null || line.cumulative_received_qty != null)) {
    ordered = source.lines.reduce((sum, line) => sum + num(line.ordered_qty), 0);
    received = source.lines.reduce((sum, line) => sum + num(line.cumulative_received_qty), 0);
  }

  const earlyInvoice = source.precedes_goods_receipt === true;
  const barangDone = receiving === "DITERIMA" || (ordered > 0 && received >= ordered);
  const barangStarted = barangDone || receiving === "SEBAGIAN DITERIMA" || received > 0;
  const isInvoice = Boolean(source.invoice_number);
  const isPayment = Boolean(source.payment_number) || Boolean(source.submission_status);
  const invoiceStarted = isInvoice
    || invoice === "DIFAKTUR"
    || invoice === "SEBAGIAN DIFAKTUR"
    || pay === "LUNAS"
    || pay === "DP"
    || pay === "BELUM DIBAYAR";
  const paid = pay === "LUNAS" || settlement === "PAID" || workflow === "DIBAYAR" || status === "DIBAYAR";
  const payStarted = paid || pay === "DP" || workflow === "DIAJUKAN" || isPayment;
  const goodsReached = barangStarted || barangDone || Boolean(source.receipt_number) || ((isInvoice || isPayment) && !earlyInvoice);

  const reached = [
    hasRequest,
    hasOrder || isInvoice || isPayment,
    goodsReached,
    invoiceStarted,
    payStarted,
  ];
  const complete = [
    reached[1] || reached[2] || reached[3] || reached[4],
    reached[2] || reached[3] || reached[4],
    (barangDone || Boolean(source.receipt_number) || ((isInvoice || isPayment) && !earlyInvoice)) && (invoiceStarted || payStarted),
    payStarted,
    paid,
  ];

  if (paid) {
    return workflowSteps(reached.map((stage) => (stage ? "done" : "next")));
  }

  let current = reached.findIndex(Boolean);
  if (current < 0) current = 0;
  reached.forEach((stage, index) => {
    if (stage && !complete[index]) current = index;
  });

  return workflowSteps(reached.map((stage, index) => {
    if (index === current) return "now";
    if (index < current && stage) return "done";
    return "next";
  }));
}

export function requestSteps(status) {
  return transactionSteps({ status });
}

export function orderStatusSentence(detail) {
  const receiving = String(detail?.receiving_status || "").toUpperCase();
  const invoice = String(detail?.invoice_status || "").toUpperCase();
  if (receiving === "DIBATALKAN") return "Dibatalkan";
  if (receiving === "SEBAGIAN DITERIMA") return "Sebagian diterima";
  if (receiving !== "DITERIMA") return "Menunggu Barang Datang";
  if (invoice === "DIFAKTUR") return "Tagihan tercatat";
  if (invoice === "SEBAGIAN DIFAKTUR") return "Tagihan sebagian";
  return "Barang diterima";
}

export function orderSteps(detail) {
  return transactionSteps(detail);
}

export function invoiceStatusSentence(status) {
  const raw = String(status || "").toUpperCase();
  if (raw === "DIBATALKAN") return "Dibatalkan";
  if (raw === "LUNAS") return "Lunas";
  if (raw === "DP") return "Sebagian dibayar";
  if (raw === "BELUM DIBAYAR" || !raw) return "Menunggu Pembayaran";
  return status;
}

export function invoiceSteps(detailOrStatus) {
  if (detailOrStatus && typeof detailOrStatus === "object") return transactionSteps(detailOrStatus);
  return transactionSteps({ invoice_number: "invoice", payment_status: detailOrStatus, receipt_number: "receipt" });
}

export function paymentStatusSentence(detail) {
  const settled = detail?.settlement_status === "PAID" || detail?.workflow_status === "DIBAYAR" || detail?.status === "DIBAYAR";
  if (detail?.cancelled) return "Dibatalkan";
  if (settled) return "Sudah dibayar";
  return "Menunggu Konfirmasi Bayar";
}

export function paymentSteps(detail) {
  return transactionSteps(detail);
}

export function receivingSteps(detail) {
  return transactionSteps(detail || {});
}

export function receiveEntryStatus(preview) {
  if (preview?.receiving_status) return orderStatusSentence(preview);
  const lines = preview?.lines || [];
  if (!lines.length) return "—";
  const remaining = lines.reduce((sum, line) => sum + (Number(line.remaining_receivable_qty) || 0), 0);
  return remaining > 0 ? "Menunggu Barang Datang" : "Barang diterima";
}

export function receiveEntrySteps(preview) {
  return transactionSteps(preview);
}
