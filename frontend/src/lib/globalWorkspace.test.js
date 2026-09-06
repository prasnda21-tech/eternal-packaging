import { NAV_KEYS } from "./navAccess";
import {
  GLOBAL_WORKSPACE_TABS,
  clearGlobalWorkspaceTabs,
  closeGlobalTab,
  globalTabByPath,
  globalWorkspaceStorageKey,
  loadGlobalWorkspaceTabs,
  openOrActivateGlobalTab,
  saveGlobalWorkspaceTabs,
  visibleGlobalWorkspaceCatalog,
} from "./globalWorkspace";

describe("global workspace tabs", () => {
  const user = { id: "u-1", email: "ops@eternal.test" };
  const allow = () => true;

  beforeEach(() => {
    sessionStorage.clear();
  });

  test("sidebar leaf opens a tab", () => {
    const r = openOrActivateGlobalTab([], "/dashboard");
    expect(r.activated.id).toBe("dashboard");
    expect(r.tabs).toHaveLength(1);
  });

  test("route activates existing tab", () => {
    const a = openOrActivateGlobalTab([], "/dashboard");
    const b = openOrActivateGlobalTab(a.tabs, "/dashboard");
    expect(b.tabs).toHaveLength(1);
    expect(b.activated.id).toBe("dashboard");
  });

  test("duplicate prevention", () => {
    const a = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    const b = openOrActivateGlobalTab(a.tabs, "/penjualan/pesanan-penjualan");
    expect(b.tabs).toHaveLength(1);
  });

  test("query does not duplicate", () => {
    const a = openOrActivateGlobalTab([], "/buku-besar/buku-besar?account_id=1&start_date=2026-01-01");
    const b = openOrActivateGlobalTab(a.tabs, "/buku-besar/buku-besar?account_id=2");
    expect(b.tabs).toHaveLength(1);
    expect(b.activated.path).toBe("/buku-besar/buku-besar");
  });

  test("close inactive does not navigate", () => {
    const a = openOrActivateGlobalTab([], "/dashboard");
    const b = openOrActivateGlobalTab(a.tabs, "/settings");
    const r = closeGlobalTab(b.tabs, "dashboard", "/settings");
    expect(r.tabs.map((t) => t.id)).toEqual(["settings"]);
    expect(r.navigateTo).toBe(null);
  });

  test("close active moves to nearest neighbor then home", () => {
    const tabs = [
      GLOBAL_WORKSPACE_TABS.find((t) => t.id === "dashboard"),
      GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/produksi"),
      GLOBAL_WORKSPACE_TABS.find((t) => t.id === "settings"),
    ];
    const mid = closeGlobalTab(tabs, tabs[1].id, "/produksi");
    expect(mid.navigateTo).toBe("/settings");
    const last = closeGlobalTab([tabs[0]], "dashboard", "/dashboard");
    expect(last.tabs).toEqual([]);
    expect(last.navigateTo).toBe("/");
  });

  test("restore session history is user-scoped", () => {
    const open = [
      GLOBAL_WORKSPACE_TABS.find((t) => t.id === "dashboard"),
      GLOBAL_WORKSPACE_TABS.find((t) => t.id === "settings"),
    ];
    saveGlobalWorkspaceTabs(user, open);
    expect(globalWorkspaceStorageKey(user)).toMatch(/ep\.workspaceTabs\./);
    expect(loadGlobalWorkspaceTabs(user).map((t) => t.id)).toEqual(["dashboard", "settings"]);
    expect(loadGlobalWorkspaceTabs({ id: "other" })).toEqual([]);
  });

  test("logout cleanup removes user key", () => {
    saveGlobalWorkspaceTabs(user, GLOBAL_WORKSPACE_TABS.slice(0, 2));
    clearGlobalWorkspaceTabs(user);
    expect(loadGlobalWorkspaceTabs(user)).toEqual([]);
  });

  test("unauthorized restored tab is filtered out", () => {
    saveGlobalWorkspaceTabs(user, [
      GLOBAL_WORKSPACE_TABS.find((t) => t.id === "dashboard"),
      GLOBAL_WORKSPACE_TABS.find((t) => t.id === "settings"),
    ]);
    const onlyDash = (key) => key === NAV_KEYS.dashboard;
    expect(loadGlobalWorkspaceTabs(user, onlyDash).map((t) => t.id)).toEqual(["dashboard"]);
    expect(openOrActivateGlobalTab([], "/settings", onlyDash).activated).toBe(null);
  });

  test("unknown route does not crash or invent a tab", () => {
    const r = openOrActivateGlobalTab([], "/not-a-sidebar-page");
    expect(r.tabs).toEqual([]);
    expect(r.activated).toBe(null);
    expect(globalTabByPath("/po/abc")).toBe(null);
    expect(globalTabByPath("/administrasi-karyawan/pengajuan/99")).toBe(null);
  });

  test("module coverage includes one leaf per active sidebar group", () => {
    const byPath = Object.fromEntries(GLOBAL_WORKSPACE_TABS.map((t) => [t.path, t]));
    expect(byPath["/dashboard"]).toBeTruthy();
    expect(byPath["/produksi"]).toBeTruthy();
    expect(byPath["/penjualan/pesanan-penjualan"]).toBeTruthy();
    expect(byPath["/pembelian/permintaan"]).toBeTruthy();
    expect(byPath["/persediaan/master-barang-jasa"]).toBeTruthy();
    expect(byPath["/kas-bank/daftar"]).toBeTruthy();
    expect(byPath["/buku-besar/jurnal-umum"]).toBeTruthy();
    expect(byPath["/laporan/analitik-output"]).toBeTruthy();
    expect(byPath["/administrasi-karyawan/pengajuan-saya"]).toBeTruthy();
    expect(byPath["/hr-payroll/penggajian"]).toBeTruthy();
    expect(byPath["/settings"]).toBeTruthy();
    expect(byPath["/optimasi-rute"]).toBeTruthy();
    expect(byPath["/penjualan/data-pelanggan"]).toBeTruthy();
  });

  test("sales outlet detail aliases Aktivitas Sales", () => {
    const a = openOrActivateGlobalTab([], "/penjualan/aktivitas-sales");
    const b = openOrActivateGlobalTab(a.tabs, "/penjualan/aktivitas-sales/outlet/abc");
    expect(b.tabs).toHaveLength(1);
    expect(b.activated.id).toBe("aktivitas-sales");
    expect(globalTabByPath("/penjualan/aktivitas-sales/outlet/abc").id).toBe("aktivitas-sales");
  });

  test("proven delivery-schedule and route-optimizer aliases map to parent tabs", () => {
    expect(globalTabByPath("/produksi/jadwal-pengiriman/batch/b1").path).toBe("/produksi/jadwal-pengiriman");
    expect(globalTabByPath("/optimasi-rute/baru").id).toBe("optimasi-rute");
    expect(globalTabByPath("/optimasi-rute/xyz").id).toBe("optimasi-rute");
  });

  test("Kas & Bank and Penjualan catalogs still participate without query duplicates", () => {
    const a = openOrActivateGlobalTab([], "/kas-bank/daftar");
    const b = openOrActivateGlobalTab(a.tabs, "/penjualan/faktur-penjualan?po_id=1");
    const c = openOrActivateGlobalTab(b.tabs, "/kas-bank/daftar?q=1");
    expect(c.tabs.map((t) => t.path)).toEqual(["/kas-bank/daftar", "/penjualan/faktur-penjualan"]);
  });

  test("visible catalog follows hasPerm and does not invent parent-group tabs", () => {
    const limited = visibleGlobalWorkspaceCatalog((key) => key === NAV_KEYS.dashboard || key === NAV_KEYS.settings);
    expect(limited.map((t) => t.id).sort()).toEqual(["dashboard", "settings"]);
    expect(GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/penjualan")).toBeFalsy();
    expect(GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/kas-bank")).toBeFalsy();
  });
});
