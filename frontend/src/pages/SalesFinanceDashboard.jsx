import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { dashboardNavVisible } from "../lib/navAccess";
import FinancialDashboard from "./FinancialDashboard";
import SalesDashboard from "./SalesDashboard";
import DashboardTabs from "./DashboardTabs";
import "./salesDashboardPremium.css";

const DISTINCTION = "Omzet penjualan mengikuti faktur (setelah diskon barang, sebelum PPN dan biaya lain) pada tanggal faktur. Pendapatan mengikuti laba rugi dari jurnal. Saldo piutang pelanggan adalah sisa tagihan faktur saat ini. Piutang usaha adalah saldo akun piutang di neraca pada tanggal akhir. Kedua bagian tidak dijumlahkan.";

export default function SalesFinanceDashboard() {
  const { hasPerm } = useAuth();
  const location = useLocation();
  const access = dashboardNavVisible(hasPerm);
  const sales = access.sales;
  const finance = access.finance;

  const navigate = useNavigate();
  const active = finance && (location.hash === "#keuangan" || !sales) ? "keuangan" : "penjualan";
  const tabs = [
    ...(sales ? [{ id: "penjualan", label: "Penjualan" }] : []),
    ...(finance ? [{ id: "keuangan", label: "Keuangan" }] : []),
  ];

  if (!sales && !finance) return <Navigate to="/" replace />;

  return (
    <div className="sales-finance-page sf-premium" data-testid="sales-finance-dashboard">
      <header className="sf-head">
        <div><p className="sf-eyebrow">ETERNAL PACKAGING / DASHBOARD</p><h1>Dashboard Penjualan</h1>
          <p className="sf-subtitle">Pantau penjualan, pelanggan, dan piutang dalam satu tempat.</p></div>
        {sales && finance ? <details className="sf-info"><summary>ⓘ Tentang angka</summary>
          <p data-testid="sales-finance-distinction">{DISTINCTION}</p></details> : null}
      </header>
      <DashboardTabs id="dashboard" label="Dashboard" items={tabs} value={active}
        onChange={(id) => navigate({ pathname: location.pathname, search: location.search, hash: `#${id}` }, { replace: true })} />
      <div className="sales-finance-scroll">
        {sales ? (
          <section id="dashboard-panel-penjualan" role="tabpanel" aria-labelledby="dashboard-tab-penjualan" hidden={active !== "penjualan"} tabIndex={0} aria-label="Penjualan" data-testid="section-penjualan">
            <SalesDashboard embedded />
          </section>
        ) : null}
        {finance ? (
          <section id="dashboard-panel-keuangan" role="tabpanel" aria-labelledby="dashboard-tab-keuangan" hidden={active !== "keuangan"} tabIndex={0} aria-label="Keuangan" data-testid="section-keuangan">
            <FinancialDashboard embedded />
          </section>
        ) : null}
      </div>
    </div>
  );
}

