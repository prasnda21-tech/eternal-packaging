import { useCallback, useEffect, useRef, useState } from "react";
import api, { publicApiUrl } from "../lib/api";
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

export default function TrackingInvoices({ token }) {
  const [phase, setPhase] = useState("loading");
  const [invoices, setInvoices] = useState([]);
  const [openId, setOpenId] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailPhase, setDetailPhase] = useState("idle");
  const listReq = useRef(0);
  const detailReq = useRef(0);

  const loadList = useCallback(() => {
    const req = ++listReq.current;
    detailReq.current += 1;
    setPhase("loading");
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
      setOpenId("");
      setDetail(null);
      setDetailPhase("idle");
      return;
    }
    setOpenId(invoiceId);
    loadDetail(invoiceId);
  };

  return (
    <section className="tracking-card tracking-invoices" data-testid="tracking-invoices" aria-label="Tagihan">
      <div className="tracking-inv-head">
        <h2>Tagihan</h2>
      </div>

      {phase === "loading" && (
        <p className="tracking-inv-loading" data-testid="tracking-invoices-loading">Memuat tagihan...</p>
      )}

      {phase === "error" && (
        <div className="tracking-inv-error" data-testid="tracking-invoices-error">
          <p>Tagihan gagal dimuat.</p>
          <button type="button" className="tracking-inv-btn" data-testid="tracking-invoices-retry" onClick={loadList}>
            Coba lagi
          </button>
        </div>
      )}

      {phase === "ready" && invoices.length === 0 && (
        <p className="tracking-inv-empty" data-testid="tracking-invoices-empty">Faktur belum tersedia</p>
      )}

      {phase === "ready" && invoices.length > 0 && (
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
                    <span>Total faktur</span>
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
                    {open ? "Tutup" : "Lihat Faktur"}
                  </button>
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
                          <div><dt>Total faktur</dt><dd data-testid="tracking-invoice-detail-total">{formatInvoiceMoney(detail.grand_total)}</dd></div>
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
    </section>
  );
}
