import { NAV_KEYS } from "./navAccess";
import { buildDesktopSidebar, sidebarLeaves } from "./sidebarPresentation";
import {
  MENU_SEARCH_MAX_RESULTS,
  clampMenuSearchHighlight,
  filterMenuSearchItems,
  isMenuSearchOpenShortcut,
  isMenuSearchShortcutBlockedTarget,
  menuSearchItemsFromRails,
  normalizeMenuSearchQuery,
} from "./menuSearch";

function leaf(to, label, key = to) {
  return { key, label, to, end: true };
}

function fullRails() {
  return buildDesktopSidebar({
    dashboardItems: [
      leaf("/dashboard", "Dashboard Produksi", NAV_KEYS.dashboard),
      leaf("/dashboard/penjualan", "Dashboard Penjualan", NAV_KEYS.dashboardSales),
    ],
    salesItems: [
      leaf("/penjualan/pesanan-penjualan", "Pesanan Penjualan", NAV_KEYS.salesOrder),
      leaf("/laporan/status-po", "Status PO", NAV_KEYS.statusPo),
      leaf("/penjualan/penerimaan-penjualan", "Penerimaan Penjualan", NAV_KEYS.salesReceipt),
    ],
    productionItems: [
      leaf("/produksi", "Jadwal Produksi", NAV_KEYS.schedule),
      leaf("/produksi/jadwal-pengiriman", "Jadwal Pengiriman", NAV_KEYS.deliverySchedule),
    ],
    taskItems: [
      { key: "packing", label: "Packing", to: "/tasks/packing" },
      { key: "terkirim", label: "Driver / Terkirim", to: "/tasks/delivery" },
    ],
    showRouteOptimizer: true,
    purchasingItems: [leaf("/pembelian/permintaan", "Permintaan", NAV_KEYS.purchaseRequests)],
    inventoryItems: [leaf("/persediaan/barang-jasa", "Barang/Jasa", NAV_KEYS.masterItems)],
    cashBankItems: [leaf("/kas-bank/penerimaan", "Penerimaan", NAV_KEYS.cashBankReceipts)],
    ledgerItems: [leaf("/buku-besar/buku-besar", "Buku Besar", NAV_KEYS.ledgerGeneralLedger)],
    employeeAdminItems: [],
    hrPayrollItems: [],
    reportItems: [leaf("/laporan/analitik-output", "Analitik Output", NAV_KEYS.analytics)],
    settingsItems: [
      { key: "employees", label: "Karyawan", to: "/settings/employees" },
      { key: "audit", label: "Audit Log", to: "/settings/audit" },
    ],
    showSpk: true,
    showChat: true,
  });
}

describe("menuSearch catalog from desktop sidebar", () => {
  test("uses the same authorized leaves as sidebarLeaves", () => {
    const rails = fullRails();
    const fromSidebar = sidebarLeaves(rails).map((l) => `${l.to}|${l.label}`);
    const fromSearch = menuSearchItemsFromRails(rails).map((l) => `${l.to}|${l.label}`);
    expect(fromSearch).toEqual(fromSidebar);
  });

  test("keeps presentation labels and attaches group labels", () => {
    const items = menuSearchItemsFromRails(fullRails());
    const bisnis = items.find((i) => i.to === "/dashboard/penjualan");
    expect(bisnis.label).toBe("Dashboard Bisnis");
    expect(bisnis.groupLabel).toBe("Dashboard");
    const kurir = items.find((i) => i.to === "/tasks/delivery");
    expect(kurir.label).toBe("Kurir / Terkirim");
    expect(kurir.groupLabel).toBe("Pengiriman");
  });

  test("excludes parents without routes and unauthorized empty rails", () => {
    const empty = menuSearchItemsFromRails(buildDesktopSidebar({}));
    expect(empty).toEqual([]);
    const items = menuSearchItemsFromRails(fullRails());
    expect(items.every((i) => i.to && i.label)).toBe(true);
    expect(items.some((i) => i.label === "Penjualan" && !i.to)).toBe(false);
  });

  test("ambiguous Penerimaan labels are distinguished by group", () => {
    const items = menuSearchItemsFromRails(fullRails());
    const hits = filterMenuSearchItems(items, "penerimaan");
    const groups = hits.map((h) => `${h.label}|${h.groupLabel}`);
    expect(groups).toEqual(expect.arrayContaining([
      "Penerimaan Penjualan|Penjualan",
      "Penerimaan|Keuangan",
    ]));
  });

  test("settings tabs appear only when present in filtered rails", () => {
    const withSettings = menuSearchItemsFromRails(fullRails());
    expect(withSettings.some((i) => i.to === "/settings/employees")).toBe(true);
    const noSettings = menuSearchItemsFromRails(buildDesktopSidebar({
      salesItems: [leaf("/penjualan/pesanan-penjualan", "Pesanan Penjualan", NAV_KEYS.salesOrder)],
    }));
    expect(noSettings.some((i) => String(i.to).startsWith("/settings"))).toBe(false);
  });
});

describe("menuSearch filtering", () => {
  const items = menuSearchItemsFromRails(fullRails());

  test("empty query returns first max items", () => {
    const out = filterMenuSearchItems(items, "   ");
    expect(out.length).toBeLessThanOrEqual(MENU_SEARCH_MAX_RESULTS);
    expect(out.length).toBeGreaterThan(0);
    expect(normalizeMenuSearchQuery("  Status PO ")).toBe("status po");
  });

  test("partial case-insensitive match on label and group", () => {
    const byLabel = filterMenuSearchItems(items, "status po");
    expect(byLabel.some((i) => i.label === "Status PO")).toBe(true);
    const byGroup = filterMenuSearchItems(items, "penjualan");
    expect(byGroup.every((i) => (
      i.groupLabel.toLowerCase().includes("penjualan")
      || i.label.toLowerCase().includes("penjualan")
      || i.section.toLowerCase().includes("penjualan")
    ))).toBe(true);
  });

  test("no results and hard cap of 20", () => {
    expect(filterMenuSearchItems(items, "zzz-not-a-menu")).toEqual([]);
    const many = Array.from({ length: 40 }, (_, i) => ({
      key: `k${i}`,
      to: `/x/${i}`,
      label: `Item ${i}`,
      group: "sales",
      section: "",
      groupLabel: "Penjualan",
    }));
    expect(filterMenuSearchItems(many, "item", 20)).toHaveLength(20);
  });

  test("highlight clamp wraps ends", () => {
    expect(clampMenuSearchHighlight(-1, 3)).toBe(2);
    expect(clampMenuSearchHighlight(3, 3)).toBe(0);
    expect(clampMenuSearchHighlight(1, 3)).toBe(1);
    expect(clampMenuSearchHighlight(0, 0)).toBe(0);
  });

  test("shortcut helpers respect editable targets", () => {
    expect(isMenuSearchOpenShortcut({ key: "k", ctrlKey: true })).toBe(true);
    expect(isMenuSearchOpenShortcut({ key: "k", metaKey: true })).toBe(true);
    expect(isMenuSearchOpenShortcut({ key: "k", ctrlKey: true, altKey: true })).toBe(false);
    expect(isMenuSearchShortcutBlockedTarget({ tagName: "INPUT" })).toBe(true);
    expect(isMenuSearchShortcutBlockedTarget({ tagName: "DIV", isContentEditable: false, getAttribute: () => null })).toBe(false);
  });
});
