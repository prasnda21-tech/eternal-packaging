import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import api from "../lib/api";
import { currentSalesMonth, formatSalesDate, formatSalesMoney } from "../lib/salesPwa";
import { mergeSalesInvoiceDetail, paymentStatusLabel, salesApiError, salesInvoiceMonth } from "./salesPwaFinance";

function moneyOrDash(value) {
  return value == null || !Number.isFinite(Number(value)) ? "—" : formatSalesMoney(value);
}

export default function SalesPwaInvoiceDetailPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const month = salesInvoiceMonth(params.get("month")) || currentSalesMonth();
  const fromReceivables = params.get("from") === "receivables";
  const [invoice, setInvoice] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setInvoice(null);
    setError("");
    Promise.all([
      api.salesPwaInvoices({ month }),
      api.salesPwaReceivables(),
    ]).then(([invoices, receivables]) => {
      if (cancelled) return;
      const invoiceRow = (invoices?.invoices || []).find((row) => String(row.id) === String(id));
      const receivableRow = (receivables?.receivables || []).find((row) => String(row.id) === String(id));
      const merged = mergeSalesInvoiceDetail(invoiceRow, receivableRow);
      if (!merged) {
        setError("Invoice tidak ditemukan");
        setInvoice(false);
        return;
      }
      setInvoice(merged);
    }).catch((err) => {
      if (!cancelled) setError(salesApiError(err, "Invoice gagal dimuat"));
    });
    return () => { cancelled = true; };
  }, [id, month]);

  const backTo = fromReceivables ? "/sales/receivables" : `/sales/invoices?month=${encodeURIComponent(month)}`;

  return (
    <>
      <header className="spwa-top spwa-detail-top">
        <Link to={backTo} className="spwa-back" data-testid="sales-invoice-back"><ChevronLeft size={14} /> {fromReceivables ? "Piutang" : "Invoice"}</Link>
        {invoice ? (
          <div className="spwa-detail-identity">
            <h1 className="spwa-top-customer">{invoice.customer_name || "—"}</h1>
            <p className="spwa-top-meta">{invoice.invoice_number || "—"}</p>
            <p className="spwa-top-meta">{formatSalesDate(invoice.invoice_date)}</p>
          </div>
        ) : <h1>Detail invoice</h1>}
      </header>
      <main className="spwa-main" data-testid="sales-pwa-invoice-detail">
        {error && <div className="spwa-err">{error}</div>}
        {!invoice && !error && <div className="spwa-muted">Memuat invoice...</div>}
        {invoice && (
          <section className="spwa-card" data-testid="sales-invoice-detail-card">
            <h2>{paymentStatusLabel(invoice.payment_status)}</h2>
            <dl className="spwa-kv">
              <dt>Jatuh tempo</dt>
              <dd>{formatSalesDate(invoice.due_date)}</dd>
              <dt>Total</dt>
              <dd>{moneyOrDash(invoice.grand_total)}</dd>
              <dt>Sudah dibayar</dt>
              <dd>{moneyOrDash(invoice.paid_amount)}</dd>
              <dt>Sisa</dt>
              <dd>{moneyOrDash(invoice.outstanding_amount)}</dd>
            </dl>
          </section>
        )}
      </main>
    </>
  );
}
