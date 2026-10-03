import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import { periodRange, shouldApplyDashboardResponse } from "../lib/financialDashboard";
import { dashboardNavVisible } from "../lib/navAccess";
import { isSalesPwaRole } from "../lib/salesPwa";
import FinancialDashboard from "./FinancialDashboard";
import SalesDashboard from "./SalesDashboard";
import DashboardTabs from "./DashboardTabs";
import "./salesDashboardPremium.css";

const DISTINCTION = "Omzet penjualan mengikuti faktur (setelah diskon barang, sebelum PPN dan biaya lain) pada tanggal faktur. Pendapatan mengikuti laba rugi dari jurnal. Saldo piutang pelanggan adalah sisa tagihan faktur saat ini. Piutang usaha adalah saldo akun piutang di neraca pada tanggal akhir. Kedua bagian tidak dijumlahkan.";

const PRESETS = [
  { id: "today", label: "Hari ini" },
  { id: "month", label: "Bulan ini" },
  { id: "year", label: "Tahun ini" },
  { id: "custom", label: "Kustom" },
];

function errText(ex) {
  const detail = ex?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return ex?.message || "Gagal memuat";
}

export default function SalesFinanceDashboard() {
  const { hasPerm, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const access = dashboardNavVisible(hasPerm);
  const sales = access.sales;
  const finance = access.finance;
  const personalSales = isSalesPwaRole(user);

  const [preset, setPreset] = useState("month");
  const [custom, setCustom] = useState({ start: "", end: "" });
  const [salesPayload, setSalesPayload] = useState(null);
  const [salesLoading, setSalesLoading] = useState(Boolean(sales));
  const [salesError, setSalesError] = useState("");
  const [financePayload, setFinancePayload] = useState(null);
  const [financeLoading, setFinanceLoading] = useState(Boolean(finance));
  const [financeError, setFinanceError] = useState("");
  const salesReq = useRef(0);
  const financeReq = useRef(0);

  const range = periodRange(preset, custom);
  const rangeReady = Boolean(range.start && range.end && range.start <= range.end);
  const rangeMessage = preset !== "custom"
    ? ""
    : !range.start || !range.end
      ? "Isi tanggal awal dan tanggal akhir."
      : range.start > range.end
        ? "Tanggal awal harus sama atau sebelum tanggal akhir."
        : "";

  const active = finance && (location.hash === "#keuangan" || !sales) ? "keuangan" : "penjualan";
  const tabs = [
    ...(sales ? [{ id: "penjualan", label: "Penjualan" }] : []),
    ...(finance ? [{ id: "keuangan", label: "Keuangan" }] : []),
  ];

  const loadSales = useCallback(async () => {
    if (!sales) return;
    if (!rangeReady) {
      salesReq.current += 1;
      setSalesPayload(null);
      setSalesLoading(false);
      setSalesError("");
      return;
    }
    const current = ++salesReq.current;
    setSalesLoading(true);
    setSalesError("");
    setSalesPayload(null);
    try {
      const data = await api.salesDashboard({
        start_date: range.start,
        end_date: range.end,
        preset,
      });
      if (!shouldApplyDashboardResponse(current, salesReq.current)) return;
      setSalesPayload(data);
      setSalesLoading(false);
    } catch (ex) {
      if (!shouldApplyDashboardResponse(current, salesReq.current)) return;
      setSalesPayload(null);
      setSalesError(errText(ex));
      setSalesLoading(false);
    }
  }, [sales, rangeReady, range.start, range.end, preset]);

  const loadFinance = useCallback(async () => {
    if (!finance) return;
    if (!rangeReady) {
      financeReq.current += 1;
      setFinancePayload(null);
      setFinanceLoading(false);
      setFinanceError("");
      return;
    }
    const current = ++financeReq.current;
    setFinanceLoading(true);
    setFinanceError("");
    setFinancePayload(null);
    try {
      const data = await api.financialDashboard({
        start_date: range.start,
        end_date: range.end,
      });
      if (!shouldApplyDashboardResponse(current, financeReq.current)) return;
      setFinancePayload(data);
      setFinanceLoading(false);
    } catch (ex) {
      if (!shouldApplyDashboardResponse(current, financeReq.current)) return;
      setFinancePayload(null);
      setFinanceError(errText(ex));
      setFinanceLoading(false);
    }
  }, [finance, rangeReady, range.start, range.end]);

  useEffect(() => {
    loadSales();
    return () => { salesReq.current += 1; };
  }, [loadSales]);

  useEffect(() => {
    loadFinance();
    return () => { financeReq.current += 1; };
  }, [loadFinance]);

  const reload = () => {
    loadSales();
    loadFinance();
  };

  if (!sales && !finance) return <Navigate to="/" replace />;

  const managed = {
    preset,
    custom,
    setPreset,
    setCustom,
    payload: salesPayload,
    loading: salesLoading,
    error: salesError,
    reload,
    financeAllowed: finance,
    financePayload,
    financeLoading,
    financeError,
    personalSales: personalSales && finance,
  };

  return (
    <div className="sales-finance-page sf-premium" data-testid="sales-finance-dashboard">
      <header className="sf-head">
        <h1>Dashboard</h1>
        <DashboardTabs id="dashboard" label="Dashboard" items={tabs} value={active}
          onChange={(id) => navigate({ pathname: location.pathname, search: location.search, hash: `#${id}` }, { replace: true })} />
        <div className="sf-period" role="group" aria-label="Rentang tanggal">
          {PRESETS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={preset === item.id ? "is-active" : ""}
              aria-pressed={preset === item.id}
              onClick={() => setPreset(item.id)}
              data-testid={`sales-preset-${item.id}`}
            >
              {item.label}
            </button>
          ))}
          {preset === "custom" ? (
            <span className="sf-custom">
              <input aria-label="Tanggal awal" type="date" value={custom.start} onChange={(event) => setCustom((cur) => ({ ...cur, start: event.target.value }))} data-testid="sales-custom-start" />
              <input aria-label="Tanggal akhir" type="date" value={custom.end} onChange={(event) => setCustom((cur) => ({ ...cur, end: event.target.value }))} data-testid="sales-custom-end" />
            </span>
          ) : (
            <p className="sf-range" data-testid="sales-range">{range.start} – {range.end}</p>
          )}
        </div>
        <button type="button" className="sf-reload" disabled={!rangeReady || salesLoading || financeLoading} onClick={reload} data-testid="sales-reload">Muat ulang</button>
        {sales && finance ? (
          <details className="sf-info">
            <summary>Info</summary>
            <p data-testid="sales-finance-distinction">{DISTINCTION}</p>
          </details>
        ) : null}
      </header>
      {rangeMessage ? <p className="sd-banner" data-testid="sales-range-note">{rangeMessage}</p> : null}
      <div className="sales-finance-scroll">
        {sales ? (
          <section id="dashboard-panel-penjualan" role="tabpanel" aria-labelledby="dashboard-tab-penjualan" hidden={active !== "penjualan"} tabIndex={0} aria-label="Penjualan" data-testid="section-penjualan">
            <SalesDashboard embedded managed={managed} />
          </section>
        ) : null}
        {finance ? (
          <section id="dashboard-panel-keuangan" role="tabpanel" aria-labelledby="dashboard-tab-keuangan" hidden={active !== "keuangan"} tabIndex={0} aria-label="Keuangan" data-testid="section-keuangan">
            <FinancialDashboard
              embedded
              external={{
                payload: financePayload,
                loading: financeLoading,
                error: financeError,
              }}
            />
          </section>
        ) : null}
      </div>
    </div>
  );
}
