import { SALES_NAV_ROUTES, NAV_KEYS } from "./navAccess";
import {
  clearSalesWorkspaceTabs,
  closeSalesTab,
  loadSalesWorkspaceTabs,
  openOrActivateSalesTab,
  saveSalesWorkspaceTabs,
  salesTabByPath,
  salesWorkspaceStorageKey,
  SALES_WORKSPACE_TABS,
} from "./salesWorkspace";

describe("salesWorkspace tabs", () => {
  const user = { id: "u-1", email: "ops@eternal.test" };

  beforeEach(() => {
    sessionStorage.clear();
  });

  test("catalog matches active Penjualan nav routes", () => {
    expect(SALES_WORKSPACE_TABS.map((t) => t.path)).toEqual([
      SALES_NAV_ROUTES[NAV_KEYS.salesOrder],
      SALES_NAV_ROUTES[NAV_KEYS.salesDeliveryOrder],
      SALES_NAV_ROUTES[NAV_KEYS.salesInvoice],
      SALES_NAV_ROUTES[NAV_KEYS.salesReceipt],
      SALES_NAV_ROUTES[NAV_KEYS.importPo],
      SALES_NAV_ROUTES[NAV_KEYS.salesActivity],
    ]);
  });

  test("openOrActivate does not duplicate", () => {
    const a = openOrActivateSalesTab([], "/penjualan/pesanan-penjualan");
    expect(a.tabs).toHaveLength(1);
    const b = openOrActivateSalesTab(a.tabs, "/penjualan/pesanan-penjualan");
    expect(b.tabs).toHaveLength(1);
    expect(b.activated.id).toBe("pesanan-penjualan");
    const c = openOrActivateSalesTab(b.tabs, "/penjualan/faktur-penjualan");
    expect(c.tabs.map((t) => t.id)).toEqual(["pesanan-penjualan", "faktur-penjualan"]);
  });

  test("outlet detail activates Aktivitas Sales without a new tab", () => {
    const a = openOrActivateSalesTab([], "/penjualan/aktivitas-sales");
    const b = openOrActivateSalesTab(a.tabs, "/penjualan/aktivitas-sales/outlet/abc");
    expect(b.tabs).toHaveLength(1);
    expect(b.activated.id).toBe("aktivitas-sales");
    expect(salesTabByPath("/penjualan/aktivitas-sales/outlet/abc").id).toBe("aktivitas-sales");
  });

  test("query strings do not create duplicates", () => {
    const a = openOrActivateSalesTab([], "/penjualan/faktur-penjualan?po_id=1&action=create");
    const b = openOrActivateSalesTab(a.tabs, "/penjualan/faktur-penjualan");
    expect(b.tabs).toHaveLength(1);
  });

  test("Faktur Penjualan remembers list search across sibling activation", () => {
    expect(SALES_WORKSPACE_TABS.find((t) => t.id === "faktur-penjualan")?.rememberSearch).toBe(true);
    const a = openOrActivateSalesTab([], "/penjualan/faktur-penjualan?tab=pending&page=2");
    expect(a.activated.search).toBe("?tab=pending&page=2");
    const b = openOrActivateSalesTab(a.tabs, "/penjualan/pengiriman-pesanan");
    expect(b.tabs.find((t) => t.id === "faktur-penjualan").search).toBe("?tab=pending&page=2");
  });

  test("Pesanan Penjualan remembers list search across sibling activation", () => {
    expect(SALES_WORKSPACE_TABS.find((t) => t.id === "pesanan-penjualan")?.rememberSearch).toBe(true);
    expect(SALES_WORKSPACE_TABS.find((t) => t.id === "pengiriman-pesanan")?.rememberSearch).toBe(true);
    expect(SALES_WORKSPACE_TABS.find((t) => t.id === "penerimaan-penjualan")?.rememberSearch).toBe(true);
    const a = openOrActivateSalesTab([], "/penjualan/pesanan-penjualan?tab=pending&period=this_month&page=2&q=kopi");
    expect(a.activated.search).toBe("?tab=pending&period=this_month&page=2&q=kopi");
    const b = openOrActivateSalesTab(a.tabs, "/penjualan/pengiriman-pesanan?status=SELESAI");
    expect(b.tabs.find((t) => t.id === "pesanan-penjualan").search)
      .toBe("?tab=pending&period=this_month&page=2&q=kopi");
    expect(b.tabs.find((t) => t.id === "pengiriman-pesanan").search).toBe("?status=SELESAI");
  });

  test("close active moves to nearest neighbor", () => {
    const tabs = SALES_WORKSPACE_TABS.slice(0, 3);
    const mid = closeSalesTab(tabs, "pengiriman-pesanan", "/penjualan/pengiriman-pesanan");
    expect(mid.tabs.map((t) => t.id)).toEqual(["pesanan-penjualan", "faktur-penjualan"]);
    expect(mid.navigateTo).toBe("/penjualan/faktur-penjualan");

    const last = closeSalesTab(tabs, "faktur-penjualan", "/penjualan/faktur-penjualan");
    expect(last.navigateTo).toBe("/penjualan/pengiriman-pesanan");
  });

  test("close last active navigates home", () => {
    const tabs = [SALES_WORKSPACE_TABS[0]];
    const r = closeSalesTab(tabs, "pesanan-penjualan", "/penjualan/pesanan-penjualan");
    expect(r.tabs).toEqual([]);
    expect(r.navigateTo).toBe("/");
  });

  test("close inactive does not navigate", () => {
    const tabs = SALES_WORKSPACE_TABS.slice(0, 2);
    const r = closeSalesTab(tabs, "pesanan-penjualan", "/penjualan/pengiriman-pesanan");
    expect(r.tabs.map((t) => t.id)).toEqual(["pengiriman-pesanan"]);
    expect(r.navigateTo).toBe(null);
  });

  test("sessionStorage restore is user-scoped", () => {
    saveSalesWorkspaceTabs(user, SALES_WORKSPACE_TABS.slice(0, 2));
    expect(salesWorkspaceStorageKey(user)).toMatch(/ep\.sales\.workspaceTabs\./);
    expect(loadSalesWorkspaceTabs(user).map((t) => t.id)).toEqual(["pesanan-penjualan", "pengiriman-pesanan"]);
    expect(loadSalesWorkspaceTabs({ id: "other" })).toEqual([]);
  });

  test("invalid stored ids are dropped", () => {
    sessionStorage.setItem(salesWorkspaceStorageKey(user), JSON.stringify(["pesanan-penjualan", "not-a-tab"]));
    expect(loadSalesWorkspaceTabs(user).map((t) => t.id)).toEqual(["pesanan-penjualan"]);
  });

  test("clear on logout removes user key", () => {
    saveSalesWorkspaceTabs(user, SALES_WORKSPACE_TABS);
    clearSalesWorkspaceTabs(user);
    expect(loadSalesWorkspaceTabs(user)).toEqual([]);
  });
});
