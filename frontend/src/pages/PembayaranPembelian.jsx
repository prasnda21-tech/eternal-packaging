import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  errDetail,
  formatRupiah,
  friendlyLoadError,
  purchaseStatusPillClass,
  purchaseRowInteractionProps,
  formatPurchaseDateTime,
} from "../lib/purchaseUi";
import PurchasingFlowNav from "./PurchasingFlowNav";
import PurchaseCancelModal from "./PurchaseCancelModal";
import PurchaseActivityHistory from "./PurchaseActivityHistory";
import PurchaseDocuments from "./PurchaseDocuments";
import "./purchasePages.css";

const FILTERS = [
  { id: "DIAJUKAN", label: "DIAJUKAN" },
  { id: "SELESAI", label: "SELESAI" },
];

export default function PembayaranPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_payments.create");
  const queryPaymentId = searchParams.get("payment_id") || "";

  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("DIAJUKAN");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelErr, setCancelErr] = useState("");
  const [confirmBusy, setConfirmBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.listPurchasePayments({
        q: q || undefined,
        status: statusFilter,
      });
      setRows(res.payments || []);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Tidak dapat memuat pengajuan pembayaran. Periksa koneksi server lalu coba lagi."));
    } finally {
      setLoading(false);
    }
  }, [q, statusFilter]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!queryPaymentId) return;
    (async () => {
      try {
        const res = await api.getPurchasePayment(queryPaymentId);
        setDetail(res.payment);
        setDetailOpen(true);
      } catch (ex) {
        setErr(errDetail(ex));
      }
    })();
  }, [queryPaymentId]);

  const openDetail = async (id) => {
    setErr("");
    try {
      const res = await api.getPurchasePayment(id);
      setDetail(res.payment);
      setDetailOpen(true);
    } catch (ex) {
      setErr(errDetail(ex));
    }
  };

  const closeDetail = () => {
    if (confirmBusy) return;
    setDetailOpen(false);
    setDetail(null);
  };

  const openCancel = (row) => {
    setCancelErr("");
    setCancelTarget(row);
  };

  const confirmCancel = async () => {
    if (!cancelTarget || cancelBusy) return;
    setCancelBusy(true);
    setCancelErr("");
    try {
      await api.cancelPurchasePayment(cancelTarget.id);
      setOk(`Pengajuan ${cancelTarget.payment_number || ""} dihapus dari daftar aktif.`);
      setCancelTarget(null);
      if (detail?.id === cancelTarget.id) {
        setDetailOpen(false);
        setDetail(null);
      }
      await load();
    } catch (ex) {
      setCancelErr(errDetail(ex));
    } finally {
      setCancelBusy(false);
    }
  };

  const confirmPaid = async () => {
    if (!detail || confirmBusy) return;
    setConfirmBusy(true);
    setErr("");
    try {
      const res = await api.confirmPurchasePaymentPaid(detail.id);
      setDetail(res.payment);
      setOk(res.message || "Pembayaran dikonfirmasi dibayar.");
      await load();
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setConfirmBusy(false);
    }
  };

  const submitted = detail?.submission_status === "SUBMITTED";
  const settled = detail?.settlement_status === "PAID";
  const listStatus = (row) =>
    row.list_status
    || (row.settlement_status === "PAID" ? "SELESAI" : (row.submission_status_label || row.workflow_status));

  return (
    <div className="prod-page purchase-page" data-testid="pembayaran-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1>Pembayaran Pembelian</h1>
          <p className="subhead">
            Daftar pengajuan pembayaran dari Faktur. Konfirmasi dibayar dilakukan dari detail pengajuan.
          </p>
        </div>
      </div>
      <PurchasingFlowNav />

      {err && !detailOpen && (
        <div className="purchase-error-panel" role="alert">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" />
            <span>{err}</span>
          </div>
          <button type="button" className="ghost sm" onClick={load}>Coba Lagi</button>
        </div>
      )}
      {ok && <p className="purchase-inline-ok" data-testid="payment-success">{ok}</p>}

      <div className="purchase-search-bar is-toolbar">
        <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
          <Search size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari no pembayaran / faktur" />
        </form>
        <div className="purchase-filter-tabs" data-testid="payment-status-filters" role="tablist">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={statusFilter === f.id}
              className={`ghost sm ${statusFilter === f.id ? "is-active" : ""}`}
              data-testid={`payment-filter-${f.id.toLowerCase()}`}
              onClick={() => setStatusFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="purchase-table-wrap">
        <table className="prod-table purchase-table purchase-table-pay" data-testid="payments-table">
          <thead>
            <tr>
              <th>No Pembayaran</th>
              <th>No Faktur</th>
              <th>Supplier</th>
              <th>Tgl Pengajuan</th>
              <th className="num">Nominal</th>
              <th className="cell-status">Status</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr className="purchase-empty-row">
                <td colSpan={6} className="muted small">Memuat…</td>
              </tr>
            )}
            {!loading && rows.length === 0 && (
              <tr className="purchase-empty-row">
                <td colSpan={6} className="muted">
                  Belum ada pengajuan pembayaran. Ajukan dari halaman Faktur Pembelian.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.id}
                {...purchaseRowInteractionProps(() => openDetail(r.id), {
                  testId: `payment-row-${r.id}`,
                  selected: detail?.id === r.id,
                })}
              >
                <td className="mono cell-clip">{r.payment_number}</td>
                <td className="mono cell-clip">{r.invoice_number}</td>
                <td className="cell-clip" title={r.supplier}>{r.supplier}</td>
                <td className="nowrap" data-testid={`payment-submitted-at-cell-${r.id}`}>
                  {formatPurchaseDateTime(r.submitted_at) || "—"}
                </td>
                <td className="num">{formatRupiah(r.amount)}</td>
                <td className="cell-status">
                  <span className={`purchase-status-badge ${purchaseStatusPillClass(listStatus(r))}`}>
                    {listStatus(r)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detailOpen && detail && (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          data-testid="payment-process-modal"
          onClick={closeDetail}
        >
          <div className="modal-inner purchase-payment-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <h3>Detail / Proses Pembayaran</h3>
                <p className="purchase-modal-helper">
                  Status pengajuan terpisah dari status keuangan faktur (BELUM DIBAYAR / DP / LUNAS).
                </p>
              </div>
              <button type="button" className="ghost sm" onClick={closeDetail} disabled={confirmBusy}>×</button>
            </div>
            <div className="purchase-modal-body" data-testid="payment-process-body">
              {err ? (
                <div className="purchase-error-panel" role="alert" style={{ marginBottom: 10 }}>
                  <AlertCircle size={16} color="#a53f2b" />
                  <span>{err}</span>
                </div>
              ) : null}
              <div className="purchase-detail-meta" data-testid="payment-detail">
                <div>
                  <span className="label">No Pembayaran</span>
                  <span className="value mono">{detail.payment_number}</span>
                </div>
                <div>
                  <span className="label">No Faktur</span>
                  <span className="value mono">{detail.invoice_number}</span>
                </div>
                <div>
                  <span className="label">Supplier</span>
                  <span className="value">{detail.supplier || "—"}</span>
                </div>
                <div>
                  <span className="label">Nominal</span>
                  <span className="value">{formatRupiah(detail.amount)}</span>
                </div>
                <div data-testid="payment-submission-status">
                  <span className="label">Status Pengajuan</span>
                  <span className={`purchase-status-badge ${purchaseStatusPillClass(detail.submission_status_label)}`}>
                    {detail.submission_status_label || (submitted ? "DIAJUKAN" : "BELUM DIAJUKAN")}
                  </span>
                </div>
                <div data-testid="payment-settlement-status">
                  <span className="label">Status Settlement</span>
                  <span className={`purchase-status-badge ${purchaseStatusPillClass(detail.settlement_status_label)}`}>
                    {detail.settlement_status_label || (settled ? "DIBAYAR" : "BELUM DIBAYAR")}
                  </span>
                </div>
                <div data-testid="payment-submitted-at">
                  <span className="label">Tanggal Pengajuan</span>
                  <span className="value">{formatPurchaseDateTime(detail.submitted_at) || "—"}</span>
                </div>
                <div data-testid="payment-submitted-by">
                  <span className="label">Diajukan oleh</span>
                  <span className="value">{detail.submitted_by_name || detail.submitted_by?.name || "—"}</span>
                </div>
                <div data-testid="payment-submitted-to">
                  <span className="label">Diajukan kepada</span>
                  <span className="value">{detail.submitted_to_name || "—"}</span>
                </div>
                <div>
                  <span className="label">Status Keuangan Faktur</span>
                  <span className={`purchase-status-badge ${purchaseStatusPillClass(detail.invoice_payment_status)}`}>
                    {detail.invoice_payment_status || "—"}
                  </span>
                </div>
                {settled ? (
                  <>
                    <div data-testid="payment-paid-by">
                      <span className="label">Dikonfirmasi oleh</span>
                      <span className="value">{detail.paid_by_name || detail.paid_by?.name || "—"}</span>
                    </div>
                    <div data-testid="payment-paid-at">
                      <span className="label">Dibayar pada</span>
                      <span className="value">{formatPurchaseDateTime(detail.paid_at)}</span>
                    </div>
                  </>
                ) : null}
              </div>

              <PurchaseDocuments
                entityType="purchase_payment"
                entityId={detail.id}
                canEdit={canCreate}
                hint="Bukti transfer / bukti pembayaran."
                testId="payment-documents"
              />

              <PurchaseActivityHistory doc={detail} testId="payment-activity" />
            </div>
            <div className="purchase-modal-actions" data-testid="payment-process-footer">
              <button type="button" className="ghost" onClick={closeDetail} disabled={confirmBusy}>Tutup</button>
              {canCreate && (
                <button
                  type="button"
                  className="ghost danger"
                  data-testid="btn-hapus-pay-detail"
                  disabled={detail.can_cancel === false || settled || confirmBusy}
                  title={
                    settled
                      ? "Pembayaran sudah dikonfirmasi dibayar."
                      : (detail.can_cancel === false ? (detail.cancel_block_reason || "") : undefined)
                  }
                  onClick={() => openCancel(detail)}
                >
                  Hapus
                </button>
              )}
              {detail.submission_chat_room_id ? (
                <Link
                  className="ghost"
                  data-testid="btn-buka-chat-pay-detail"
                  to={`/chat/${encodeURIComponent(detail.submission_chat_room_id)}`}
                >
                  Buka Chat
                </Link>
              ) : null}
              {canCreate && detail.can_confirm_paid && (
                <button
                  type="button"
                  className="primary"
                  data-testid="btn-konfirmasi-dibayar"
                  disabled={confirmBusy}
                  onClick={confirmPaid}
                >
                  {confirmBusy ? "Mengonfirmasi…" : "Konfirmasi Dibayar"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <PurchaseCancelModal
        open={Boolean(cancelTarget)}
        title="Hapus Pengajuan Pembayaran?"
        body="Dokumen akan dikeluarkan dari daftar aktif. Riwayat pembatalan tetap tersimpan."
        busy={cancelBusy}
        error={cancelErr}
        onCancel={() => !cancelBusy && setCancelTarget(null)}
        onConfirm={confirmCancel}
      />
    </div>
  );
}
