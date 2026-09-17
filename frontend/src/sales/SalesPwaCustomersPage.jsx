import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import api from "../lib/api";
import { formatSalesDate } from "../lib/salesPwa";

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
          <Link
            key={c.customer_key}
            className="spwa-list-btn"
            to={c.customer_key ? `/sales/prediksi/${encodeURIComponent(c.customer_key)}` : "/sales/prediksi"}
            data-testid={`sales-customer-${c.customer_key}`}
          >
            <strong>{c.customer}</strong>
            <div className="spwa-po">{c.outlet || "—"} · {c.po_count} PO · terakhir {formatSalesDate(c.last_order_date)}</div>
            {c.prediction?.followup_status ? (
              <div className="spwa-pred-status">{c.prediction.followup_status}</div>
            ) : null}
          </Link>
        ))}
        {data && !(data.customers || []).length && <div className="spwa-empty">Tidak ada pelanggan pada PO Anda.</div>}
      </main>
    </>
  );
}
