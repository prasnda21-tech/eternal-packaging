import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { formatSalesMoney } from "../lib/salesPwa";
import { formatSalesInvoiceDate } from "../lib/salesInvoiceDate";
import { salesApiError, salesInvoiceHref } from "./salesPwaFinance";

export default function SalesPwaReceivablesPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError("");
    api.salesPwaReceivables()
      .then((result) => { if (!cancelled) setData(result || { receivables: [], summary: {} }); })
      .catch((err) => { if (!cancelled) setError(salesApiError(err, "Piutang gagal dimuat")); });
    return () => { cancelled = true; };
  }, []);

  const rows = data?.receivables || [];
  const summary = data?.summary || {};

  return (
    <>
      <header className="spwa-top">
        <p className="spwa-kicker">PWA SALES</p>
        <h1>Piutang berjalan</h1>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-receivables">
        {error && <div className="spwa-err">{error}</div>}
        {!data && !error && <div className="spwa-muted">Memuat piutang...</div>}
        {data && (
          <>
            <p className="spwa-filter-hint" data-testid="sales-receivable-summary">
              {summary.invoice_count || 0} invoice belum lunas · {formatSalesMoney(summary.outstanding_total)}
            </p>
            <ul className="spwa-po-cards">
              {rows.map((row) => (
                <li key={row.id}>
                  <Link className="spwa-po-card" to={salesInvoiceHref(row, "receivables")} data-testid={`sales-receivable-${row.id}`}>
                    <div className="spwa-po-card-customer">{row.customer_name || "—"}</div>
                    <div className="spwa-finance-balance">{formatSalesMoney(row.outstanding_amount)}</div>
                    <div className="spwa-po-card-line"><span>Jatuh tempo</span><strong>{formatSalesInvoiceDate(row.due_date) || "—"}</strong></div>
                    <div className="spwa-po-card-number">{row.invoice_number || "—"} · {formatSalesInvoiceDate(row.invoice_date) || "—"}</div>
                    <div className="spwa-po-card-line"><span>Total</span><strong>{formatSalesMoney(row.grand_total)}</strong></div>
                    <div className="spwa-po-card-line"><span>Sudah dibayar</span><strong>{formatSalesMoney(row.paid_amount)}</strong></div>
                  </Link>
                </li>
              ))}
            </ul>
            {!rows.length && <div className="spwa-empty">Tidak ada invoice yang masih memiliki sisa piutang.</div>}
          </>
        )}
      </main>
    </>
  );
}
