import { BarChart3, ChevronDown, Download, FileBarChart, LayoutDashboard, LogOut, Settings as SettingsIcon, ShoppingBag, ShoppingCart, Table, Warehouse } from "lucide-react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import { NAV_KEYS, reportsNavVisible, salesNavVisible } from "../lib/navAccess";
import BrandMark from "./BrandMark";

const navClass = ({ isActive }) => "top-nav-link" + (isActive ? " active" : "");

export default function AppShell() {
  const { user, hasPerm, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const importActive = location.pathname.startsWith("/admin/import");
  const reportsActive = location.pathname.startsWith("/laporan");
  const [salesOpen, setSalesOpen] = useState(true);
  const [reportsOpen, setReportsOpen] = useState(true);
  // Go-Live: indikator koneksi backend/MongoDB berbasis health probe. TIDAK
  // menampilkan "Terhubung" kalau probe gagal.
  const [health, setHealth] = useState({ ok: null, checkedAt: null });

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const r = await api.systemHealth();
        if (!cancelled) setHealth({ ok: !!r?.mongo, checkedAt: r?.checked_at || null });
      } catch (_) {
        if (!cancelled) setHealth({ ok: false, checkedAt: null });
      }
    };
    check();
    const t = setInterval(check, 30000); // Reuse existing polling cadence (30s)
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  useEffect(() => {
    if (importActive) setSalesOpen(true);
  }, [importActive]);

  useEffect(() => {
    if (reportsActive) setReportsOpen(true);
  }, [reportsActive]);

  const doLogout = async () => {
    await logout();
    navigate("/login");
  };

  const healthLabel = health.ok === true
    ? "Operasional · MongoDB Terhubung"
    : health.ok === false
      ? "Backend tidak terhubung"
      : "Mengecek koneksi…";
  const healthDotStyle = health.ok === false
    ? { background: "#c9302c" }
    : health.ok === true
      ? undefined
      : { background: "#c9a227" };

  const todayLabel = new Date().toLocaleDateString("id-ID", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  const salesNav = salesNavVisible(hasPerm);
  const reportsNav = reportsNavVisible(hasPerm);
  const showDashboard = hasPerm(NAV_KEYS.dashboard);
  const showSchedule = hasPerm(NAV_KEYS.schedule);
  const showPurchasing = hasPerm(NAV_KEYS.purchasing);
  const showInventory = hasPerm(NAV_KEYS.inventory);
  const showSettings = hasPerm(NAV_KEYS.settings);

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <Link to="/" className="brand">
          <BrandMark compact onDark />
        </Link>
        <nav className="app-sidebar-nav">
          {showDashboard && (
            <NavLink to="/dashboard" end className={navClass} data-testid="top-nav-dashboard">
              <LayoutDashboard size={16} /> Dashboard
            </NavLink>
          )}
          {salesNav.parent && (
            <div className="nav-group">
              <button
                type="button"
                className={"top-nav-link nav-group-toggle" + (importActive ? " parent-open" : "")}
                aria-expanded={salesOpen}
                onClick={() => setSalesOpen((open) => !open)}
                data-testid="top-nav-sales"
              >
                <ShoppingCart size={16} /> Penjualan
                <ChevronDown size={14} className="nav-chevron" />
              </button>
              {salesOpen && salesNav.importPo && (
                <div className="nav-sub">
                  <NavLink to="/admin/import" className={navClass} data-testid="top-nav-import">
                    <Download size={16} /> Import PO
                  </NavLink>
                </div>
              )}
            </div>
          )}
          {showSchedule && (
            <NavLink to="/produksi" end className={navClass} data-testid="top-nav-produksi">
              <Table size={16} /> Jadwal Produksi
            </NavLink>
          )}
          {showPurchasing && (
            <NavLink to="/pembelian" className={navClass} data-testid="top-nav-pembelian">
              <ShoppingBag size={16} /> Pembelian
            </NavLink>
          )}
          {showInventory && (
            <NavLink to="/persediaan" className={navClass} data-testid="top-nav-persediaan">
              <Warehouse size={16} /> Persediaan
            </NavLink>
          )}
          {reportsNav.parent && (
            <div className="nav-group">
              <button
                type="button"
                className={"top-nav-link nav-group-toggle" + (reportsActive ? " parent-open" : "")}
                aria-expanded={reportsOpen}
                onClick={() => setReportsOpen((open) => !open)}
                data-testid="top-nav-laporan"
              >
                <BarChart3 size={16} /> Laporan
                <ChevronDown size={14} className="nav-chevron" />
              </button>
              {reportsOpen && reportsNav.analytics && (
                <div className="nav-sub">
                  <NavLink to="/laporan/analitik-output" className={navClass} data-testid="top-nav-analitik-output">
                    <FileBarChart size={16} /> Analitik Output
                  </NavLink>
                </div>
              )}
            </div>
          )}
          {showSettings && (
            <NavLink to="/settings" className={navClass} data-testid="top-nav-settings">
              <SettingsIcon size={16} /> Pengaturan
            </NavLink>
          )}
        </nav>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <span className="topbar-date">{todayLabel}</span>
          <div className="sync" data-testid="sync-status" title={health.checkedAt ? `Cek terakhir: ${health.checkedAt}` : undefined}>
            <span className="live-dot" style={healthDotStyle} /> {healthLabel}
          </div>
          <div className="profile">
            <div className="profile-meta">
              <strong className="profile-name">{user?.name || user?.email}</strong>
              <span data-testid="current-user-role">{ROLES[user?.role] || user?.role}</span>
            </div>
            <button onClick={doLogout} aria-label="Keluar" data-testid="logout-button">
              <LogOut size={16} />
            </button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
