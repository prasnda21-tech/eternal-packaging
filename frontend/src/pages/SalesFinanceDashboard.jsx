import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { dashboardNavVisible } from "../lib/navAccess";
import FinancialDashboard from "./FinancialDashboard";
import SalesDashboard from "./SalesDashboard";

const DISTINCTION = "Omzet penjualan mengikuti faktur (setelah diskon barang, sebelum PPN dan biaya lain) pada tanggal faktur. Pendapatan mengikuti laba rugi dari jurnal. Saldo piutang pelanggan adalah sisa tagihan faktur saat ini. Piutang usaha adalah saldo akun piutang di neraca pada tanggal akhir. Kedua bagian tidak dijumlahkan.";

export default function SalesFinanceDashboard() {
  const { hasPerm } = useAuth();
  const location = useLocation();
  const access = dashboardNavVisible(hasPerm);
  const sales = access.sales;
  const finance = access.finance;

  useEffect(() => {
    if (location.hash !== "#keuangan" || !finance) return undefined;
    const node = document.getElementById("keuangan");
    if (!node) return undefined;
    node.scrollIntoView({ block: "start" });
    return undefined;
  }, [location.hash, finance]);

  if (!sales && !finance) return <Navigate to="/" replace />;

  return (
    <div className="sales-finance-page" data-testid="sales-finance-dashboard">
      <header className="sf-head">
        <h1>Dashboard Penjualan</h1>
      </header>
      <div className="sales-finance-scroll">
        {sales && finance ? (
          <p className="sf-distinction" data-testid="sales-finance-distinction">{DISTINCTION}</p>
        ) : null}
        {sales ? (
          <section id="penjualan" aria-label="Penjualan" data-testid="section-penjualan">
            <SalesDashboard embedded />
          </section>
        ) : null}
        {finance ? (
          <section id="keuangan" aria-label="Keuangan" data-testid="section-keuangan">
            <FinancialDashboard embedded />
          </section>
        ) : null}
      </div>
    </div>
  );
}
