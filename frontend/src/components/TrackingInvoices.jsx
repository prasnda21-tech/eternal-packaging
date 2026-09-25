import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FileText } from "lucide-react";
import api, { publicApiUrl } from "../lib/api";
import TrackingInvoicePdfViewer from "./TrackingInvoicePdfViewer";
import {
  formatInvoiceDate,
  formatInvoiceMoney,
  invoiceStatusClass,
  safeInvoicePdfPath,
} from "../lib/trackingInvoices";

function lineQty(line) {
  const qty = line?.quantity;
  if (qty == null || qty === "") return "—";
  const n = Number(qty);
  const shown = Number.isFinite(n) ? n.toLocaleString("id-ID") : String(qty);
  return line.unit ? `${shown} ${line.unit}` : shown;
}

function countLabel(count) {
  return count === 1 ? "1 faktur" : `${count} faktur`;
}

export default function TrackingInvoices({ token }) {
  const [phase, setPhase] = useState("loading");
  const [invoices, setInvoices] = useState([]);
  const [drawer, setDrawer] = useState(false);
  const [openId, setOpenId] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailPhase, setDetailPhase] = useState("idle");
  const [pdfTarget, setPdfTarget] = useState(null);
  const listReq = useRef(0);
  const detailReq = useRef(0);
  const bodyRef = useRef(null);
  const scrollRef = useRef(0);

  const loadList = useCallback(() => {
    const req = ++listReq.current;
    detailReq.current += 1;
    setPhase("loading");
    setDrawer(false);
    setPdfTarget(null);
    setOpenId("");
    setDetail(null);
    setDetailPhase("idle");
    api.publicTrackingInvoices(token).then((data) => {
      if (listReq.current !== req) return;
      if (!Array.isArray(data?.invoices)) {
        setInvoices([]);
        setPhase("error");
        return;
      }
      setInvoices(data.invoices);
      setPhase("ready");
    }).catch(() => {
      if (listReq.current !== req) return;
      setInvoices([]);
      setPhase("error");
    });
  }, [token]);

  useEffect(() => {
    loadList();
    return () => { listReq.current += 1; };
  }, [loadList]);

  const closeDrawer = useCallback(() => {
    detailReq.current += 1;
    setDrawer(false);
    setPdfTarget(null);
    setOpenId("");
    setDetail(null);
    setDetailPhase("idle");
  }, []);

  const openPdf = (invoice) => {
    const path = safeInvoicePdfPath(invoice?.pdf_url);
    if (!path) return;
    scrollRef.current = bodyRef.current?.scrollTop || 0;
    setPdfTarget({ id: invoice.id, number: invoice.invoice_number, path });
  };

  const closePdf = () => setPdfTarget(null);

  useEffect(() => {
    if (pdfTarget || !bodyRef.current) return;
    bodyRef.current.scrollTop = scrollRef.current;
  }, [pdfTarget]);

  useEffect(() => {
    if (!drawer) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") closeDrawer();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, closeDrawer]);

  const loadDetail = useCallback((invoiceId) => {
    const req = ++detailReq.current;
    setDetail(null);
    setDetailPhase("loading");
    api.publicTrackingInvoice(token, invoiceId).then((row) => {
      if (detailReq.current !== req) return;
      if (!row || row.id !== invoiceId) {
        setDetailPhase("error");
        return;
      }
      setDetail(row);
      setDetailPhase("ready");
    }).catch(() => {
      if (detailReq.current === req) setDetailPhase("error");
    });
  }, [token]);

  const toggleInvoice = (invoiceId) => {
    if (openId === invoiceId) {
      detailReq.current += 1;
      setOpenId("");
      setDetail(null);
      setDetailPhase("idle");
      return;
    }
    setOpenId(invoiceId);
    loadDetail(invoiceId);
  };

  const available = phase === "ready" && invoices.length > 0;
  const statusText = phase === "loading"
    ? "Memuat..."
    : phase === "error"
      ? "Gagal dimuat"
      : available
        ? countLabel(invoices.length)
        : "Belum tersedia";
  const actionText = phase === "error" ? "Coba lagi" : "Lihat";

  const onTile = () => {
    if (phase === "error") {
      loadList();
      return;
    }
    if (!available) return;
    setDrawer(true);
  };

  const tile = (
    <button
      type="button"
      className={`tracking-doc-tile${available || phase === "error" ? "" : " is-off"}`}
      disabled={phase !== "error" && !available}
      data-testid="tracking-doc-tile-faktur"
      aria-label={`Faktur Penjualan: ${statusText}`}
      onClick={onTile}
    >
      <span className="tracking-doc-ico"><FileText size={14} /></span>
      <strong>Faktur Penjualan</strong>
      <span
        className="muted small"
        data-testid={
          phase === "loading"
            ? "tracking-invoices-loading"
            : phase === "error"
              ? "tracking-invoices-error"
              : available
                ? "tracking-invoices-count"
                : "tracking-invoices-empty"
        }
      >
        {phase === "loading" || phase === "error" ? statusText : (available ? `✓ ${statusText}` : `○ ${statusText}`)}
      </span>
      <span className="tracking-doc-action" data-testid={phase === "error" ? "tracking-invoices-retry" : "tracking-invoice-lihat"}>
        {actionText}
      </span>
    </button>
  );

  const sheet = drawer ? (
    <div className="tracking-sheet-root" data-testid="tracking-invoice-sheet" onClick={closeDrawer}>
      <div
        className="tracking-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="Faktur Penjualan"
        onClick={(event) => event.stopPropagation()}
      >
        <header className={`tracking-sheet-head${pdfTarget ? " is-pdf" : ""}`}>
          {pdfTarget ? (
            <button type="button" className="ghost sm" data-testid="tracking-invoice-pdf-back" onClick={closePdf}>
              Kembali
            </button>
          ) : null}
          <h2 data-testid="tracking-invoice-sheet-title">{pdfTarget ? pdfTarget.number : "Faktur Penjualan"}</h2>
          <button type="button" className="ghost sm" data-testid="tracking-invoice-sheet-close" onClick={closeDrawer}>
            Tutup
          </button>
        </header>
        <div
          className={`tracking-sheet-body${pdfTarget ? " is-pdf" : ""}`}
          ref={bodyRef}
          data-testid="tracking-invoice-sheet-body"
        >
          {pdfTarget ? (
            <TrackingInvoicePdfViewer
              key={pdfTarget.id}
              pdfPath={pdfTarget.path}
              invoiceId={pdfTarget.id}
            />
          ) : (
          <div className="tracking-inv-list">
            {invoices.map((inv) => {
              const pdfPath = safeInvoicePdfPath(inv.pdf_url);
              const open = openId === inv.id;
              return (
                <article key={inv.id} className="tracking-inv-row" data-testid={`tracking-invoice-${inv.id}`}>
                  <div className="tracking-inv-top">
                    <strong data-testid={`tracking-invoice-number-${inv.id}`}>{inv.invoice_number}</strong>
                    <span
                      className={`tracking-inv-status ${invoiceStatusClass(inv.payment_label)}`}
                      data-testid={`tracking-invoice-status-${inv.id}`}
                    >
                      {inv.payment_label}
                    </span>
                  </div>
                  <p className="tracking-inv-meta">
                    <span>{formatInvoiceDate(inv.invoice_date)}</span>
                    {inv.due_date ? (
                      <span data-testid={`tracking-invoice-due-${inv.id}`}>
                        Jatuh tempo {formatInvoiceDate(inv.due_date)}
                      </span>
                    ) : null}
                  </p>
                  <div className="tracking-inv-figures">
                    <div>
                      <span>Total</span>
                      <strong data-testid={`tracking-invoice-total-${inv.id}`}>{formatInvoiceMoney(inv.grand_total)}</strong>
                    </div>
                    <div>
                      <span>Sisa pembayaran</span>
                      <strong data-testid={`tracking-invoice-outstanding-${inv.id}`}>{formatInvoiceMoney(inv.outstanding_amount)}</strong>
                    </div>
                  </div>
                  <div className="tracking-inv-actions">
                    <button
                      type="button"
                      className="tracking-inv-btn"
                      aria-expanded={open}
                      data-testid={`tracking-invoice-open-${inv.id}`}
                      onClick={() => toggleInvoice(inv.id)}
                    >
                      {open ? "Tutup detail" : "Detail"}
                    </button>
                    {pdfPath ? (
                      <button
                        type="button"
                        className="tracking-inv-btn is-ghost"
                        data-testid={`tracking-invoice-view-pdf-${inv.id}`}
                        onClick={() => openPdf(inv)}
                      >
                        Buka PDF
                      </button>
                    ) : null}
                    {pdfPath ? (
                      <a
                        className="tracking-inv-btn is-ghost"
                        href={publicApiUrl(pdfPath)}
                        download
                        data-testid={`tracking-invoice-pdf-${inv.id}`}
                      >
                        Unduh PDF
                      </a>
                    ) : null}
                  </div>
                  {open && (
                    <div className="tracking-inv-detail" data-testid={`tracking-invoice-detail-${inv.id}`}>
                      {detailPhase === "loading" && <p data-testid="tracking-invoice-detail-loading">Memuat faktur...</p>}
                      {detailPhase === "error" && (
                        <div data-testid="tracking-invoice-detail-error">
                          <p>Detail faktur gagal dimuat.</p>
                          <button type="button" className="tracking-inv-btn" data-testid="tracking-invoice-detail-retry" onClick={() => loadDetail(inv.id)}>
                            Coba lagi
                          </button>
                        </div>
                      )}
                      {detailPhase === "ready" && detail && (
                        <>
                          <p className="tracking-inv-detail-status" data-testid="tracking-invoice-detail-status">
                            Status pembayaran: {detail.payment_label}
                          </p>
                          <div className="tracking-inv-lines">
                            {(detail.lines || []).map((line, index) => (
                              <div key={`${line.item_name}-${index}`} className="tracking-inv-line" data-testid={`tracking-invoice-line-${index}`}>
                                <div>
                                  <strong>{line.item_name}</strong>
                                  <span>{lineQty(line)} × {formatInvoiceMoney(line.unit_price)}</span>
                                </div>
                                <strong>{formatInvoiceMoney(line.line_subtotal)}</strong>
                              </div>
                            ))}
                          </div>
                          <dl className="tracking-inv-totals">
                            <div><dt>Subtotal</dt><dd>{formatInvoiceMoney(detail.subtotal)}</dd></div>
                            {Number(detail.discount_amount) > 0 && (
                              <div><dt>Diskon</dt><dd>{formatInvoiceMoney(detail.discount_amount)}</dd></div>
                            )}
                            {Number(detail.ppn_amount) > 0 && (
                              <div><dt>PPN</dt><dd>{formatInvoiceMoney(detail.ppn_amount)}</dd></div>
                            )}
                            <div><dt>Total</dt><dd data-testid="tracking-invoice-detail-total">{formatInvoiceMoney(detail.grand_total)}</dd></div>
                            <div><dt>Sudah dibayar</dt><dd>{formatInvoiceMoney(detail.paid_total)}</dd></div>
                            <div><dt>Sisa pembayaran</dt><dd data-testid="tracking-invoice-detail-outstanding">{formatInvoiceMoney(detail.outstanding_amount)}</dd></div>
                          </dl>
                        </>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
          )}
        </div>
      </div>
    </div>
  ) : null;

  return (
    <>
      {tile}
      {sheet ? createPortal(sheet, document.body) : null}
    </>
  );
}
