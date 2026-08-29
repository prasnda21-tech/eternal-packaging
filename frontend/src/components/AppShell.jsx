import { BarChart3, Boxes, CalendarClock, ClipboardList, Download, FileBarChart, FileText, Image as ImageIcon, LayoutDashboard, LineChart, LogOut, Package, RotateCcw, ScanLine, Settings as SettingsIcon, ShoppingBag, ShoppingCart, Table as TableIcon, Truck, Users, Warehouse } from "lucide-react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { forwardRef, useEffect, useLayoutEffect, useRef, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import { NAV_KEYS, inventoryMenuItems, inventoryNavVisible, productionMenuItems, productionNavVisible, purchasingMenuItems, purchasingNavVisible, reportsMenuItems, reportsNavVisible, salesMenuItems, salesNavVisible, taskMenuItems } from "../lib/navAccess";
import { nextSidebarMenu, placeSidebarFlyout } from "../lib/sidebarFlyout";
import BrandMark from "./BrandMark";
import SidebarFlyoutGrid from "./SidebarFlyoutGrid";

const railLinkClass = ({ isActive }) => "rail-btn" + (isActive ? " is-active" : "");

const TILE_ICONS = {
  [NAV_KEYS.importPo]: Download,
  [NAV_KEYS.purchaseReceipts]: Package,
  [NAV_KEYS.masterItems]: Package,
  [NAV_KEYS.currentStock]: Boxes,
  [NAV_KEYS.analytics]: FileBarChart,
  [NAV_KEYS.statusPo]: TableIcon,
  [NAV_KEYS.shipmentStatus]: Truck,
  [NAV_KEYS.gajiOperator]: FileBarChart,
  [NAV_KEYS.orderPrediction]: LineChart,
  [NAV_KEYS.schedule]: TableIcon,
  deliveryToday: ClipboardList,
  [NAV_KEYS.deliverySchedule]: Truck,
  [NAV_KEYS.deliveryReschedule]: RotateCcw,
  designer: ImageIcon,
  screener: ScanLine,
  kepala_produksi: CalendarClock,
  helper: Users,
  operator: Users,
  packing: Package,
  surat_jalan: FileText,
  loading: Boxes,
  terkirim: Truck,
};

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
  }, [activeSidebarMenu]);

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
  const salesItems = salesMenuItems(hasPerm);
  const reportsNav = reportsNavVisible(hasPerm);
  const reportsItems = reportsMenuItems(hasPerm);
  const inventoryNav = inventoryNavVisible(hasPerm);
  const inventoryItems = inventoryMenuItems(hasPerm);
  const purchasingNav = purchasingNavVisible(hasPerm);
  const purchasingItems = purchasingMenuItems(hasPerm);
  const productionNav = productionNavVisible(hasPerm);
  const productionItems = productionMenuItems(hasPerm);
  const taskItems = taskMenuItems(hasPerm);
  void productionNav.deliveryToday;
  const showDashboard = hasPerm(NAV_KEYS.dashboard);
  const showSettings = hasPerm(NAV_KEYS.settings);

  const flyoutOpen = !!activeSidebarMenu;
  const produksiOpen = activeSidebarMenu === "produksi";
  const salesOpen = activeSidebarMenu === "sales";
  const purchasingOpen = activeSidebarMenu === "purchasing";
  const inventoryOpen = activeSidebarMenu === "inventory";
  const reportsOpen = activeSidebarMenu === "reports";
  const flyoutStyle = { left: flyoutPos.left, top: flyoutPos.top };

  return (
    <div className="app-shell">
      <div className="app-sidebar-slot">
      <aside className="app-sidebar" ref={sidebarRef}>
        <Link to="/" className="brand" title="Eternal Packaging" data-testid="sidebar-brand" onClick={closeFlyout}>
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

      {produksiOpen && (productionItems.length + taskItems.length) > 0 && (
        <SidebarFlyoutGrid
          innerRef={flyoutRef}
          id="sidebar-flyout-produksi"
          testid="sidebar-flyout-produksi"
          title="Produksi"
          placement={flyoutPos.placement}
          style={flyoutStyle}
          icons={TILE_ICONS}
          onNavigate={closeFlyout}
          sections={[
            { ariaLabel: "Menu Produksi", items: productionItems },
            {
              heading: "Tugas Produksi",
              headingTestid: "top-nav-tasks-group",
              ariaLabel: "Tugas Produksi",
              items: taskItems,
            },
          ]}
        />
      )}
      {/* Pengiriman Hari Ini + TASK_NAV_ORDER.map stay in productionMenuItems / taskMenuItems */}

      {salesOpen && salesItems.length > 0 && (
        <SidebarFlyoutGrid
          innerRef={flyoutRef}
          id="sidebar-flyout-sales"
          testid="sidebar-flyout-sales"
          title="Penjualan"
          ariaLabel="Menu Penjualan"
          placement={flyoutPos.placement}
          style={flyoutStyle}
          items={salesItems}
          icons={TILE_ICONS}
          onNavigate={closeFlyout}
        />
      )}

      {purchasingOpen && purchasingItems.length > 0 && (
        <SidebarFlyoutGrid
          innerRef={flyoutRef}
          id="sidebar-flyout-purchasing"
          testid="sidebar-flyout-purchasing"
          title="Pembelian"
          ariaLabel="Menu Pembelian"
          placement={flyoutPos.placement}
          style={flyoutStyle}
          items={purchasingItems}
          icons={TILE_ICONS}
          onNavigate={closeFlyout}
        />
      )}

      {inventoryOpen && inventoryItems.length > 0 && (
        <SidebarFlyoutGrid
          innerRef={flyoutRef}
          id="sidebar-flyout-inventory"
          testid="sidebar-flyout-inventory"
          title="Persediaan"
          ariaLabel="Menu Persediaan"
          placement={flyoutPos.placement}
          style={flyoutStyle}
          items={inventoryItems}
          icons={TILE_ICONS}
          onNavigate={closeFlyout}
        />
      )}

      {reportsOpen && reportsItems.length > 0 && (
        <SidebarFlyoutGrid
          innerRef={flyoutRef}
          id="sidebar-flyout-reports"
          testid="sidebar-flyout-reports"
          title="Laporan"
          ariaLabel="Menu Laporan"
          placement={flyoutPos.placement}
          style={flyoutStyle}
          items={reportsItems}
          icons={TILE_ICONS}
          onNavigate={closeFlyout}
        />
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
