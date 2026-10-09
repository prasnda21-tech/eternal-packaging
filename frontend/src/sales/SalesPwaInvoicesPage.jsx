import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import api from "../lib/api";
import { currentSalesMonth, formatSalesMoney } from "../lib/salesPwa";
import { formatSalesInvoiceDate } from "../lib/salesInvoiceDate";
import {
  invoiceOutstandingAmount,
  paymentStatusLabel,
  paymentStatusTone,
  receivableById,
  salesApiError,
  salesInvoiceHref,
  salesInvoiceMonth,
} from "./salesPwaFinance";

function initialMonth(search) {
  const value = salesInvoiceMonth(new URLSearchParams(search || "").get("month"));
  return value || currentSalesMonth();
}

export default function SalesPwaInvoicesPage() {
  const location = useLocation();
  const [month, setMonth] = useState(() => initialMonth(location.search));
  const [data, setData] = useState(null);
  const [receivables, setReceivables] = useState([]);
  const [outstandingNote, setOutstandingNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError("");
    setOutstandingNote("");
    Promise.allSettled([
      api.salesPwaInvoices({ month }),
      api.salesPwaReceivables(),
    ]).then(([invoiceResult, receivableResult]) => {
      if (cancelled) return;
      if (invoiceResult.status === "rejected") {
        setError(salesApiError(invoiceResult.reason, "Invoice gagal dimuat"));
        return;
      }
      setData(invoiceResult.value || { invoices: [], summary: {} });
      if (receivableResult.status === "fulfilled") {
        setReceivables(receivableResult.value?.receivables || []);
      } else {
        setReceivables([]);
        setOutstandingNote("Sisa piutang belum dapat dimuat.");
      }
    });
    return () => { cancelled = true; };
  }, [month]);

  const invoices = data?.invoices || [];
  const byReceivable = receivableById(receivables);
  const summary = data?.summary || {};

  return (
    <>
      <header className="spwa-top spwa-top-inline">
        <div>
          <p className="spwa-kicker">PWA SALES</p>
          <h1>Invoice</h1>
        </div>
        <input aria-label="Bulan invoice" type="month" value={month} onChange={(event) => setMonth(event.target.value)} data-testid="sales-invoice-month" />
      </header>
      <main className="spwa-main" data-testid="sales-pwa-invoices">
        {error && <div className="spwa-err">{error}</div>}
        {!data && !error && <div className="spwa-muted">Memuat invoice...</div>}
        {data && (
          <>
            <p className="spwa-filter-hint" data-testid="sales-invoice-summary">
              {summary.invoice_count || 0} faktur · {formatSalesMoney(summary.total_amount)}
            </p>
            {outstandingNote && <div className="spwa-err">{outstandingNote}</div>}
            <ul className="spwa-po-cards">
              {invoices.map((invoice) => {
                const receivable = byReceivable.get(String(invoice.id));
                const outstanding = invoiceOutstandingAmount(invoice, receivable);
                return (
                  <li key={invoice.id}>
                    <Link className="spwa-po-card" to={salesInvoiceHref(invoice, "", month)} data-testid={`sales-invoice-${invoice.id}`}>
                      <div className="spwa-po-card-customer">{invoice.customer_name || "—"}</div>
                      <div className={`spwa-po-card-status ${paymentStatusTone(invoice.payment_status)}`}>
                        {paymentStatusLabel(invoice.payment_status)}
                        <span className="spwa-po-card-number">{invoice.invoice_number || "—"}</span>
                      </div>
                      <div className="spwa-po-card-line"><span>Total</span><strong>{formatSalesMoney(invoice.grand_total)}</strong></div>
                      <div className="spwa-po-card-line"><span>Sisa</span><strong>{outstanding == null ? "—" : formatSalesMoney(outstanding)}</strong></div>
                      <div className="spwa-po-card-line"><span>Tanggal</span><strong>{formatSalesInvoiceDate(invoice.invoice_date) || "—"}</strong></div>
                    </Link>
                  </li>
                );
              })}
            </ul>
            {!invoices.length && <div className="spwa-empty">Tidak ada invoice final pada bulan ini.</div>}
          </>
        )}
      </main>
    </>
  );
}
