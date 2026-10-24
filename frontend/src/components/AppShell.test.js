import fs from "fs";
import path from "path";
import { nextSidebarMenu } from "../lib/sidebarFlyout";

describe("sidebar flyout interaction", () => {
  const shell = fs.readFileSync(path.join(__dirname, "AppShell.jsx"), "utf8");
  const grid = fs.readFileSync(path.join(__dirname, "SidebarFlyoutGrid.jsx"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const presentation = fs.readFileSync(path.join(__dirname, "../lib/sidebarPresentation.js"), "utf8");
  const flyout = fs.readFileSync(path.join(__dirname, "../lib/sidebarFlyout.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

  test("click parent opens, same parent closes, other parent switches", () => {
    expect(nextSidebarMenu(null, "produksi")).toBe("produksi");
    expect(nextSidebarMenu("produksi", "produksi")).toBe(null);
    expect(nextSidebarMenu("produksi", "reports")).toBe("reports");
    expect(shell).toMatch(/nextSidebarMenu\(cur, key\)/);
    expect(presentation).toMatch(/sidebar-flyout-produksi/);
    expect(presentation).toMatch(/sidebar-flyout-reports/);
  });

  test("click outside and Escape close the flyout and Escape returns focus", () => {
    expect(shell).toMatch(/sidebar-flyout-scrim/);
    expect(shell).toMatch(/e\.key !== "Escape"/);
    expect(shell).toMatch(/setActiveSidebarMenu\(null\)/);
    expect(shell).toMatch(/trigger\.focus/);
    expect(shell).toMatch(/\[location\.pathname, location\.search\]/);
    expect(shell).toMatch(/onNavigate=\{closeFlyout\}/);
  });

  test("active route still marks parent and tooltip is preserved", () => {
    expect(shell).toMatch(/produksi: produksiActive/);
    expect(shell).toMatch(/is-active/);
    expect(shell).toMatch(/reportsActive/);
    expect(shell).toMatch(/pengirimanActive/);
    expect(shell).toMatch(/keuanganActive/);
    expect(shell).toMatch(/onClick=\{closeFlyout\}/);
    expect(shell).toMatch(/title=\{label\}/);
    expect(shell).toMatch(/rail-tooltip/);
    expect(shell).toMatch(/aria-label=\{label\}/);
    expect(shell).toMatch(/rail-label/);
    expect(shell).toMatch(/showLabel=\{sidebarExpanded\}/);
  });

  test("flyout stays to the right of the rail token and does not shift main content", () => {
    expect(shell).toMatch(/placeSidebarFlyout/);
    expect(shell).toMatch(/app-sidebar-slot/);
    expect(shell).toMatch(/style=\{flyoutStyle\}/);
    expect(shell).toMatch(/ref=\{sidebarRef\}/);
    expect(css).toMatch(/\.app-sidebar-slot\{/);
    expect(css).toMatch(/\.app-shell\{--sidebar-rail:64px/);
    expect(css).toMatch(/grid-template-columns:var\(--sidebar-rail\) minmax\(0,1fr\)/);
    expect(css).toMatch(/\[data-sidebar-mode="expanded"\]\{--sidebar-rail:220px\}/);
    expect(css).not.toMatch(/--sidebar-rail:240px/);
    expect(css).not.toMatch(/\.sidebar-flyout\{[^}]*top:12px/);
    expect(css).not.toMatch(/\.sidebar-flyout\{[^}]*left:8px;right:8px;top:56px/);
    expect(css).toMatch(/overflow-x:hidden/);
    expect(css).toMatch(/width:min\(280px,calc\(100vw - var\(--sidebar-rail\) - 16px\)\)/);
    expect(flyout).toMatch(/permanent compact vertical rail always opens to the right/);
    expect(shell).toMatch(/sidebarExpanded/);
    expect(shell).toMatch(/sidebar-mode-toggle/);
    expect(shell).toMatch(/data-sidebar-mode=\{sidebarMode\}/);
    expect(shell).toMatch(/sidebarRailWidth\(sidebarMode\)/);
  });

  test("menu names routes and permission helpers stay the same", () => {
    expect(shell).toMatch(/Produksi/);
    expect(shell).toMatch(/Penjualan/);
    expect(shell).toMatch(/Pembelian/);
    expect(shell).toMatch(/Persediaan/);
    expect(shell).toMatch(/Laporan/);
    expect(shell).toMatch(/Administrasi Karyawan/);
    expect(shell).toMatch(/Pengaturan/);
    expect(shell).toMatch(/Keuangan/);
    expect(shell).toMatch(/Pengiriman/);
    expect(nav).toMatch(/Jadwal Produksi/);
    expect(nav).not.toMatch(/to: "\/produksi\/pengiriman-hari-ini"/);
    expect(nav).toMatch(/Jadwal Pengiriman/);
    expect(nav).toMatch(/Jadwal Pengiriman Ulang/);
    expect(nav).toMatch(/Barang\/Jasa/);
    expect(nav).toMatch(/TASK_NAV_ORDER/);
    expect(nav).toMatch(/"\/produksi\/jadwal-pengiriman"/);
    expect(nav).toMatch(/\/persediaan\/master-barang-jasa/);
    expect(nav).toMatch(/"\/laporan"/);
    expect(nav).toMatch(/\/laporan\?view=saved/);
    expect(nav).toMatch(/Dashboard Produksi/);
    expect(nav).toMatch(/Dashboard Penjualan/);
    expect(nav).toMatch(/Driver \/ Terkirim/);
    expect(shell).not.toMatch(/Rute Saya/);
    expect(shell).not.toMatch(/rute-saya/);
    expect(shell).toMatch(/Tugas Produksi/);
    expect(shell).toMatch(/productionNavVisible\(hasPerm\)/);
    expect(shell).toMatch(/productionMenuItems\(hasPerm\)/);
    expect(shell).toMatch(/taskMenuItems\(hasPerm\)/);
    expect(shell).toMatch(/salesNavVisible\(hasPerm\)/);
    expect(shell).toMatch(/hasPerm\(NAV_KEYS\.dashboard\)/);
    expect(shell).toMatch(/dashboardNavVisible\(hasPerm, user\)/);
    expect(shell).toMatch(/dashboardMenuItems\(hasPerm, user\)/);
    expect(shell).toMatch(/TASK_NAV_ORDER/);
    expect(shell).not.toMatch(/Tugas per Divisi/);
    expect(shell).toMatch(/productionNav\.deliveryToday/);
    expect(shell).not.toMatch(/Pengiriman Hari Ini/);
    expect(shell).not.toMatch(/nav\.keuangan/);
    expect(presentation).toMatch(/Dashboard Bisnis/);
    expect(presentation).toMatch(/Kurir \/ Terkirim/);
  });

  test("submenu is a compact list instead of the tile grid", () => {
    expect(shell).toMatch(/salesMenuItems\(hasPerm\)/);
    expect(shell).toMatch(/settingsMenuItems\(hasPerm\)/);
    expect(grid).toMatch(/className="sidebar-flyout is-menu-list"/);
    expect(grid).toMatch(/sidebar-menu-list/);
    expect(grid).toMatch(/sidebar-menu-link/);
    expect(grid).toMatch(/to=\{item\.to\}/);
    expect(grid).toMatch(/data-testid=\{item\.testid\}/);
    expect(grid).toMatch(/aria-label=\{item\.label\}/);
    expect(grid).toMatch(/size=\{18\}/);
    expect(grid).not.toMatch(/sales-menu-tile/);
    expect(grid).not.toMatch(/is-sales-grid/);
    expect(css).toMatch(/\.sidebar-flyout\.is-menu-list\{/);
    expect(css).toMatch(/\.sidebar-menu-link:focus-visible\{/);
    expect(css).toMatch(/\.sidebar-menu-link\.is-active\{/);
    expect(shell).not.toMatch(/Penawaran/);
    expect(shell).not.toMatch(/Faktur/);
    expect(shell).not.toMatch(/Komisi/);
  });

  test("presentation parents reuse one list flyout and hide when they have no leaves", () => {
    expect(presentation).toMatch(/id: "dashboard"/);
    expect(presentation).toMatch(/id: "produksi"/);
    expect(presentation).toMatch(/id: "pengiriman"/);
    expect(presentation).toMatch(/id: "sales"/);
    expect(presentation).toMatch(/id: "purchasing"/);
    expect(presentation).toMatch(/id: "inventory"/);
    expect(presentation).toMatch(/id: "keuangan"/);
    expect(presentation).toMatch(/sidebar-flyout-ledger/);
    expect(presentation).toMatch(/sidebar-flyout-cash-bank/);
    expect(presentation).toMatch(/id: "reports"/);
    expect(presentation).toMatch(/id: "employeeAdmin"/);
    expect(presentation).toMatch(/id: "hrPayroll"/);
    expect(presentation).toMatch(/id: "settings"/);
    expect(shell.match(/<SidebarFlyoutGrid/g).length).toBe(1);
    expect(shell).toMatch(/openSections\.length > 0/);
    expect(shell).toMatch(/showSettings = settingsItems\.length > 0/);
    expect(grid).toMatch(/if \(!blocks\.length\) return null/);
  });

  test("one global workspace strip is rendered from the shared engine", () => {
    expect(shell).toMatch(/useGlobalWorkspaceTabs/);
    expect(shell).toMatch(/<WorkspaceTabs/);
    expect(shell).toMatch(/testid="workspace-tabs"/);
    expect(shell).toMatch(/content-workspace/);
    expect(shell.match(/<WorkspaceTabs/g).length).toBe(1);
    expect(shell).not.toMatch(/CashBankWorkspaceTabs/);
    expect(shell).not.toMatch(/useSalesWorkspaceTabs/);
    expect(shell).not.toMatch(/useCashBankWorkspaceTabs/);
  });

  test("global menu search uses sidebarLeaves catalog and navigate(to)", () => {
    expect(shell).toMatch(/menuSearchItemsFromRails\(sidebar\)/);
    expect(shell).toMatch(/<GlobalMenuSearch/);
    expect(shell).toMatch(/global-menu-search-trigger/);
    expect(shell).toMatch(/Cari menu/);
    expect(shell).toMatch(/isMenuSearchOpenShortcut/);
    expect(shell).toMatch(/navigate\(item\.to\)/);
    expect(shell).not.toMatch(/fuse\.js|fuse-js|fuzzy/);
  });

  test("topbar profile shows the signed-in account, not the open dashboard title", () => {
    expect(shell).toMatch(/className="profile-name">\{user\?\.name \|\| user\?\.email\}/);
    expect(shell).toMatch(/data-testid="current-user-role">\{ROLES\[user\?\.role\] \|\| user\?\.role\}/);
    expect(shell).not.toMatch(/Preview Keuangan/);
    expect(shell).not.toMatch(/Dashboard Produksi|Dashboard Keuangan/);
  });

  test("topbar aligns search, chat, and notification without duplicating chat unread fetch", () => {
    expect(shell).toMatch(/topbar-actions/);
    expect(shell).toMatch(/topbar-chat-button/);
    expect(shell).toMatch(/to="\/chat"/);
    expect(shell).toMatch(/FloatingNotificationBell placement="topbar"/);
    expect(shell).toMatch(/chatUnread > 0 \? `Chat, \$\{chatUnread\} belum dibaca`/);
    expect(shell).toMatch(/entry\.id === "chat" && chatUnread/);
    expect(presentation).toMatch(/testid: "top-nav-chat"/);
    expect((shell.match(/api\.chatUnreadSummary/g) || []).length).toBe(1);
    expect((shell.match(/<FloatingNotificationBell/g) || []).length).toBe(1);
  });

  test("SPK and Chat stay direct rail links while Dashboard opens a submenu", () => {
    expect(shell).toMatch(/to=\{entry\.id === "spk" \? "\/spk" : entry\.id === "chat" \? "\/chat" : entry\.to\}/);
    expect(shell).not.toMatch(/sidebar-flyout-spk/);
    expect(presentation).toMatch(/flyoutId: "sidebar-flyout-dashboard"/);
    expect(shell).toMatch(/toggleMenu\(entry\.id\)/);
    expect(shell).not.toMatch(/to="\/dashboard"/);
    expect(presentation).toMatch(/label: "SPK"/);
    expect(presentation).toMatch(/tooltipLabel: "SPK Antar Divisi"/);
    expect(presentation).not.toMatch(/labelCompact/);
    expect(presentation).not.toMatch(/labelExpanded/);
    expect(presentation).toMatch(/export const SIDEBAR_EXPANDED_WIDTH = 220/);
  });

  test("Pengaturan rail opens the shared list flyout without navigating", () => {
    expect(presentation).toMatch(/testid: "top-nav-settings"/);
    expect(shell).toMatch(/settings: "Pengaturan"/);
    expect(shell).toMatch(/settingsMenuItems\(hasPerm\)/);
    expect(presentation).toMatch(/flyoutId: "sidebar-flyout-settings"/);
    expect(shell).not.toMatch(/to="\/settings"/);
    expect(shell).not.toMatch(/Pengaturan Hub/);
    expect(grid).toMatch(/to=\{item\.to\}/);
  });

  test("divider chevron opens the active grouped rail and stays off direct links", () => {
    expect(shell).toMatch(/groupedRailForPath\(location\.pathname\)/);
    expect(shell).toMatch(/nextChevronMenu\(cur, edgeGroupId\)/);
    expect(shell).toMatch(/data-testid="sidebar-edge-toggle"/);
    expect(shell).toMatch(/aria-label=\{activeSidebarMenu \? "Tutup menu" : "Buka menu"\}/);
    expect(shell).toMatch(/aria-expanded=\{!!activeSidebarMenu\}/);
    expect(shell).toMatch(/aria-controls=\{openEntry \? openEntry\.flyoutId : undefined\}/);
    expect(shell).toMatch(/\{groupedEntry \? \(/);
    expect(shell).toMatch(/<ChevronRight/);
    expect(shell).toMatch(/<ChevronLeft/);
    expect(css).toMatch(/\.sidebar-edge-toggle\{/);
    expect(css).toMatch(/width:26px;height:26px/);
    expect(css).toMatch(/\.sidebar-edge-toggle:focus-visible\{/);
    expect(presentation).toMatch(/export const SIDEBAR_RAIL_WIDTH = 64/);
    expect(presentation).not.toMatch(/ep\.desktopSidebar\.mode/);
    expect(presentation).not.toMatch(/readSidebarMode/);
    expect(presentation).not.toMatch(/writeSidebarMode/);
    expect(shell).toMatch(/resolveSidebarMode/);
    expect(shell).toMatch(/writeSidebarMode/);
    expect(shell).toMatch(/data-testid="sidebar-mode-toggle"/);
    expect(shell).toMatch(/data-testid="sidebar-edge-toggle"/);
    expect(css).toMatch(/\.sidebar-mode-toggle\{/);
    expect(shell).not.toMatch(/role === /);
  });

  test("collapsible mode keeps flyout submenu and user preference helpers outside presentation", () => {
    expect(shell).toMatch(/from "\.\.\/lib\/sidebarMode"/);
    expect(shell).toMatch(/toggleSidebarWidth/);
    expect(shell).toMatch(/sidebarUserSetRef/);
    expect(shell).toMatch(/defaultSidebarModeForWidth/);
    expect(shell).toMatch(/BrandMark compact=\{!sidebarExpanded\}/);
    expect(css).toMatch(/\.rail-label\{/);
    expect(css).toMatch(/\[data-sidebar-mode="expanded"\] \.rail-label\{display:block\}/);
    expect(css).toMatch(/\[data-sidebar-mode="expanded"\] \.rail-tooltip\{display:none\}/);
  });

  test("authenticated shell mounts web floating notification bell once", () => {
    expect(shell).toMatch(/import FloatingNotificationBell from "\.\/FloatingNotificationBell"/);
    expect(shell).toMatch(/<FloatingNotificationBell placement="topbar" \/>/);
  });
});
