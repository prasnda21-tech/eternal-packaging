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
    expect(globalTabByPath("/administrasi-karyawan/pengajuan/99")).toBe(null);
    expect(globalTabByPath("/po/abc/kerja")).toBe(null);
    expect(globalTabByPath("/not-a-sidebar-page")).toBe(null);
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
    expect(byPath["/spk"]).toMatchObject({ id: "spk", label: "SPK", matchPrefix: true });
  });

  test("SPK list, create, and detail share one history tab", () => {
    const list = openOrActivateGlobalTab([], "/spk");
    const create = openOrActivateGlobalTab(list.tabs, "/spk/baru");
    const detail = openOrActivateGlobalTab(create.tabs, "/spk/ord-1");
    expect(detail.tabs).toHaveLength(1);
    expect(detail.activated).toMatchObject({ id: "spk", path: "/spk", label: "SPK" });
    expect(globalTabByPath("/spk/baru").id).toBe("spk");
    const hidden = visibleGlobalWorkspaceCatalog((key) => key === NAV_KEYS.dashboard);
    expect(hidden.find((tab) => tab.id === "spk")).toBeUndefined();
    expect(openOrActivateGlobalTab([], "/spk/baru", (key) => key === NAV_KEYS.dashboard).activated).toBe(null);
  });

  test("product request list, form, and detail share one workspace tab", () => {
    const list = openOrActivateGlobalTab([], "/penjualan/permintaan-produk");
    const form = openOrActivateGlobalTab(list.tabs, "/penjualan/permintaan-produk/baru");
    const detail = openOrActivateGlobalTab(form.tabs, "/penjualan/permintaan-produk/req-1");
    expect(detail.tabs).toHaveLength(1);
    expect(detail.activated.id).toBe("penjualan-permintaan-produk");
    expect(detail.activated.path).toBe("/penjualan/permintaan-produk/req-1");
    expect(globalTabByPath("/penjualan/permintaan-produk/baru").id).toBe("penjualan-permintaan-produk");
    expect(globalTabByPath("/penjualan/pesanan-penjualan").id).not.toBe("penjualan-permintaan-produk");
  });

  test("crm entry path still opens the product request tab on detail", () => {
    const crm = (key) => [
      "nav.sales",
      "nav.sales.product_requests",
      "product_request.create",
      "product_request.read_own_draft",
      "nav.sales.sales_order",
    ].includes(key);
    const opened = openOrActivateGlobalTab([], "/penjualan/permintaan-produk/req-9", crm);
    expect(opened.activated.id).toBe("penjualan-permintaan-produk");
    expect(opened.activated.path).toBe("/penjualan/permintaan-produk/req-9");
    const neighbor = openOrActivateGlobalTab(opened.tabs, "/penjualan/pesanan-penjualan", crm);
    expect(neighbor.tabs.map((t) => t.id)).toEqual(["penjualan-permintaan-produk", "pesanan-penjualan"]);
    expect(neighbor.tabs.find((t) => t.id === "pesanan-penjualan").path).toBe("/penjualan/pesanan-penjualan");
  });

  test("rememberPath stays on product requests and does not retarget other tabs", () => {
    const sales = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    expect(sales.activated.path).toBe("/penjualan/pesanan-penjualan");
    expect(sales.activated.rememberPath).toBeUndefined();
    const aktivitas = openOrActivateGlobalTab([], "/penjualan/aktivitas-sales/outlet/abc");
    expect(aktivitas.activated.id).toBe("aktivitas-sales");
    expect(aktivitas.activated.path).toBe("/penjualan/aktivitas-sales");

    const draft = openOrActivateGlobalTab([], "/penjualan/permintaan-produk/req-1");
    const withSales = openOrActivateGlobalTab(draft.tabs, "/penjualan/pesanan-penjualan");
    const back = openOrActivateGlobalTab(withSales.tabs, "/penjualan/permintaan-produk/req-1");
    expect(back.tabs.find((t) => t.id === "penjualan-permintaan-produk").path).toBe("/penjualan/permintaan-produk/req-1");
    expect(back.tabs.find((t) => t.id === "pesanan-penjualan").path).toBe("/penjualan/pesanan-penjualan");
    expect(back.activated.path).not.toBe("/penjualan/permintaan-produk/baru");

    const user = { id: "remember-path-user" };
    sessionStorage.clear();
    saveGlobalWorkspaceTabs(user, back.tabs);
    const loaded = loadGlobalWorkspaceTabs(user);
    expect(loaded.find((t) => t.id === "penjualan-permintaan-produk").path).toBe("/penjualan/permintaan-produk/req-1");
    expect(loaded.find((t) => t.id === "pesanan-penjualan").path).toBe("/penjualan/pesanan-penjualan");
  });

  test("SPK and product request tabs keep separate paths", () => {
    const catalog = Object.fromEntries(GLOBAL_WORKSPACE_TABS.map((tab) => [tab.id, tab]));
    expect(catalog.spk).toMatchObject({ id: "spk", path: "/spk", label: "SPK", matchPrefix: true });
    expect(catalog["penjualan-permintaan-produk"].id).toBe("penjualan-permintaan-produk");
    expect(catalog.spk.id).not.toBe(catalog["penjualan-permintaan-produk"].id);

    const draft = openOrActivateGlobalTab([], "/penjualan/permintaan-produk/req-1");
    const spk = openOrActivateGlobalTab(draft.tabs, "/spk");
    expect(spk.activated).toMatchObject({ id: "spk", path: "/spk" });
    expect(spk.tabs.find((tab) => tab.id === "penjualan-permintaan-produk").path).toBe("/penjualan/permintaan-produk/req-1");
    const child = openOrActivateGlobalTab(spk.tabs, "/spk/baru");
    expect(child.activated.path).toBe("/spk");
    expect(child.tabs.find((tab) => tab.id === "penjualan-permintaan-produk").path).toBe("/penjualan/permintaan-produk/req-1");
    const back = openOrActivateGlobalTab(child.tabs, "/penjualan/permintaan-produk/req-1");
    expect(back.activated.path).toBe("/penjualan/permintaan-produk/req-1");
    expect(back.tabs.find((tab) => tab.id === "spk").path).toBe("/spk");
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

  test("settings children are distinct tabs and query does not duplicate", () => {
    const a = openOrActivateGlobalTab([], "/laporan/analitik-output");
    expect(a.tabs.map((t) => t.id)).not.toContain("settings");
    const b = openOrActivateGlobalTab(a.tabs, "/settings/sales-accounting");
    const c = openOrActivateGlobalTab(b.tabs, "/settings/payroll-accounting");
    const d = openOrActivateGlobalTab(c.tabs, "/settings/sales-accounting?x=1");
    expect(d.tabs.map((t) => t.path)).toEqual([
      "/laporan/analitik-output",
      "/settings/sales-accounting",
      "/settings/payroll-accounting",
    ]);
    expect(d.activated.path).toBe("/settings/sales-accounting");
    expect(d.tabs.filter((t) => t.label === "Pengaturan")).toHaveLength(0);
    expect(globalTabByPath("/settings?tab=sales-accounting").path).toBe("/settings");
    expect(globalTabByPath("/settings/sales-accounting").label).toBe("Akuntansi Penjualan");
  });

  test("unauthorized settings child is hidden from restore", () => {
    saveGlobalWorkspaceTabs(user, [
      GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/settings/sales-accounting"),
      GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/settings/payroll-accounting"),
    ]);
    const onlySales = (key) => key === NAV_KEYS.settings || key === "settings.sales_accounting.view";
    expect(loadGlobalWorkspaceTabs(user, onlySales).map((t) => t.path)).toEqual(["/settings/sales-accounting"]);
    expect(openOrActivateGlobalTab([], "/settings/payroll-accounting", onlySales).activated).toBe(null);
  });

  test("CASE 1 — Lihat PO A opens a PO workspace tab with customer label", () => {
    const list = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    const r = openOrActivateGlobalTab(list.tabs, "/po/ord-a", () => true, {
      po_number: "PO/2026/09/09/000018",
      customer: "WELCOME TO PLEASURE",
    });
    expect(r.activated.id).toBe("po-detail:ord-a");
    expect(r.activated.path).toBe("/po/ord-a");
    expect(r.activated.label).toBe("WELCOME TO PLEASURE");
    expect(r.tabs.map((t) => t.id)).toEqual(["pesanan-penjualan", "po-detail:ord-a"]);
  });

  test("CASE 2 — second PO adds a tab and becomes active", () => {
    let r = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, {
      po_number: "PO/2026/09/09/000018",
      customer: "WELCOME TO PLEASURE",
    });
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-b", () => true, {
      po_number: "PO/2026/09/09/000017",
      customer: "PT ABC",
    });
    expect(r.tabs.map((t) => t.id)).toEqual(["pesanan-penjualan", "po-detail:ord-a", "po-detail:ord-b"]);
    expect(r.activated.id).toBe("po-detail:ord-b");
    expect(r.activated.label).toBe("PT ABC");
  });

  test("CASE 3 — third PO coexists with A and B", () => {
    let r = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-b");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-c");
    expect(r.tabs.map((t) => t.id)).toEqual([
      "pesanan-penjualan",
      "po-detail:ord-a",
      "po-detail:ord-b",
      "po-detail:ord-c",
    ]);
  });

  test("CASE 4 — opening the same PO focuses existing tab without duplicate", () => {
    let r = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, {
      po_number: "PO/2026/09/09/000018",
      customer: "WELCOME TO PLEASURE",
    });
    r = openOrActivateGlobalTab(r.tabs, "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, {
      po_number: "PO/2026/09/09/000018",
      customer: "WELCOME TO PLEASURE",
    });
    expect(r.tabs.filter((t) => t.id === "po-detail:ord-a")).toHaveLength(1);
    expect(r.activated.id).toBe("po-detail:ord-a");
    expect(r.activated.label).toBe("WELCOME TO PLEASURE");
    expect(r.tabs.map((t) => t.id)).toEqual(["pesanan-penjualan", "po-detail:ord-a"]);
  });

  test("CASE 5 — switching tabs uses exact order identity", () => {
    let r = openOrActivateGlobalTab([], "/po/ord-a");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-b");
    const a = openOrActivateGlobalTab(r.tabs, "/po/ord-a");
    expect(a.activated.path).toBe("/po/ord-a");
    expect(a.activated.id).toBe("po-detail:ord-a");
    const b = openOrActivateGlobalTab(a.tabs, "/po/ord-b");
    expect(b.activated.path).toBe("/po/ord-b");
    expect(b.activated.id).toBe("po-detail:ord-b");
  });

  test("CASE 6 — close inactive PO removes only that tab", () => {
    let r = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-b");
    const closed = closeGlobalTab(r.tabs, "po-detail:ord-a", "/po/ord-b");
    expect(closed.tabs.map((t) => t.id)).toEqual(["pesanan-penjualan", "po-detail:ord-b"]);
    expect(closed.navigateTo).toBe(null);
  });

  test("CASE 7 — close active PO uses existing neighbor fallback", () => {
    let r = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-b");
    const closed = closeGlobalTab(r.tabs, "po-detail:ord-b", "/po/ord-b");
    expect(closed.tabs.map((t) => t.id)).toEqual(["pesanan-penjualan", "po-detail:ord-a"]);
    expect(closed.navigateTo).toBe("/po/ord-a");
  });

  test("CASE 8 — same customer different order_id stay two tabs with same visible label", () => {
    const a = openOrActivateGlobalTab([], "/po/ord-a", () => true, {
      po_number: "PO/2026/09/09/000018",
      customer: "WELCOME TO PLEASURE",
    });
    const b = openOrActivateGlobalTab(a.tabs, "/po/ord-b", () => true, {
      po_number: "PO/2026/09/09/000017",
      customer: "WELCOME TO PLEASURE",
    });
    expect(b.tabs).toHaveLength(2);
    expect(b.tabs[0].id).not.toBe(b.tabs[1].id);
    expect(b.tabs.map((t) => t.label)).toEqual(["WELCOME TO PLEASURE", "WELCOME TO PLEASURE"]);
    expect(b.tabs.map((t) => t.path).sort()).toEqual(["/po/ord-a", "/po/ord-b"]);
  });

  test("CASE 9 — compact PO label from slash serial when customer missing", () => {
    const r = openOrActivateGlobalTab([], "/po/ord-a", () => true, { po_number: "PO/2026/09/09/000018" });
    expect(r.activated.label).toBe("PO 000018");
    const full = openOrActivateGlobalTab([], "/po/ord-x", () => true, { po_number: "TASK-SCREEN-SIZE-01" });
    expect(full.activated.label).toBe("TASK-SCREEN-SIZE-01");
  });

  test("CASE 10 — Pesanan Penjualan tab is not duplicated when opening POs", () => {
    let r = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a");
    r = openOrActivateGlobalTab(r.tabs, "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-b");
    r = openOrActivateGlobalTab(r.tabs, "/penjualan/pesanan-penjualan");
    expect(r.tabs.filter((t) => t.id === "pesanan-penjualan")).toHaveLength(1);
    expect(r.activated.id).toBe("pesanan-penjualan");
  });

  test("CASE 12 — returning to a PO tab with null location.state preserves customer label", () => {
    let r = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, {
      po_number: "PO/2026/09/09/000018",
      customer: "WELCOME TO PLEASURE",
    });
    r = openOrActivateGlobalTab(r.tabs, "/penjualan/pesanan-penjualan");
    expect(() => openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, null)).not.toThrow();
    const back = openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, null);
    expect(back.activated.id).toBe("po-detail:ord-a");
    expect(back.activated.path).toBe("/po/ord-a");
    expect(back.activated.label).toBe("WELCOME TO PLEASURE");
    expect(back.tabs.filter((t) => t.id === "po-detail:ord-a")).toHaveLength(1);
  });

  test("CASE 11 — direct /po/:id route opens a tab and nested kerja does not", () => {
    const direct = openOrActivateGlobalTab([], "/po/ord-a");
    expect(direct.activated.path).toBe("/po/ord-a");
    expect(direct.activated.label).toBe("PO ord-a");
    expect(globalTabByPath("/po/ord-a").id).toBe("po-detail:ord-a");
    const nested = openOrActivateGlobalTab(direct.tabs, "/po/ord-a/kerja");
    expect(nested.activated).toBe(null);
    expect(nested.tabs.map((t) => t.id)).toEqual(["po-detail:ord-a"]);
  });

  test("CASE 6 — catalog Dashboard / Pesanan Penjualan / Jadwal Produksi stay distinct", () => {
    let r = openOrActivateGlobalTab([], "/dashboard");
    r = openOrActivateGlobalTab(r.tabs, "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/produksi");
    expect(r.tabs.map((t) => t.id)).toEqual(["dashboard", "pesanan-penjualan", "produksi"]);
    expect(r.activated.path).toBe("/produksi");
  });

  test("PO workspace tabs persist customer label in sessionStorage", () => {
    let r = openOrActivateGlobalTab([], "/penjualan/pesanan-penjualan");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, {
      po_number: "PO/2026/09/09/000018",
      customer: "WELCOME TO PLEASURE",
    });
    saveGlobalWorkspaceTabs(user, r.tabs);
    const restored = loadGlobalWorkspaceTabs(user);
    expect(restored.map((t) => t.id)).toEqual(["pesanan-penjualan", "po-detail:ord-a"]);
    expect(restored[1].label).toBe("WELCOME TO PLEASURE");
    expect(restored[1].path).toBe("/po/ord-a");
    expect(restored[1].labelPriority).toBe(2);
  });

  test("customer enrichment upgrades bare PO fallback; po_number alone does not downgrade", () => {
    let r = openOrActivateGlobalTab([], "/po/ord-a");
    expect(r.activated.label).toBe("PO ord-a");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, {
      po_number: "PO/2026/09/09/000018",
      customer: "WELCOME TO PLEASURE",
    });
    expect(r.activated.label).toBe("WELCOME TO PLEASURE");
    r = openOrActivateGlobalTab(r.tabs, "/po/ord-a", () => true, { po_number: "PO/2026/09/09/000018" });
    expect(r.activated.label).toBe("WELCOME TO PLEASURE");
  });
});
