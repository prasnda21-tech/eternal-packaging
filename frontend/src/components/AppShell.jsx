import { BarChart3, Boxes, ChevronDown, Download, FileBarChart, LayoutDashboard, LogOut, Package, Settings as SettingsIcon, ShoppingBag, ShoppingCart, Table as TableIcon, Truck, Warehouse } from "lucide-react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { forwardRef, useEffect, useLayoutEffect, useRef, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import { NAV_KEYS, inventoryNavVisible, productionNavVisible, purchasingNavVisible, reportsNavVisible, salesNavVisible, TASK_NAV_LABELS, TASK_NAV_ORDER, TASK_NAV_PATHS } from "../lib/navAccess";
import { nextSidebarMenu, placeSidebarFlyout } from "../lib/sidebarFlyout";
import BrandMark from "./BrandMark";

const navClass = ({ isActive }) => "top-nav-link" + (isActive ? " active" : "");
const railLinkClass = ({ isActive }) => "rail-btn" + (isActive ? " is-active" : "");

const RailButton = forwardRef(function RailButton({ testid, label, icon: Icon, expanded, controls, active, open, onClick }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className={"rail-btn" + (active ? " is-active" : "") + (open ? " is-open" : "")}
      aria-expanded={expanded}
      aria-controls={controls}
      aria-label={label}
      title={label}
      onClick={onClick}
      data-testid={testid}
    >
      <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
      <span className="rail-tooltip">{label}</span>
    </button>
  );
});

function RailNavLink({ to, testid, label, icon: Icon, end, onNavigate }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={railLinkClass}
      aria-label={label}
      title={label}
      data-testid={testid}
      onClick={onNavigate}
    >
      <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
      <span className="rail-tooltip">{label}</span>
    </NavLink>
  );
}

export default function AppShell() {
  const { user, hasPerm, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const importActive = location.pathname.startsWith("/admin/import");
  const reportsActive = location.pathname.startsWith("/laporan");
  const inventoryActive = location.pathname.startsWith("/persediaan");
  const purchasingActive = location.pathname.startsWith("/pembelian");
  const tasksActive = location.pathname.startsWith("/tasks");
  const produksiActive = location.pathname.startsWith("/produksi") || tasksActive;
  const [activeSidebarMenu, setActiveSidebarMenu] = useState(null);
  const [tasksOpen, setTasksOpen] = useState(true);
  // Go-Live: indikator koneksi backend/MongoDB berbasis health probe. TIDAK
  // menampilkan "Terhubung" kalau probe gagal.
  const [health, setHealth] = useState({ ok: null, checkedAt: null });
  const [flyoutPos, setFlyoutPos] = useState({ left: 64, top: 8, scrimLeft: 64, scrimTop: 0, placement: "right" });
  const sidebarRef = useRef(null);
  const flyoutRef = useRef(null);
  const railRefs = useRef({});

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
    setActiveSidebarMenu(null);
  }, [location.pathname]);

  useEffect(() => {
    if (!activeSidebarMenu) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setActiveSidebarMenu(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeSidebarMenu]);

  useLayoutEffect(() => {
    if (!activeSidebarMenu) return undefined;
    const update = () => {
      const rail = sidebarRef.current;
      const anchor = railRefs.current[activeSidebarMenu];
      const panel = flyoutRef.current;
      if (!rail || !anchor) return;
      setFlyoutPos(placeSidebarFlyout(
        rail.getBoundingClientRect(),
        anchor.getBoundingClientRect(),
        { width: panel?.offsetWidth || 260, height: panel?.offsetHeight || 0 },
        { width: window.innerWidth, height: window.innerHeight },
      ));
    };
    update();
    const frame = window.requestAnimationFrame(update);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [activeSidebarMenu, tasksOpen]);

  const doLogout = async () => {
    await logout();
    navigate("/login");
  };

  const toggleMenu = (key) => {
    setActiveSidebarMenu((cur) => nextSidebarMenu(cur, key));
  };

  const closeFlyout = () => setActiveSidebarMenu(null);

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
  const productionNav = productionNavVisible(hasPerm);
  const tasksNav = productionNav.tasks;
  const showDashboard = hasPerm(NAV_KEYS.dashboard);
  const showSettings = hasPerm(NAV_KEYS.settings);

  const flyoutOpen = !!activeSidebarMenu;
  const produksiOpen = activeSidebarMenu === "produksi";
  const salesOpen = activeSidebarMenu === "sales";
  const purchasingOpen = activeSidebarMenu === "purchasing";
  const inventoryOpen = activeSidebarMenu === "inventory";
  const reportsOpen = activeSidebarMenu === "reports";

  return (
    <div className="app-shell">
      <div className="app-sidebar-slot">
      <aside className="app-sidebar" ref={sidebarRef}>
        <Link to="/" className="brand" title="Eternal Packaging" onClick={closeFlyout}>
          <BrandMark compact onDark />
        </Link>
        <nav className="app-sidebar-nav" aria-label="Menu utama">
          {showDashboard && (
            <RailNavLink
              to="/dashboard"
              end
              testid="top-nav-dashboard"
              label="Dashboard"
              icon={LayoutDashboard}
              onNavigate={closeFlyout}
            />
          )}
          {productionNav.parent && (
            <div className="nav-group" data-testid="top-nav-produksi-group">
              <RailButton
                ref={(el) => { railRefs.current.produksi = el; }}
                testid="top-nav-produksi"
                label="Produksi"
                icon={TableIcon}
                expanded={produksiOpen}
                controls="sidebar-flyout-produksi"
                active={produksiActive}
                open={produksiOpen}
                onClick={() => toggleMenu("produksi")}
              />
            </div>
          )}
          {salesNav.parent && (
            <div className="nav-group">
              <RailButton
                ref={(el) => { railRefs.current.sales = el; }}
                testid="top-nav-sales"
                label="Penjualan"
                icon={ShoppingCart}
                expanded={salesOpen}
                controls="sidebar-flyout-sales"
                active={importActive}
                open={salesOpen}
                onClick={() => toggleMenu("sales")}
              />
            </div>
          )}
          {purchasingNav.parent && (
            <div className="nav-group">
              <RailButton
                ref={(el) => { railRefs.current.purchasing = el; }}
                testid="top-nav-pembelian"
                label="Pembelian"
                icon={ShoppingBag}
                expanded={purchasingOpen}
                controls="sidebar-flyout-purchasing"
                active={purchasingActive}
                open={purchasingOpen}
                onClick={() => toggleMenu("purchasing")}
              />
            </div>
          )}
          {inventoryNav.parent && (
            <div className="nav-group">
              <RailButton
                ref={(el) => { railRefs.current.inventory = el; }}
                testid="top-nav-persediaan"
                label="Persediaan"
                icon={Warehouse}
                expanded={inventoryOpen}
                controls="sidebar-flyout-inventory"
                active={inventoryActive}
                open={inventoryOpen}
                onClick={() => toggleMenu("inventory")}
              />
            </div>
          )}
          {reportsNav.parent && (
            <div className="nav-group">
              <RailButton
                ref={(el) => { railRefs.current.reports = el; }}
                testid="top-nav-laporan"
                label="Laporan"
                icon={BarChart3}
                expanded={reportsOpen}
                controls="sidebar-flyout-reports"
                active={reportsActive}
                open={reportsOpen}
                onClick={() => toggleMenu("reports")}
              />
            </div>
          )}
          {showSettings && (
            <RailNavLink
              to="/settings"
              testid="top-nav-settings"
              label="Pengaturan"
              icon={SettingsIcon}
              onNavigate={closeFlyout}
            />
          )}
        </nav>
      </aside>

      {produksiOpen && (
        <div
          ref={flyoutRef}
          className="sidebar-flyout"
          id="sidebar-flyout-produksi"
          data-testid="sidebar-flyout-produksi"
          data-placement={flyoutPos.placement}
          role="dialog"
          aria-label="Produksi"
          style={{ left: flyoutPos.left, top: flyoutPos.top }}
        >
          <header className="sidebar-flyout-head"><h2>Produksi</h2></header>
          <div className="nav-sub">
            {productionNav.schedule && (
              <NavLink to="/produksi" end className={navClass} data-testid="top-nav-jadwal-produksi" onClick={closeFlyout}>
                Jadwal Produksi
              </NavLink>
            )}
            {productionNav.deliverySchedule && (
              <NavLink to="/produksi/jadwal-pengiriman" className={navClass} data-testid="top-nav-jadwal-pengiriman" onClick={closeFlyout}>
                Jadwal Pengiriman
              </NavLink>
            )}
            {productionNav.deliveryReschedule && (
              <NavLink to="/produksi/jadwal-pengiriman-ulang" className={navClass} data-testid="top-nav-jadwal-pengiriman-ulang" onClick={closeFlyout}>
                Jadwal Pengiriman Ulang
              </NavLink>
            )}
            {tasksNav.parent && (
              <div className="nav-nested" data-testid="top-nav-tasks-group">
                <button
                  type="button"
                  className={"top-nav-link nav-group-toggle" + (tasksActive ? " parent-open" : "")}
                  aria-expanded={tasksOpen}
                  onClick={() => setTasksOpen((open) => !open)}
                  data-testid="top-nav-tasks"
                >
                  Tugas Produksi
                  <ChevronDown size={14} className="nav-chevron" />
                </button>
                {tasksOpen && (
                  <div className="nav-sub-sub">
                    {TASK_NAV_ORDER.map((id) => (
                      tasksNav[id] ? (
                        <NavLink key={id} to={TASK_NAV_PATHS[id]} className={navClass} data-testid={`top-nav-tasks-${id}`} onClick={closeFlyout}>
                          {TASK_NAV_LABELS[id]}
                        </NavLink>
                      ) : null
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {salesOpen && salesNav.importPo && (
        <div
          ref={flyoutRef}
          className="sidebar-flyout"
          id="sidebar-flyout-sales"
          data-testid="sidebar-flyout-sales"
          data-placement={flyoutPos.placement}
          role="dialog"
          aria-label="Penjualan"
          style={{ left: flyoutPos.left, top: flyoutPos.top }}
        >
          <header className="sidebar-flyout-head"><h2>Penjualan</h2></header>
          <div className="nav-sub">
            <NavLink to="/admin/import" className={navClass} data-testid="top-nav-import" onClick={closeFlyout}>
              <Download size={16} /> Import PO
            </NavLink>
          </div>
        </div>
      )}

      {purchasingOpen && purchasingNav.receipts && (
        <div
          ref={flyoutRef}
          className="sidebar-flyout"
          id="sidebar-flyout-purchasing"
          data-testid="sidebar-flyout-purchasing"
          data-placement={flyoutPos.placement}
          role="dialog"
          aria-label="Pembelian"
          style={{ left: flyoutPos.left, top: flyoutPos.top }}
        >
          <header className="sidebar-flyout-head"><h2>Pembelian</h2></header>
          <div className="nav-sub">
            <NavLink to="/pembelian/penerimaan-barang" className={navClass} data-testid="top-nav-penerimaan-barang" onClick={closeFlyout}>
              <Package size={16} /> Penerimaan Barang
            </NavLink>
          </div>
        </div>
      )}

      {inventoryOpen && (
        <div
          ref={flyoutRef}
          className="sidebar-flyout"
          id="sidebar-flyout-inventory"
          data-testid="sidebar-flyout-inventory"
          data-placement={flyoutPos.placement}
          role="dialog"
          aria-label="Persediaan"
          style={{ left: flyoutPos.left, top: flyoutPos.top }}
        >
          <header className="sidebar-flyout-head"><h2>Persediaan</h2></header>
          <div className="nav-sub">
            {inventoryNav.masterItems && (
              <NavLink to="/persediaan/master-barang-jasa" className={navClass} data-testid="top-nav-master-barang-jasa" onClick={closeFlyout}>
                <Package size={16} /> Barang/Jasa
              </NavLink>
            )}
            {inventoryNav.currentStock && (
              <NavLink to="/persediaan/stock-saat-ini" className={navClass} data-testid="top-nav-stock-saat-ini" onClick={closeFlyout}>
                <Boxes size={16} /> Stock Saat Ini
              </NavLink>
            )}
          </div>
        </div>
      )}

      {reportsOpen && (
        <div
          ref={flyoutRef}
          className="sidebar-flyout"
          id="sidebar-flyout-reports"
          data-testid="sidebar-flyout-reports"
          data-placement={flyoutPos.placement}
          role="dialog"
          aria-label="Laporan"
          style={{ left: flyoutPos.left, top: flyoutPos.top }}
        >
          <header className="sidebar-flyout-head"><h2>Laporan</h2></header>
          <div className="nav-sub">
            {reportsNav.analytics && (
              <NavLink to="/laporan/analitik-output" className={navClass} data-testid="top-nav-analitik-output" onClick={closeFlyout}>
                <FileBarChart size={16} /> Analitik Output
              </NavLink>
            )}
            {reportsNav.statusPo && (
              <NavLink to="/laporan/status-po" className={navClass} data-testid="top-nav-status-po" onClick={closeFlyout}>
                <TableIcon size={16} /> Status PO
              </NavLink>
            )}
            {reportsNav.shipmentStatus && (
              <NavLink to="/laporan/status-pengiriman" className={navClass} data-testid="top-nav-status-pengiriman" onClick={closeFlyout}>
                <Truck size={16} /> Status Pengiriman
              </NavLink>
            )}
            {reportsNav.gajiOperator && (
              <NavLink to="/laporan/gaji-operator" className={navClass} data-testid="top-nav-gaji-operator" onClick={closeFlyout}>
                <FileBarChart size={16} /> Gaji Operator
              </NavLink>
            )}
          </div>
        </div>
      )}
      </div>

      <div className="app-main">
        {flyoutOpen && (
          <button
            type="button"
            className="sidebar-flyout-scrim"
            aria-label="Tutup menu"
            data-testid="sidebar-flyout-scrim"
            style={{ left: flyoutPos.scrimLeft, top: flyoutPos.scrimTop }}
            onClick={closeFlyout}
          />
        )}
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
