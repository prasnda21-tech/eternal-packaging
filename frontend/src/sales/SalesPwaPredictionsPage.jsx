import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import api from "../lib/api";
import { formatSalesDate } from "../lib/salesPwa";

const BUCKETS = [
  { id: "follow_up", label: "Perlu follow-up" },
  { id: "tujuh_hari", label: "7 hari ke depan" },
  { id: "berikutnya", label: "Berikutnya" },
  { id: "belum_cukup", label: "Belum cukup data" },
];

export default function SalesPwaPredictionsPage() {
  const { customerKey } = useParams();
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let cancelled = false;
    const req = customerKey
      ? api.salesPwaPredictionDetail(customerKey)
      : api.salesPwaPredictions();
    req
      .then((res) => { if (!cancelled) setData(res); })
      .catch((e) => { if (!cancelled) setErr(e.response?.status === 404 ? "Prediksi tidak ditemukan" : (e.response?.data?.detail || e.message)); });
    return () => { cancelled = true; };
  }, [customerKey]);

  const buckets = data?.buckets || {};
  const analysis = data?.analysis;

  return (
    <>
      <header className="spwa-top">
        {customerKey ? <Link to="/sales/prediksi" className="spwa-back"><ChevronLeft size={14} /> Prediksi</Link> : null}
        <p className="spwa-kicker">PWA SALES</p>
        <h1>Prediksi Order</h1>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-predictions">
        {err && <div className="spwa-err">{err}</div>}
        {analysis && (
          <section className="spwa-card" data-testid="sales-prediction-detail">
            <h2>{analysis.customer || data.customer}</h2>
            <p className="spwa-pred-status">{analysis.followup_status}</p>
            <p className="spwa-muted" style={{ padding: 0 }}>{analysis.evidence}</p>
            <dl className="spwa-kv">
              <dt>Mulai</dt><dd>{formatSalesDate(analysis.prediction_start)}</dd>
              <dt>Sampai</dt><dd>{formatSalesDate(analysis.prediction_end)}</dd>
              <dt>Histori</dt><dd>{analysis.total_orders || 0} order (min {analysis.min_orders || 3})</dd>
            </dl>
          </section>
        )}
        {!customerKey && BUCKETS.map((b) => (
          <section key={b.id} className="spwa-card" data-testid={`sales-pred-bucket-${b.id}`}>
            <h2>{b.label}</h2>
            {(buckets[b.id] || []).map((row) => (
              <Link
                key={row.customer_key}
                className="spwa-linkrow"
                to={`/sales/prediksi/${encodeURIComponent(row.customer_key)}`}
                style={{ padding: "8px 0", borderBottom: "1px solid #eef2f4" }}
              >
                <strong>{row.customer}</strong>
                <div className="spwa-po">{row.evidence}</div>
                <div className={`spwa-pred-status${row.bucket === "follow_up" ? " warn" : ""}`}>{row.followup_status}</div>
              </Link>
            ))}
            {!(buckets[b.id] || []).length && <p className="spwa-muted" style={{ padding: 0 }}>Tidak ada.</p>}
          </section>
        ))}
      </main>
    </>
  );
}
