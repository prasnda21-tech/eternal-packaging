import { useEffect, useState } from "react";
import {
  FEEDBACK_MAX_LEN,
  NOTE_REQUIRED_STATUSES,
  STATUS_OPTIONS,
  STATUS_PURCHASED,
  customerSourceLabel,
  productSourceLabel,
  statusLabel,
} from "../lib/productRequest";

export const DETAIL_SECTIONS = [
  { key: "customer", label: "Customer" },
  { key: "product", label: "Produk" },
  { key: "potential", label: "Potensi" },
  { key: "workflow", label: "Status & Feedback" },
];

export function displayValue(value) {
  if (value === 0) return "0";
  if (value === "" || value == null) return "—";
  return String(value);
}

function formatWhen(value) {
  if (!value) return "—";
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return String(value);
  }
}

function activitySummary(row) {
  if (row?.type === "feedback") return "Feedback";
  if (row?.type === "submitted") return "Pengajuan dikirim";
  if (row?.type === "status_change") {
    const from = row.from_status_label || statusLabel(row.from_status);
    const to = row.to_status_label || statusLabel(row.to_status);
    return `Status: ${from} → ${to}`;
  }
  return row?.type || "Aktivitas";
}

export default function ProductRequestDetail({
  request,
  testId = "prq-readonly-detail",
  activities = [],
  canFeedback = false,
  canUpdateStatus = false,
  workflowBusy = "",
  onSubmitFeedback,
  onSubmitStatus,
}) {
  const [section, setSection] = useState("customer");
  const [feedbackNote, setFeedbackNote] = useState("");
  const [statusValue, setStatusValue] = useState(request?.status || "submitted");
  const [statusNote, setStatusNote] = useState("");
  const [actualQty, setActualQty] = useState(
    request?.purchase_actual_qty != null ? String(request.purchase_actual_qty) : "",
  );
  const [actualPrice, setActualPrice] = useState(
    request?.purchase_actual_price != null ? String(request.purchase_actual_price) : "",
  );

  useEffect(() => {
    setStatusValue(request?.status || "submitted");
    setActualQty(request?.purchase_actual_qty != null ? String(request.purchase_actual_qty) : "");
    setActualPrice(request?.purchase_actual_price != null ? String(request.purchase_actual_price) : "");
  }, [request?.id, request?.status, request?.doc_version, request?.purchase_actual_qty, request?.purchase_actual_price]);

  const customer = request?.customer_snapshot || {};
  const product = request?.product_snapshot || {};
  const creator = request?.created_by_snapshot || {};
  const branch = request?.branch_snapshot || {};
  const showWorkflow = Boolean(request) && request.status !== "draft";
  const tabs = showWorkflow ? DETAIL_SECTIONS : DETAIL_SECTIONS.filter((item) => item.key !== "workflow");
  const noteRequired = NOTE_REQUIRED_STATUSES.has(statusValue);

  const submitFeedback = async (event) => {
    event.preventDefault();
    if (!onSubmitFeedback || !feedbackNote.trim()) return;
    const ok = await onSubmitFeedback({ note: feedbackNote.trim(), doc_version: request?.doc_version });
    if (ok) setFeedbackNote("");
  };

  const submitStatus = async (event) => {
    event.preventDefault();
    if (!onSubmitStatus) return;
    const payload = {
      status: statusValue,
      note: statusNote.trim(),
      doc_version: request?.doc_version,
    };
    if (statusValue === STATUS_PURCHASED) {
      if (actualQty !== "") payload.actual_qty = Number(actualQty);
      if (actualPrice !== "") payload.actual_price = Number(actualPrice);
    }
    const ok = await onSubmitStatus(payload);
    if (ok) setStatusNote("");
  };

  return (
    <div className="prq-detail" data-testid={testId}>
      <div className="prq-detail-tabs" role="tablist" data-testid="prq-detail-tabs">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={section === item.key}
            className={"prq-detail-tab" + (section === item.key ? " is-active" : "")}
            data-testid={`prq-detail-tab-${item.key}`}
            onClick={() => setSection(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <section hidden={section !== "customer"} data-testid="prq-detail-panel-customer">
        <dl className="prq-dl">
          <dt>Nomor</dt><dd>{displayValue(request?.request_number)}</dd>
          <dt>Status</dt>
          <dd data-testid="prq-detail-status">
            {request?.status_label || statusLabel(request?.status)}
          </dd>
          <dt>Customer</dt><dd>{customerSourceLabel(request?.customer_source)}</dd>
          <dt>Nama</dt><dd data-testid="prq-detail-customer-name">{displayValue(customer.name)}</dd>
          <dt>PIC</dt><dd>{displayValue(customer.pic)}</dd>
          <dt>WhatsApp</dt><dd>{displayValue(customer.whatsapp || customer.phone)}</dd>
          <dt>Kota</dt><dd>{displayValue(customer.city)}</dd>
          <dt>Alamat</dt><dd>{displayValue(customer.address)}</dd>
        </dl>
      </section>

      <section hidden={section !== "product"} data-testid="prq-detail-panel-product">
        <dl className="prq-dl">
          <dt>Produk</dt><dd>{productSourceLabel(request?.product_source)}</dd>
          <dt>Nama</dt><dd data-testid="prq-detail-product-name">{displayValue(product.item_name || product.name)}</dd>
          <dt>Kode</dt><dd data-testid="prq-detail-product-code">{displayValue(product.item_code)}</dd>
          <dt>Satuan</dt><dd data-testid="prq-detail-product-unit">{displayValue(product.unit)}</dd>
          <dt>Kategori</dt><dd data-testid="prq-detail-product-category">{displayValue(product.category)}</dd>
          <dt>Ukuran</dt><dd data-testid="prq-detail-product-size">{displayValue(product.size)}</dd>
          <dt>Material</dt><dd data-testid="prq-detail-product-material">{displayValue(product.material)}</dd>
          <dt>Model / Spesifikasi</dt><dd data-testid="prq-detail-product-model">{displayValue(product.model || product.specification)}</dd>
        </dl>
      </section>

      <section hidden={section !== "potential"} data-testid="prq-detail-panel-potential">
        <dl className="prq-dl">
          <dt>Qty</dt><dd>{displayValue(request?.potential_qty)}</dd>
          <dt>Frekuensi</dt><dd>{displayValue(request?.frequency)}</dd>
          <dt>Harga</dt><dd>{displayValue(request?.target_price)}</dd>
          <dt>Tanggal</dt><dd>{displayValue(request?.needed_date)}</dd>
          <dt>Sumber Channel</dt><dd data-testid="prq-detail-channel">{displayValue(request?.request_channel)}</dd>
          <dt>Catatan</dt><dd data-testid="prq-detail-note">{displayValue(request?.request_note)}</dd>
          <dt>Qty Aktual Beli</dt><dd data-testid="prq-detail-actual-qty">{displayValue(request?.purchase_actual_qty)}</dd>
          <dt>Harga Aktual Beli</dt><dd data-testid="prq-detail-actual-price">{displayValue(request?.purchase_actual_price)}</dd>
          <dt>Pembuat</dt><dd>{displayValue(creator.name)}</dd>
          <dt>Cabang</dt><dd>{displayValue(branch.name)}</dd>
        </dl>
      </section>

      {showWorkflow && (
        <section hidden={section !== "workflow"} data-testid="prq-detail-panel-workflow">
          {canFeedback && (
            <form className="prq-workflow-form" data-testid="prq-feedback-form" onSubmit={submitFeedback}>
              <label className="prq-field">
                <span>Feedback</span>
                <textarea
                  data-testid="prq-feedback-input"
                  value={feedbackNote}
                  maxLength={FEEDBACK_MAX_LEN}
                  rows={3}
                  onChange={(event) => setFeedbackNote(event.target.value)}
                  placeholder="Tulis feedback tanpa mengubah status"
                />
              </label>
              <button
                type="submit"
                className="prq-btn prq-btn-primary"
                data-testid="prq-feedback-submit"
                disabled={Boolean(workflowBusy) || !feedbackNote.trim()}
              >
                {workflowBusy === "feedback" ? "Menyimpan…" : "Kirim Feedback"}
              </button>
            </form>
          )}

          {canUpdateStatus && (
            <form className="prq-workflow-form" data-testid="prq-status-form" onSubmit={submitStatus}>
              <label className="prq-field">
                <span>Status</span>
                <select
                  data-testid="prq-status-select"
                  value={statusValue}
                  onChange={(event) => setStatusValue(event.target.value)}
                >
                  {STATUS_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </label>
              <label className="prq-field">
                <span>Catatan{noteRequired ? " (wajib)" : ""}</span>
                <textarea
                  data-testid="prq-status-note"
                  value={statusNote}
                  maxLength={FEEDBACK_MAX_LEN}
                  rows={2}
                  onChange={(event) => setStatusNote(event.target.value)}
                />
              </label>
              {statusValue === STATUS_PURCHASED && (
                <div className="prq-actual-grid">
                  <label className="prq-field">
                    <span>Jumlah aktual (opsional)</span>
                    <input
                      data-testid="prq-actual-qty"
                      type="number"
                      min="0"
                      step="1"
                      value={actualQty}
                      onChange={(event) => setActualQty(event.target.value)}
                    />
                  </label>
                  <label className="prq-field">
                    <span>Harga pembelian (opsional)</span>
                    <input
                      data-testid="prq-actual-price"
                      type="number"
                      min="0"
                      step="1"
                      value={actualPrice}
                      onChange={(event) => setActualPrice(event.target.value)}
                    />
                  </label>
                </div>
              )}
              <button
                type="submit"
                className="prq-btn prq-btn-primary"
                data-testid="prq-status-submit"
                disabled={Boolean(workflowBusy) || (noteRequired && !statusNote.trim())}
              >
                {workflowBusy === "status" ? "Menyimpan…" : "Simpan Status"}
              </button>
            </form>
          )}

          <div className="prq-activity" data-testid="prq-activity-list">
            <h3>Riwayat</h3>
            {activities.length === 0 && <p className="muted">Belum ada aktivitas.</p>}
            <ol>
              {activities.map((row) => (
                <li key={row.id} data-testid={`prq-activity-${row.id}`}>
                  <strong>{activitySummary(row)}</strong>
                  <span className="prq-activity-meta">
                    {displayValue(row.actor_snapshot?.name)} · {formatWhen(row.created_at)}
                  </span>
                  {row.note ? <p>{row.note}</p> : null}
                </li>
              ))}
            </ol>
          </div>
        </section>
      )}
    </div>
  );
}
