import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, ChevronRight, ClipboardList, MapPin, TrendingUp, WalletCards } from "lucide-react";
import api from "../lib/api";
import { currentSalesMonth, formatSalesDate, formatSalesMoney } from "../lib/salesPwa";

export default function SalesPwaDashboardPage() {
  const [month, setMonth] = useState(currentSalesMonth());
  const [data, setData] = useState(null);
  const [dataMonth, setDataMonth] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    api.salesPwaDashboard({ month })
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setDataMonth(month);
        }
      })
      .catch((err) => { if (!cancelled) setError(err.response?.data?.detail || "Dashboard gagal dimuat"); });
    return () => { cancelled = true; };
  }, [month]);

  const visible = data && dataMonth === month && (!data.month || data.month === month) ? data : null;
  const metrics = visible?.metrics || {};
  return (
    <>
      <header className="spwa-top spwa-top-inline">
        <div><p className="spwa-kicker">PWA SALES</p><h1>Dashboard Saya</h1></div>
        <input aria-label="Bulan dashboard" type="month" value={month} onChange={(event) => { setMonth(event.target.value); setError(""); }} />
      </header>
      <main className="spwa-main" data-testid="sales-pwa-dashboard">
        {error && <div className="spwa-err">{error}</div>}
        {!visible && !error && <div className="spwa-muted">Memuat data penjualan...</div>}
        {visible && (
          <>
            <section className="spwa-metric-grid" aria-label="Ringkasan penjualan">
              <article className="spwa-metric is-revenue">
                <TrendingUp size={17} />
                <span>Omzet faktur bulan ini</span>
                <strong>{formatSalesMoney(metrics.revenue)}</strong>
                <small>{metrics.invoice_count || 0} faktur final</small>
              </article>
              <article className="spwa-metric is-debt">
                <WalletCards size={17} />
                <span>Piutang pelanggan</span>
                <strong>{formatSalesMoney(metrics.receivables)}</strong>
                <small>{metrics.receivable_invoice_count || 0} faktur belum lunas</small>
              </article>
              <article className="spwa-metric">
                <ClipboardList size={17} />
                <span>PO aktif</span>
                <strong>{metrics.active_po || 0}</strong>
                <small>{metrics.buffer_po || 0} PO buffer</small>
              </article>
              <article className="spwa-metric">
                <MapPin size={17} />
                <span>Visit bulan ini</span>
                <strong>{metrics.visit_month || 0}</strong>
                <small>{metrics.visit_overdue || 0} reminder terlambat</small>
              </article>
            </section>

            <section className="spwa-panel">
              <div className="spwa-section-head">
                <div><p className="spwa-kicker">AGENDA</p><h2>Jadwal visit</h2></div>
                <Link to="/sales/visit">Kalender <ChevronRight size={15} /></Link>
              </div>
              {(visible.upcoming_visits || []).length === 0 && <p className="spwa-empty compact">Belum ada reminder visit bulan ini.</p>}
              <div className="spwa-agenda">
                {(visible.upcoming_visits || []).map((item) => (
                  <Link key={item.id} to="/sales/visit" className={item.overdue ? "is-overdue" : ""}>
                    <CalendarDays size={16} />
                    <span><strong>{item.outlet}</strong><small>{formatSalesDate(item.date)} · {item.overdue ? "Terlambat" : "Terjadwal"}</small></span>
                  </Link>
                ))}
              </div>
            </section>

            <section className="spwa-source-note">
              <strong>Sumber angka</strong>
              <span>Omzet: faktur final berdasarkan tanggal faktur. Piutang: faktur final dikurangi penerimaan tercatat. Semua data dibatasi oleh sales_user_id akun ini.</span>
            </section>
          </>
        )}
      </main>
    </>
  );
}
