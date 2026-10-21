/** Desktop sidebar presentation only. Authorization stays in navAccess. */

import { NAV_KEYS } from "./navAccess";

export const SIDEBAR_RAIL_WIDTH = 64;
/** Expanded desktop rail width — kept in sync with sidebarMode.SIDEBAR_EXPANDED_WIDTH. */
export const SIDEBAR_EXPANDED_WIDTH = 220;

const PRODUCTION_TASK_KEYS = ["designer", "screener", "kepala_produksi", "helper", "operator", "packing"];
const DELIVERY_TASK_KEYS = ["surat_jalan", "loading", "terkirim"];

/** Sidebar copy only. Workspace tabs keep the catalog labels from navAccess. */
export function presentSidebarLeaf(item) {
  if (!item) return item;
  const label = item.to === "/dashboard/penjualan"
    ? "Dashboard Bisnis"
    : (item.key === "terkirim" || item.to === "/tasks/delivery")
      ? "Kurir / Terkirim"
      : item.label;
  const next = { ...item, label };
  if (next.to === "/produksi/jadwal-pengiriman") delete next.end;
  return next;
}

function leavesOf(items) {
  return (items || []).map(presentSidebarLeaf).filter((item) => item && item.to);
}

function section(heading, headingTestid, items, marker) {
  const leaves = leavesOf(items);
  if (!leaves.length) return null;
  return {
    heading: heading || "",
    headingTestid: headingTestid || "",
    marker: marker || "",
    items: leaves,
  };
}

/**
 * Presentation rails in desktop order. A parent with no openable leaf is omitted.
 * Does not grant permissions and does not invent destinations.
 */
export function buildDesktopSidebar({
  dashboardItems = [],
  salesItems = [],
  productionItems = [],
  taskItems = [],
  showRouteOptimizer = false,
  purchasingItems = [],
  inventoryItems = [],
  cashBankItems = [],
  ledgerItems = [],
  employeeAdminItems = [],
  hrPayrollItems = [],
  reportItems = [],
  settingsItems = [],
  showSpk = false,
  showChat = false,
} = {}) {
  const rails = [];
  const dashboard = leavesOf(dashboardItems);
  if (dashboard.length) {
    rails.push({
      id: "dashboard",
      label: "Dashboard",
      kind: "menu",
      testid: "top-nav-dashboard",
      flyoutId: "sidebar-flyout-dashboard",
      sections: [{ heading: "", headingTestid: "", marker: "", items: dashboard }],
    });
  }
  if (showSpk) {
    rails.push({
      id: "spk",
      label: "SPK",
      tooltipLabel: "SPK Antar Divisi",
      kind: "link",
      to: "/spk",
      testid: "top-nav-spk",
    });
  }
  if (showChat) {
    rails.push({
      id: "chat",
      label: "Chat",
      kind: "link",
      to: "/chat",
      testid: "top-nav-chat",
    });
  }

  const sales = leavesOf(salesItems);
  if (sales.length) {
    rails.push({
      id: "sales",
      label: "Penjualan",
      kind: "menu",
      testid: "top-nav-sales",
      flyoutId: "sidebar-flyout-sales",
      sections: [{ heading: "", headingTestid: "", marker: "", items: sales }],
    });
  }

  const schedule = (productionItems || []).filter((item) => item.to === "/produksi");
  const paintCat = (productionItems || []).filter((item) => item.to === "/produksi/sentralisasi-cat");
  const produksiTasks = (taskItems || []).filter((item) => PRODUCTION_TASK_KEYS.includes(item.key));
  const produksiSections = [
    section("Jadwal", "sidebar-section-jadwal", schedule),
    section("Sentralisasi Cat", "sidebar-section-sentralisasi-cat", paintCat),
    section("Tugas Produksi", "top-nav-tasks-group", produksiTasks),
  ].filter(Boolean);
  if (produksiSections.length) {
    rails.push({
      id: "produksi",
      label: "Produksi",
      kind: "menu",
      testid: "top-nav-produksi",
      flyoutId: "sidebar-flyout-produksi",
      sections: produksiSections,
    });
  }

  const deliveryDocs = (productionItems || []).filter((item) => (
    item.to === "/produksi/jadwal-pengiriman"
    || item.to === "/produksi/rencana-jadwal-kirim"
    || item.to === "/produksi/jadwal-pengiriman-ulang"
  ));
  const deliveryTasks = (taskItems || []).filter((item) => DELIVERY_TASK_KEYS.includes(item.key));
  const pengiriman = leavesOf([...deliveryDocs, ...deliveryTasks]);
  if (showRouteOptimizer) {
    pengiriman.push({
      key: NAV_KEYS.routeOptimizer,
      label: "Optimasi Rute",
      to: "/optimasi-rute",
      testid: "top-nav-optimasi-rute",
      tone: "blue",
    });
  }
  if (pengiriman.length) {
    rails.push({
      id: "pengiriman",
      label: "Pengiriman",
      kind: "menu",
      testid: "top-nav-pengiriman",
      flyoutId: "sidebar-flyout-pengiriman",
      sections: [{ heading: "", headingTestid: "", marker: "", items: pengiriman }],
    });
  }

  const purchasingLeaves = leavesOf(purchasingItems);
  const purchasingTxn = purchasingLeaves.filter((item) => item.key !== NAV_KEYS.purchaseHpp);
  const purchasingHpp = purchasingLeaves.filter((item) => item.key === NAV_KEYS.purchaseHpp);
  const purchasingSections = [];
  if (purchasingTxn.length) {
    purchasingSections.push({ heading: "", headingTestid: "", marker: "", items: purchasingTxn });
  }
  if (purchasingHpp.length) {
    purchasingSections.push({
      heading: "",
      headingTestid: "sidebar-purchasing-hpp-divider",
      marker: purchasingTxn.length ? "sidebar-purchasing-divider" : "",
      items: purchasingHpp,
    });
  }
  if (purchasingSections.length) {
    rails.push({
      id: "purchasing",
      label: "Pembelian",
      kind: "menu",
      testid: "top-nav-pembelian",
      flyoutId: "sidebar-flyout-purchasing",
      sections: purchasingSections,
    });
  }

  const inventory = leavesOf(inventoryItems);
  if (inventory.length) {
    rails.push({
      id: "inventory",
      label: "Persediaan",
      kind: "menu",
      testid: "top-nav-persediaan",
      flyoutId: "sidebar-flyout-inventory",
      sections: [{ heading: "", headingTestid: "", marker: "", items: inventory }],
    });
  }

  const financeSections = [
    section("Kas & Bank", "top-nav-kas-bank", cashBankItems, "sidebar-flyout-cash-bank"),
    section("Buku Besar", "top-nav-buku-besar", ledgerItems, "sidebar-flyout-ledger"),
  ].filter(Boolean);
  if (financeSections.length) {
    rails.push({
      id: "keuangan",
      label: "Keuangan",
      kind: "menu",
      testid: "top-nav-keuangan",
      flyoutId: "sidebar-flyout-keuangan",
      sections: financeSections,
    });
  }

  const employeeAdmin = leavesOf(employeeAdminItems);
  if (employeeAdmin.length) {
    rails.push({
      id: "employeeAdmin",
      label: "Administrasi Karyawan",
      kind: "menu",
      testid: "top-nav-employee-admin",
      flyoutId: "sidebar-flyout-employee-admin",
      sections: [{ heading: "", headingTestid: "", marker: "", items: employeeAdmin }],
    });
  }

  const hrPayroll = leavesOf(hrPayrollItems);
  if (hrPayroll.length) {
    rails.push({
      id: "hrPayroll",
      label: "HR & Payroll",
      kind: "menu",
      testid: "top-nav-hr-payroll-menu",
      flyoutId: "sidebar-flyout-hr-payroll",
      sections: [{ heading: "", headingTestid: "", marker: "", items: hrPayroll }],
    });
  }

  const reports = leavesOf(reportItems);
  if (reports.length) {
    rails.push({
      id: "reports",
      label: "Laporan",
      kind: "menu",
      testid: "top-nav-laporan",
      flyoutId: "sidebar-flyout-reports",
      sections: [{ heading: "", headingTestid: "", marker: "", items: reports }],
    });
  }

  const settings = leavesOf(settingsItems);
  if (settings.length) {
    rails.push({
      id: "settings",
      label: "Pengaturan",
      kind: "menu",
      testid: "top-nav-settings",
      flyoutId: "sidebar-flyout-settings",
      sections: [{ heading: "", headingTestid: "", marker: "", items: settings }],
    });
  }

  return rails;
}

export function sidebarLeaves(rails) {
  const leaves = [];
  (rails || []).forEach((rail) => {
    if (rail.kind === "link") {
      leaves.push({ group: rail.id, section: "", label: rail.label, to: rail.to, key: rail.id });
    }
    (rail.sections || []).forEach((block) => {
      (block.items || []).forEach((item) => {
        leaves.push({
          group: rail.id,
          section: block.heading || "",
          label: item.label,
          to: item.to,
          key: item.key,
        });
      });
    });
  });
  return leaves;
}

function cleanPath(pathname) {
  const path = String(pathname || "").split("?")[0].replace(/\/$/, "") || "/";
  return path;
}

function matchesBase(pathname, base) {
  const path = cleanPath(pathname);
  return path === base || path.startsWith(`${base}/`);
}

export function isSalesReportPath(pathname) {
  const path = cleanPath(pathname);
  return path === "/laporan/status-po" || path === "/laporan/prediksi-order";
}

export function isProductionRailActive(pathname) {
  const path = cleanPath(pathname);
  if (path === "/produksi" || path === "/produksi/sentralisasi-cat") return true;
  return [
    "/tasks/designer",
    "/tasks/screener",
    "/tasks/kepala-produksi",
    "/tasks/helper",
    "/tasks/operator",
    "/tasks/packing",
  ].some((base) => matchesBase(pathname, base));
}

export function isDeliveryRailActive(pathname) {
  return [
    "/produksi/jadwal-pengiriman",
    "/produksi/rencana-jadwal-kirim",
    "/produksi/jadwal-pengiriman-ulang",
    "/tasks/surat-jalan",
    "/tasks/loading",
    "/tasks/delivery",
    "/optimasi-rute",
  ].some((base) => matchesBase(pathname, base));
}

export function isSalesRailActive(pathname) {
  const path = cleanPath(pathname);
  return path.startsWith("/penjualan") || path.startsWith("/admin/import") || isSalesReportPath(pathname);
}

export function isOperationalReportRailActive(pathname) {
  const path = cleanPath(pathname);
  return path.startsWith("/laporan") && !isSalesReportPath(pathname);
}

export function isFinanceRailActive(pathname) {
  const path = cleanPath(pathname);
  return path.startsWith("/kas-bank") || path.startsWith("/buku-besar");
}

export function railLabel(entry) {
  if (!entry) return "";
  return entry.tooltipLabel || entry.label;
}

/** Grouped rail that owns this path. Direct links such as Chat and SPK return null. */
export function groupedRailForPath(pathname) {
  if (isDeliveryRailActive(pathname)) return "pengiriman";
  if (isProductionRailActive(pathname)) return "produksi";
  if (isSalesRailActive(pathname)) return "sales";
  if (isFinanceRailActive(pathname)) return "keuangan";
  const path = cleanPath(pathname);
  if (path === "/dashboard" || path.startsWith("/dashboard/")) return "dashboard";
  if (path.startsWith("/pembelian")) return "purchasing";
  if (path.startsWith("/persediaan")) return "inventory";
  if (path.startsWith("/administrasi-karyawan")) return "employeeAdmin";
  if (path.startsWith("/hr-payroll")) return "hrPayroll";
  if (isOperationalReportRailActive(pathname)) return "reports";
  if (path.startsWith("/settings")) return "settings";
  return null;
}

/** Open the active group, or close the flyout that is already open. */
export function nextChevronMenu(openMenuId, groupedRailId) {
  if (openMenuId) return null;
  return groupedRailId || null;
}
