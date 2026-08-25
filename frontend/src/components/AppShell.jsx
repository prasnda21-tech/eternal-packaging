import { BarChart3, Boxes, ChevronDown, Download, FileBarChart, LayoutDashboard, LogOut, Package, Settings as SettingsIcon, ShoppingBag, ShoppingCart, Table as TableIcon, Truck, Warehouse } from "lucide-react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import { NAV_KEYS, inventoryNavVisible, purchasingNavVisible, reportsNavVisible, salesNavVisible } from "../lib/navAccess";
import BrandMark from "./BrandMark";

const navClass = ({ isActive }) => "top-nav-link" + (isActive ? " active" : "");

export default function AppShell() {
  const { user, hasPerm, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const importActive = location.pathname.startsWith("/admin/import");
  const reportsActive = location.pathname.startsWith("/laporan");
  const inventoryActive = location.pathname.startsWith("/persediaan");
  const purchasingActive = location.pathname.startsWith("/pembelian");
  const [salesOpen, setSalesOpen] = useState(true);
  const [reportsOpen, setReportsOpen] = useState(true);
  const [inventoryOpen, setInventoryOpen] = useState(true);
  const [purchasingOpen, setPurchasingOpen] = useState(true);
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

  useEffect(() => {
    if (inventoryActive) setInventoryOpen(true);
  }, [inventoryActive]);

  useEffect(() => {
    if (purchasingActive) setPurchasingOpen(true);
  }, [purchasingActive]);

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
  const inventoryNav = inventoryNavVisible(hasPerm);
  const purchasingNav = purchasingNavVisible(hasPerm);
  const showDashboard = hasPerm(NAV_KEYS.dashboard);
  const showSchedule = hasPerm(NAV_KEYS.schedule);
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
              <TableIcon size={16} /> Jadwal Produksi
            </NavLink>
          )}
          {purchasingNav.parent && (
            <div className="nav-group">
              <button
                type="button"
                className={"top-nav-link nav-group-toggle" + (purchasingActive ? " parent-open" : "")}
                aria-expanded={purchasingOpen}
                onClick={() => setPurchasingOpen((open) => !open)}
                data-testid="top-nav-pembelian"
              >
                <ShoppingBag size={16} /> Pembelian
                <ChevronDown size={14} className="nav-chevron" />
              </button>
              {purchasingOpen && purchasingNav.receipts && (
                <div className="nav-sub">
                  <NavLink to="/pembelian/penerimaan-barang" className={navClass} data-testid="top-nav-penerimaan-barang">
                    <Package size={16} /> Penerimaan Barang
                  </NavLink>
                </div>
              )}
            </div>
          )}
          {inventoryNav.parent && (
            <div className="nav-group">
              <button
                type="button"
                className={"top-nav-link nav-group-toggle" + (inventoryActive ? " parent-open" : "")}
                aria-expanded={inventoryOpen}
                onClick={() => setInventoryOpen((open) => !open)}
                data-testid="top-nav-persediaan"
              >
                <Warehouse size={16} /> Persediaan
                <ChevronDown size={14} className="nav-chevron" />
              </button>
              {inventoryOpen && (
                <div className="nav-sub">
                  {inventoryNav.masterItems && (
                    <NavLink to="/persediaan/master-barang-jasa" className={navClass} data-testid="top-nav-master-barang-jasa">
                      <Package size={16} /> Master Barang/Jasa
                    </NavLink>
                  )}
                  {inventoryNav.currentStock && (
                    <NavLink to="/persediaan/stock-saat-ini" className={navClass} data-testid="top-nav-stock-saat-ini">
                      <Boxes size={16} /> Stock Saat Ini
                    </NavLink>
                  )}
                </div>
              )}
            </div>
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
              {reportsOpen && (
                <div className="nav-sub">
                  {reportsNav.analytics && (
                    <NavLink to="/laporan/analitik-output" className={navClass} data-testid="top-nav-analitik-output">
                      <FileBarChart size={16} /> Analitik Output
                    </NavLink>
                  )}
                  {reportsNav.shipmentStatus && (
                    <NavLink to="/laporan/status-pengiriman" className={navClass} data-testid="top-nav-status-pengiriman">
                      <Truck size={16} /> Status Pengiriman
                    </NavLink>
                  )}
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
