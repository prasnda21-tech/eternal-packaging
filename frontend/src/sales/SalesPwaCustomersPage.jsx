import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import api from "../lib/api";
import { formatSalesDate, formatSalesWibInstant, salesWibParts } from "../lib/salesPwa";

export default function SalesPwaCustomersPage() {
  const [q, setQ] = useState("");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let cancelled = false;
    api.salesPwaCustomers({ q })
      .then((res) => { if (!cancelled) setData(res); })
      .catch((e) => { if (!cancelled) setErr(e.response?.data?.detail || e.message); });
    return () => { cancelled = true; };
  }, [q]);

  return (
    <>
      <header className="spwa-top">
        <p className="spwa-kicker">PWA SALES</p>
        <h1>Pelanggan</h1>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-customers">
        <label className="spwa-search">
          <Search size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari pelanggan / PO" data-testid="sales-customers-search" />
        </label>
        {err && <div className="spwa-err">{err}</div>}
        {(data?.customers || []).map((c) => (
          <article key={c.customer_key} className="spwa-customer-block" data-testid={`sales-customer-block-${c.customer_key}`}>
            <Link
              className="spwa-list-btn"
              to={(c.prediction_key || c.customer_key) ? `/sales/prediksi/${encodeURIComponent(c.prediction_key || c.customer_key)}` : "/sales/prediksi"}
              data-testid={`sales-customer-${c.customer_key}`}
            >
              <strong>{c.customer}</strong>
              <div className="spwa-po">{c.outlet || "—"} · {c.po_count} PO · terakhir {formatSalesDate(c.last_order_date)}</div>
              {c.prediction?.followup_status ? (
                <div className="spwa-pred-status">{c.prediction.followup_status}</div>
              ) : null}
            </Link>
            {c.visit_link === "linked" && (
              <div className="spwa-visit-history" data-testid={`sales-customer-visits-${c.customer_key}`}>
                {(c.visit_history || []).length === 0 && <p className="spwa-empty compact">Belum ada riwayat visit.</p>}
                {(c.visit_history || []).map((visit) => (
                  <article key={visit.id}>
                    <strong>{visit.outlet || "Outlet"}</strong>
                    {visit.activity_at ? <span>{formatSalesDate(salesWibParts(visit.activity_at)?.day || visit.activity_at)}</span> : null}
                    {String(visit.result || "").trim() ? <span>Hasil: {visit.result}</span> : null}
                    {String(visit.next_action || "").trim() ? <small>Next action: {visit.next_action}</small> : null}
                    {String(visit.notes || "").trim() ? <small>{visit.notes}</small> : null}
                    {visit.next_follow_up_at ? <small>Follow-up: {formatSalesWibInstant(visit.next_follow_up_at)}</small> : null}
                  </article>
                ))}
              </div>
            )}
          </article>
        ))}
        {data && !(data.customers || []).length && <div className="spwa-empty">Tidak ada pelanggan pada PO Anda.</div>}
      </main>
    </>
  );
}
