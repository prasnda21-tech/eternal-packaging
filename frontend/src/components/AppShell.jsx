import { BarChart3, BookOpen, Boxes, CalendarClock, ChevronLeft, ChevronRight, ClipboardCheck, ClipboardList, ClipboardPlus, Coins, Contact, CreditCard, Download, FileBarChart, FileText, FolderOpen, IdCard, Image as ImageIcon, Landmark, LayoutDashboard, LineChart, ListOrdered, LogOut, MessageCircle, Package, PanelLeft, PanelLeftClose, PanelLeftOpen, PlusCircle, Receipt, RotateCcw, Route, Scale, ScanLine, ScrollText, Search, Settings as SettingsIcon, Shield, ShoppingBag, ShoppingCart, Table as TableIcon, Tags, Target, TrendingUp, Truck, Users, Wallet, Warehouse, ArrowLeftRight } from "lucide-react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Suspense, forwardRef, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import { canAccessRoutePage } from "../lib/independentRoute";
import {
  isMenuSearchOpenShortcut,
  isMenuSearchShortcutBlockedTarget,
  menuSearchItemsFromRails,
} from "../lib/menuSearch";
import { NAV_KEYS, TASK_NAV_ORDER, cashBankMenuItems, cashBankNavVisible, dashboardMenuItems, dashboardNavVisible, employeeAdminMenuItems, employeeAdminNavVisible, hrPayrollMenuItems, hrPayrollNavVisible, inventoryMenuItems, inventoryNavVisible, ledgerMenuItems, ledgerNavVisible, productionMenuItems, productionNavVisible, purchasingMenuItems, purchasingNavVisible, reportsMenuItems, reportsNavVisible, salesMenuItems, salesNavVisible, settingsMenuItems, taskMenuItems } from "../lib/navAccess";
import { nextSidebarMenu, placeSidebarFlyout } from "../lib/sidebarFlyout";
import {
  SIDEBAR_MODE_COLLAPSED,
  SIDEBAR_MODE_EXPANDED,
  defaultSidebarModeForWidth,
  resolveSidebarMode,
  sidebarRailWidth,
  toggleSidebarMode,
  writeSidebarMode,
} from "../lib/sidebarMode";
import { buildDesktopSidebar, groupedRailForPath, isDeliveryRailActive, isFinanceRailActive, isOperationalReportRailActive, isProductionRailActive, isSalesRailActive, isSalesReportPath, nextChevronMenu, railLabel } from "../lib/sidebarPresentation";
import { canSeeSpkNav } from "../lib/spk";
import { moduleFromPath } from "../lib/uiModule";
import { mergeWorkspaceTabsPreview } from "../lib/workspaceTabsPreview";
import BrandMark from "./BrandMark";
import FloatingNotificationBell from "./FloatingNotificationBell";
import GlobalMenuSearch from "./GlobalMenuSearch";
import SidebarFlyoutGrid from "./SidebarFlyoutGrid";
import WorkspaceTabs from "./WorkspaceTabs";
import WorkspaceChunkFallback from "./WorkspaceChunkFallback";
import { useGlobalWorkspaceTabs } from "../hooks/useGlobalWorkspaceTabs";

const railLinkClass = ({ isActive }) => "rail-btn" + (isActive ? " is-active" : "");

const RAIL_LABELS = {
  dashboard: "Dashboard",
  sales: "Penjualan",
  produksi: "Produksi",
  pengiriman: "Pengiriman",
  purchasing: "Pembelian",
  inventory: "Persediaan",
  keuangan: "Keuangan",
  employeeAdmin: "Administrasi Karyawan",
  hrPayroll: "HR & Payroll",
  reports: "Laporan",
  settings: "Pengaturan",
};

const RAIL_ICONS = {
  dashboard: LayoutDashboard,
  spk: ClipboardPlus,
  chat: MessageCircle,
  sales: ShoppingCart,
  produksi: TableIcon,
  pengiriman: Truck,
  purchasing: ShoppingBag,
  inventory: Warehouse,
  keuangan: Landmark,
  employeeAdmin: IdCard,
  hrPayroll: Wallet,
  reports: BarChart3,
  settings: SettingsIcon,
};

const TILE_ICONS = {
  [NAV_KEYS.dashboard]: LayoutDashboard,
  [NAV_KEYS.dashboardSales]: LineChart,
  [NAV_KEYS.dashboardFinance]: Landmark,
  [NAV_KEYS.salesCustomers]: Users,
  [NAV_KEYS.productRequests]: ClipboardList,
  [NAV_KEYS.salesOrder]: ShoppingCart,
  [NAV_KEYS.salesDeliveryOrder]: Truck,
  [NAV_KEYS.salesInvoice]: FileText,
  [NAV_KEYS.salesReceipt]: Receipt,
  [NAV_KEYS.importPo]: Download,
  [NAV_KEYS.salesActivity]: Contact,
  [NAV_KEYS.purchaseRequests]: ClipboardList,
  [NAV_KEYS.purchaseOrders]: ShoppingBag,
  [NAV_KEYS.purchaseAdvances]: Wallet,
  [NAV_KEYS.purchaseInvoices]: FileText,
  [NAV_KEYS.purchasePayments]: CreditCard,
  [NAV_KEYS.purchaseReceipts]: Package,
  [NAV_KEYS.purchaseReturns]: RotateCcw,
  [NAV_KEYS.purchaseHpp]: Coins,
  [NAV_KEYS.masterItems]: Package,
  [NAV_KEYS.currentStock]: Boxes,
  [NAV_KEYS.inventoryValue]: Coins,
  [NAV_KEYS.analytics]: FileBarChart,
  [NAV_KEYS.statusPo]: TableIcon,
  [NAV_KEYS.shipmentStatus]: Truck,
  [NAV_KEYS.gajiOperator]: FileBarChart,
  [NAV_KEYS.orderPrediction]: LineChart,
  [NAV_KEYS.employeeAdminMine]: ClipboardList,
  [NAV_KEYS.employeeAdminCreate]: PlusCircle,
  [NAV_KEYS.employeeAdminApprove]: Users,
  [NAV_KEYS.employeeAdminArchive]: FolderOpen,
  [NAV_KEYS.hrPayrollEmployees]: Users,
  [NAV_KEYS.hrPayrollEmployment]: IdCard,
  [NAV_KEYS.hrPayrollPayroll]: Wallet,
  [NAV_KEYS.hrPayrollPayslips]: Receipt,
  [NAV_KEYS.hrPayrollComponents]: Coins,
  [NAV_KEYS.hrPayrollHistory]: FileBarChart,
  [NAV_KEYS.ledgerAccounts]: BookOpen,
  [NAV_KEYS.ledgerGeneralJournal]: FileText,
  [NAV_KEYS.ledgerOpeningBalance]: Scale,
  [NAV_KEYS.ledgerGeneralLedger]: ListOrdered,
  [NAV_KEYS.ledgerAccountsReceivable]: Wallet,
  [NAV_KEYS.ledgerAccountsPayable]: CreditCard,
  [NAV_KEYS.ledgerTrialBalance]: TableIcon,
  [NAV_KEYS.ledgerProfitLoss]: TrendingUp,
  [NAV_KEYS.ledgerBalanceSheet]: Scale,
  [NAV_KEYS.cashBankAccounts]: Landmark,
  [NAV_KEYS.cashBankReceipts]: Receipt,
  [NAV_KEYS.cashBankPayments]: CreditCard,
  [NAV_KEYS.cashBankTransfers]: ArrowLeftRight,
  [NAV_KEYS.cashBankMutasi]: ListOrdered,
  [NAV_KEYS.cashBankReconciliation]: Scale,
  [NAV_KEYS.cashBankReconOwner]: Scale,
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
  employees: Users,
  roles: Shield,
  "sidebar-access": PanelLeft,
  "operator-wage": Coins,
  hpp: Scale,
  "physical-stock": Package,
  "sales-accounting": BookOpen,
  "purchase-accounting": CreditCard,
  "payroll-accounting": Wallet,
  "pricing-catalog": Tags,
  "customer-approval": ClipboardCheck,
  "delivery-deadline": Truck,
  "operator-target": Target,
  "work-panel": ClipboardList,
  "employee-admin": IdCard,
  "hr-payroll": Wallet,
  "sales-activity": Contact,
  "route-optimizer": Route,
  "accurate-creators": Users,
  "sales-user-mapping": Users,
  "sales-master": Users,
  "crm-daily-report-options": ClipboardList,
  audit: ScrollText,
};

const RailButton = forwardRef(function RailButton({ testid, label, icon: Icon, expanded, controls, active, open, showLabel, onClick }, ref) {
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
      {showLabel ? <span className="rail-label">{label}</span> : null}
      <span className="rail-tooltip">{label}</span>
    </button>
  );
});

function RailNavLink({ to, testid, label, icon: Icon, end, onNavigate, badge, showLabel }) {
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
      {showLabel ? <span className="rail-label">{label}</span> : null}
      {badge ? (
        <span className="rail-unread-badge" data-testid={`${testid}-unread`}>
          {badge > 99 ? "99+" : badge}
        </span>
      ) : null}
      <span className="rail-tooltip">{label}</span>
    </NavLink>
  );
}

export default function AppShell() {
  const { user, hasPerm, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const workspace = useGlobalWorkspaceTabs();
  const workspaceTabs = useMemo(
    () => mergeWorkspaceTabsPreview(workspace.tabs, location.search),
    [workspace.tabs, location.search],
  );
  const workspaceTabsVisible = workspace.visible || workspaceTabs.length > 0;
  const settingsActive = location.pathname.startsWith("/settings");
  const dashboardActive = location.pathname === "/dashboard" || location.pathname.startsWith("/dashboard/");
  const salesReportActive = isSalesReportPath(location.pathname);
  const salesActive = isSalesRailActive(location.pathname) || salesReportActive;
  const reportsActive = isOperationalReportRailActive(location.pathname) && !salesReportActive;
  const inventoryActive = location.pathname.startsWith("/persediaan");
  const purchasingActive = location.pathname.startsWith("/pembelian");
  const ledgerActive = location.pathname.startsWith("/buku-besar");
  const cashBankActive = location.pathname.startsWith("/kas-bank");
  const keuanganActive = isFinanceRailActive(location.pathname) || ledgerActive || cashBankActive;
  const employeeAdminActive = location.pathname.startsWith("/administrasi-karyawan");
  const hrPayrollActive = location.pathname.startsWith("/hr-payroll");
  const produksiActive = isProductionRailActive(location.pathname);
  const pengirimanActive = isDeliveryRailActive(location.pathname);
  const userId = user?.id || user?._id || user?.email || "";
  const [sidebarMode, setSidebarMode] = useState(SIDEBAR_MODE_COLLAPSED);
  const [sidebarUserSet, setSidebarUserSet] = useState(false);
  const sidebarExpanded = sidebarMode === SIDEBAR_MODE_EXPANDED;
  const [activeSidebarMenu, setActiveSidebarMenu] = useState(null);
  const [menuSearchOpen, setMenuSearchOpen] = useState(false);
  const [edgeTop, setEdgeTop] = useState(96);
  // Go-Live: indikator koneksi backend/MongoDB berbasis health probe. TIDAK
  // menampilkan "Terhubung" kalau probe gagal.
  const [health, setHealth] = useState({ ok: null, checkedAt: null });
  const [chatUnread, setChatUnread] = useState(0);
  const [flyoutPos, setFlyoutPos] = useState({ left: 64, top: 8, scrimLeft: 64, scrimTop: 0, placement: "right" });
  const sidebarRef = useRef(null);
  const flyoutRef = useRef(null);
  const railRefs = useRef({});
  const menuSearchTriggerRef = useRef(null);
  const sidebarUserSetRef = useRef(false);

  useEffect(() => {
    sidebarUserSetRef.current = sidebarUserSet;
  }, [sidebarUserSet]);

  useEffect(() => {
    const storage = typeof window !== "undefined" ? window.localStorage : null;
    const width = typeof window !== "undefined" ? window.innerWidth : 1024;
    const resolved = resolveSidebarMode({
      storage,
      userId,
      viewportWidth: width,
    });
    setSidebarMode(resolved.mode);
    setSidebarUserSet(resolved.userSet);
  }, [userId]);

  useEffect(() => {
    const onResize = () => {
      if (sidebarUserSetRef.current) return;
      setSidebarMode(defaultSidebarModeForWidth(window.innerWidth));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const check = async () => {
      try {
        const r = await api.systemHealth();
        if (!cancelled) setHealth({ ok: !!r?.mongo, checkedAt: r?.checked_at || null });
      } catch (_) {
        if (!cancelled) setHealth({ ok: false, checkedAt: null });
      }
    };
    const startPoll = () => {
      if (timer != null) return;
      timer = setInterval(() => {
        if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
        check();
      }, 30000);
    };
    const stopPoll = () => {
      if (timer == null) return;
      clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") {
        check();
        startPoll();
        return;
      }
      stopPoll();
    };
    check();
    if (typeof document === "undefined" || document.visibilityState === "visible") startPoll();
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }
    return () => {
      cancelled = true;
      stopPoll();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const loadUnread = async () => {
      try {
        const r = await api.chatUnreadSummary();
        if (!cancelled) setChatUnread(Number(r?.total_unread || 0));
      } catch (_) {
        if (!cancelled) setChatUnread(0);
      }
    };
    const startPoll = () => {
      if (timer != null) return;
      timer = setInterval(() => {
        if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
        loadUnread();
      }, 30000);
    };
    const stopPoll = () => {
      if (timer == null) return;
      clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") {
        loadUnread();
        startPoll();
        return;
      }
      stopPoll();
    };
    loadUnread();
    if (typeof document === "undefined" || document.visibilityState === "visible") startPoll();
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }
    return () => {
      cancelled = true;
      stopPoll();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, []);

  useEffect(() => {
    // Close on path or query change so same-path leaves (e.g. /laporan?view=saved)
    // cannot leave a flyout/scrim overlay covering content.
    setActiveSidebarMenu(null);
  }, [location.pathname, location.search]);

  useEffect(() => {
    setMenuSearchOpen(false);
  }, [user?.id, user?.email, user?.sub]);

  useEffect(() => {
    const onKey = (e) => {
      if (!isMenuSearchOpenShortcut(e)) return;
      if (menuSearchOpen) {
        e.preventDefault();
        setMenuSearchOpen(false);
        const trigger = menuSearchTriggerRef.current;
        if (trigger && typeof trigger.focus === "function") {
          window.requestAnimationFrame(() => trigger.focus());
        }
        return;
      }
      if (isMenuSearchShortcutBlockedTarget(e.target)) return;
      e.preventDefault();
      setActiveSidebarMenu(null);
      setMenuSearchOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuSearchOpen]);

  useEffect(() => {
    if (!activeSidebarMenu || menuSearchOpen) return undefined;
    const trigger = railRefs.current[activeSidebarMenu];
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      setActiveSidebarMenu(null);
      if (trigger && typeof trigger.focus === "function") trigger.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeSidebarMenu, menuSearchOpen]);

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
  }, [activeSidebarMenu, sidebarMode]);

  const doLogout = async () => {
    await logout();
    navigate("/login");
  };

  const toggleMenu = (key) => {
    setActiveSidebarMenu((cur) => nextSidebarMenu(cur, key));
  };

  const closeFlyout = () => setActiveSidebarMenu(null);

  const toggleSidebarWidth = () => {
    const next = toggleSidebarMode(sidebarMode);
    setSidebarMode(next);
    setSidebarUserSet(true);
    const storage = typeof window !== "undefined" ? window.localStorage : null;
    writeSidebarMode(storage, userId, next);
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
  const salesItems = salesMenuItems(hasPerm);
  const reportsNav = reportsNavVisible(hasPerm);
  const reportsItems = reportsMenuItems(hasPerm);
  const inventoryNav = inventoryNavVisible(hasPerm);
  const inventoryItems = inventoryMenuItems(hasPerm);
  const purchasingNav = purchasingNavVisible(hasPerm);
  const purchasingItems = purchasingMenuItems(hasPerm);
  const productionNav = productionNavVisible(hasPerm);
  const productionItems = productionMenuItems(hasPerm);
  const taskItems = [...taskMenuItems(hasPerm)].sort(
    (a, b) => TASK_NAV_ORDER.indexOf(a.key) - TASK_NAV_ORDER.indexOf(b.key),
  );
  const employeeAdminNav = employeeAdminNavVisible(hasPerm);
  const employeeAdminItems = employeeAdminMenuItems(hasPerm);
  const hrPayrollNav = hrPayrollNavVisible(hasPerm);
  const hrPayrollItems = hrPayrollMenuItems(hasPerm);
  const ledgerNav = ledgerNavVisible(hasPerm);
  const ledgerItems = ledgerMenuItems(hasPerm);
  const cashBankNav = cashBankNavVisible(hasPerm);
  const cashBankItems = cashBankMenuItems(hasPerm);
  const settingsItems = settingsMenuItems(hasPerm);
  void salesNav;
  void reportsNav;
  void inventoryNav;
  void purchasingNav;
  void employeeAdminNav;
  void hrPayrollNav;
  void ledgerNav;
  void cashBankNav;
  void productionNav.deliveryToday;
  const dashboardNav = dashboardNavVisible(hasPerm, user);
  const dashboardItems = dashboardMenuItems(hasPerm, user);
  const showDashboard = (hasPerm(NAV_KEYS.dashboard) || dashboardNav.parent) && dashboardItems.length > 0;
  const showSpk = canSeeSpkNav(hasPerm);
  const showChat = hasPerm(NAV_KEYS.chat) || hasPerm("chat.use");
  const showRouteOptimizer = canAccessRoutePage(hasPerm);
  const showSettings = settingsItems.length > 0;
  const sidebar = buildDesktopSidebar({
    dashboardItems: showDashboard ? dashboardItems : [],
    salesItems,
    productionItems,
    taskItems,
    showRouteOptimizer,
    purchasingItems,
    inventoryItems,
    cashBankItems,
    ledgerItems,
    employeeAdminItems,
    hrPayrollItems,
    reportItems: reportsItems,
    settingsItems: showSettings ? settingsItems : [],
    showSpk,
    showChat,
  }).map((entry) => {
    if (entry.id !== "pengiriman") return entry;
    return {
      ...entry,
      sections: entry.sections.map((section) => ({
        ...section,
        items: section.items.map((item) => (
          item.to === "/optimasi-rute" ? { ...item, testid: "top-nav-optimasi-rute" } : item
        )),
      })),
    };
  });

  const menuSearchItems = useMemo(
    () => menuSearchItemsFromRails(sidebar),
    [sidebar],
  );

  const flyoutOpen = !!activeSidebarMenu;
  const keuanganOpen = activeSidebarMenu === "keuangan";
  const ledgerOpen = keuanganOpen;
  const cashBankOpen = keuanganOpen;
  const showLedgerSection = Boolean(ledgerOpen && ledgerItems.length > 0);
  const showCashSection = Boolean(cashBankOpen && cashBankItems.length > 0);
  const flyoutStyle = { left: flyoutPos.left, top: flyoutPos.top };
  const openEntry = sidebar.find((entry) => entry.kind === "menu" && entry.id === activeSidebarMenu) || null;
  const openSections = !openEntry ? [] : openEntry.id === "keuangan"
    ? openEntry.sections.filter((section) => (
      section.marker === "sidebar-flyout-cash-bank" ? showCashSection : showLedgerSection
    ))
    : openEntry.sections;

  const uiModule = moduleFromPath(location.pathname);
  const railActive = {
    dashboard: dashboardActive,
    produksi: produksiActive,
    pengiriman: pengirimanActive,
    sales: salesActive,
    purchasing: purchasingActive,
    inventory: inventoryActive,
    keuangan: keuanganActive,
    employeeAdmin: employeeAdminActive,
    hrPayroll: hrPayrollActive,
    reports: reportsActive,
    settings: settingsActive,
  };

  const groupedRailId = groupedRailForPath(location.pathname);
  const groupedEntry = sidebar.find((entry) => entry.kind === "menu" && entry.id === groupedRailId) || null;
  const edgeGroupId = groupedEntry ? groupedEntry.id : null;

  useLayoutEffect(() => {
    if (!edgeGroupId) return undefined;
    const update = () => {
      const rail = sidebarRef.current;
      if (!rail) return;
      const openAnchor = activeSidebarMenu ? railRefs.current[activeSidebarMenu] : null;
      const anchor = openAnchor || railRefs.current[edgeGroupId];
      const railBox = rail.getBoundingClientRect();
      const minTop = 72;
      const maxTop = Math.max(minTop, railBox.height - 20);
      let next = 96;
      if (anchor) {
        const box = anchor.getBoundingClientRect();
        next = box.top - railBox.top + (box.height / 2);
      }
      next = Math.min(maxTop, Math.max(minTop, Math.round(next)));
      setEdgeTop((prev) => (prev === next ? prev : next));
    };
    update();
    const nav = sidebarRef.current?.querySelector(".app-sidebar-nav");
    nav?.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      nav?.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [activeSidebarMenu, edgeGroupId, sidebarMode]);

  const toggleChevron = () => {
    setActiveSidebarMenu((cur) => nextChevronMenu(cur, edgeGroupId));
  };

  return (
    <div
      className="app-shell"
      data-module={uiModule}
      data-ui-system="colorful"
      data-sidebar-mode={sidebarMode}
      style={{ "--sidebar-rail": `${sidebarRailWidth(sidebarMode)}px` }}
    >
      <div className="app-sidebar-slot">
      <aside className="app-sidebar" ref={sidebarRef}>
        <Link to="/" className="brand" title="Eternal Packaging" data-testid="sidebar-brand" onClick={closeFlyout}>
          <BrandMark compact={!sidebarExpanded} onDark />
        </Link>
        <nav className="app-sidebar-nav" aria-label="Menu utama">
          {sidebar.map((entry) => (
            entry.kind === "link" ? (
              <RailNavLink
                key={entry.id}
                to={entry.id === "spk" ? "/spk" : entry.id === "chat" ? "/chat" : entry.to}
                testid={entry.testid}
                label={railLabel(entry)}
                icon={RAIL_ICONS[entry.id]}
                badge={entry.id === "chat" && chatUnread > 0 ? chatUnread : 0}
                showLabel={sidebarExpanded}
                onNavigate={closeFlyout}
              />
            ) : (
              <div className="nav-group" key={entry.id} data-testid={`${entry.testid}-group`}>
                <RailButton
                  ref={(el) => { railRefs.current[entry.id] = el; }}
                  testid={entry.id === "employeeAdmin" ? "top-nav-employee-admin" : entry.testid}
                  label={RAIL_LABELS[entry.id] || railLabel(entry)}
                  icon={RAIL_ICONS[entry.id]}
                  expanded={activeSidebarMenu === entry.id}
                  controls={entry.flyoutId}
                  active={!!railActive[entry.id]}
                  open={activeSidebarMenu === entry.id}
                  showLabel={sidebarExpanded}
                  onClick={() => toggleMenu(entry.id)}
                />
              </div>
            )
          ))}
        </nav>
        <button
          type="button"
          className="sidebar-mode-toggle"
          aria-label={sidebarExpanded ? "Ciutkan sidebar" : "Perluas sidebar"}
          aria-pressed={sidebarExpanded}
          data-testid="sidebar-mode-toggle"
          onClick={toggleSidebarWidth}
        >
          {sidebarExpanded
            ? <PanelLeftClose size={18} strokeWidth={1.75} aria-hidden="true" />
            : <PanelLeftOpen size={18} strokeWidth={1.75} aria-hidden="true" />}
          <span className="sidebar-mode-toggle-label">
            {sidebarExpanded ? "Ciutkan menu" : "Perluas menu"}
          </span>
        </button>
        {groupedEntry ? (
          <button
            type="button"
            className="sidebar-edge-toggle"
            aria-label={activeSidebarMenu ? "Tutup menu" : "Buka menu"}
            aria-expanded={!!activeSidebarMenu}
            aria-controls={openEntry ? openEntry.flyoutId : undefined}
            data-testid="sidebar-edge-toggle"
            style={{ top: edgeTop }}
            onClick={toggleChevron}
          >
            {activeSidebarMenu
              ? <ChevronLeft size={16} strokeWidth={2} aria-hidden="true" />
              : <ChevronRight size={16} strokeWidth={2} aria-hidden="true" />}
          </button>
        ) : null}
      </aside>

      {openEntry && openSections.length > 0 && (
        <SidebarFlyoutGrid
          innerRef={flyoutRef}
          id={openEntry.flyoutId}
          testid={openEntry.id === "employeeAdmin" ? "sidebar-flyout-employee-admin" : openEntry.id === "hrPayroll" ? "sidebar-flyout-hr-payroll" : openEntry.flyoutId}
          title={RAIL_LABELS[openEntry.id] || openEntry.label}
          ariaLabel={`Menu ${openEntry.label}`}
          placement={flyoutPos.placement}
          style={flyoutStyle}
          icons={TILE_ICONS}
          onNavigate={closeFlyout}
          sections={openSections.map((section) => ({
            ...section,
            heading: section.heading === "Tugas Produksi" ? "Tugas Produksi" : section.heading === "Buku Besar" ? "Buku Besar" : section.heading === "Kas & Bank" ? "Kas & Bank" : section.heading,
            headingTestid: section.heading === "Kas & Bank" ? "top-nav-kas-bank" : section.heading === "Buku Besar" ? "top-nav-buku-besar" : section.headingTestid,
            marker: section.heading === "Kas & Bank" ? "sidebar-flyout-cash-bank" : section.heading === "Buku Besar" ? "sidebar-flyout-ledger" : section.marker,
          }))}
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
        <header className="topbar" data-testid="app-topbar">
          <span className="topbar-date">{todayLabel}</span>
          <div className="topbar-actions" data-testid="topbar-actions">
            <button
              ref={menuSearchTriggerRef}
              type="button"
              className="ep-menu-search-trigger"
              data-testid="global-menu-search-trigger"
              aria-label="Cari menu"
              aria-haspopup="dialog"
              aria-expanded={menuSearchOpen}
              aria-keyshortcuts="Control+K Meta+K"
              onClick={() => {
                setActiveSidebarMenu(null);
                setMenuSearchOpen(true);
              }}
            >
              <Search size={15} strokeWidth={1.75} aria-hidden="true" />
              <span className="ep-menu-search-trigger-label">Cari menu</span>
              <kbd className="ep-menu-search-kbd">Ctrl+K</kbd>
            </button>
            {showChat ? (
              <NavLink
                to="/chat"
                className={({ isActive }) => "topbar-action-btn" + (isActive ? " is-active" : "")}
                data-testid="topbar-chat-button"
                aria-label={chatUnread > 0 ? `Chat, ${chatUnread} belum dibaca` : "Chat"}
                title="Chat"
                onClick={closeFlyout}
              >
                <MessageCircle size={16} strokeWidth={1.75} aria-hidden="true" />
                {chatUnread > 0 ? (
                  <span className="topbar-action-badge" data-testid="topbar-chat-unread" aria-hidden="true">
                    {chatUnread > 99 ? "99+" : chatUnread}
                  </span>
                ) : null}
              </NavLink>
            ) : null}
            <FloatingNotificationBell placement="topbar" />
          </div>
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
        <GlobalMenuSearch
          open={menuSearchOpen}
          items={menuSearchItems}
          triggerRef={menuSearchTriggerRef}
          onClose={() => setMenuSearchOpen(false)}
          onSelect={(item) => {
            if (!item?.to) return;
            setActiveSidebarMenu(null);
            navigate(item.to);
          }}
        />
        <main className={"content" + (workspaceTabsVisible || cashBankActive || salesActive ? " content-workspace content-kas-bank-workspace content-penjualan-workspace" : "")}>
          {workspaceTabsVisible && (
            <WorkspaceTabs
              tabs={workspaceTabs}
              activePath={workspace.activePath}
              onActivate={workspace.activateTab}
              onClose={workspace.closeTab}
              tabByPath={workspace.tabByPath}
              ariaLabel="Workspace"
              testid="workspace-tabs"
              testidPrefix="workspace-tab"
            />
          )}
          <Suspense fallback={<WorkspaceChunkFallback />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
