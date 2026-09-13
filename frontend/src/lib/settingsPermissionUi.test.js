import {
  SIDEBAR_NAV_MODULE,
  LOADING_GRANULAR_CONFIGURED,
  businessPermissionModules,
  isSidebarNavPermissionKey,
  expandLoadingDraftForEdit,
  roleBusinessSavePayload,
  isSensitivePermissionKey,
  filterRolePermissionModules,
  countRolePermissionTotals,
  countRolePermissionDirty,
} from "./settingsPermissionUi";

const catalog = {
  modules: {
    "Akses Menu": [
      { key: "nav.route_optimizer", label: "Menu Optimasi Rute" },
      { key: "nav.configured", label: "Akses menu sudah dikonfigurasi" },
    ],
    Produksi: [
      { key: "schedule.view", label: "Lihat jadwal" },
      { key: "schedule.edit", label: "Ubah jadwal" },
      { key: "nav.production", label: "should not leak" },
    ],
    Loading: [
      { key: "loading.update", label: "Konfirmasi Loading" },
      { key: "loading.confirm_match", label: "Konfirmasi Barang Sesuai SJ" },
      { key: "loading.granular_configured", label: "Hak akses Loading granular sudah dikonfigurasi" },
    ],
    "Route Optimizer": [
      { key: "route_optimizer.view", label: "Lihat optimasi rute" },
    ],
    Sales: [
      { key: "sales_activity.view", label: "Lihat aktivitas sales" },
    ],
  },
};

describe("settingsPermissionUi — hide sidebar nav from generic editors", () => {
  test("identifies only nav.* keys as sidebar visibility", () => {
    expect(isSidebarNavPermissionKey("nav.route_optimizer")).toBe(true);
    expect(isSidebarNavPermissionKey("nav.configured")).toBe(true);
    expect(isSidebarNavPermissionKey("route_optimizer.view")).toBe(false);
    expect(isSidebarNavPermissionKey("sales_activity.view")).toBe(false);
    expect(isSidebarNavPermissionKey("payroll.approve")).toBe(false);
    expect(SIDEBAR_NAV_MODULE).toBe("Akses Menu");
  });

  test("businessPermissionModules drops Akses Menu, leaked nav.*, and loading sentinel", () => {
    const mods = businessPermissionModules(catalog);
    const names = mods.map(([m]) => m);
    expect(names).not.toContain("Akses Menu");
    expect(names).toEqual(expect.arrayContaining(["Produksi", "Loading", "Route Optimizer", "Sales"]));
    const keys = mods.flatMap(([, ps]) => ps.map((p) => p.key));
    expect(keys).toEqual([
      "schedule.view",
      "schedule.edit",
      "loading.update",
      "loading.confirm_match",
      "route_optimizer.view",
      "sales_activity.view",
    ]);
    expect(keys).not.toContain("nav.route_optimizer");
    expect(keys).not.toContain("nav.production");
    expect(keys).not.toContain(LOADING_GRANULAR_CONFIGURED);
  });

  test("search still finds business permissions only", () => {
    const mods = businessPermissionModules(catalog, "rute");
    expect(mods.map(([m]) => m)).toEqual(["Route Optimizer"]);
    expect(mods[0][1].map((p) => p.key)).toEqual(["route_optimizer.view"]);
  });

  test("role save keeps stored nav.* and does not invent grants", () => {
    const payload = roleBusinessSavePayload(
      ["schedule.view", "nav.dashboard"],
      ["nav.route_optimizer", "nav.configured", "schedule.edit"]
    );
    expect(payload).toContain("schedule.view");
    expect(payload).toContain("nav.route_optimizer");
    expect(payload).toContain("nav.configured");
    expect(payload).not.toContain("nav.dashboard");
    expect(payload.filter((k) => k === "nav.configured")).toHaveLength(1);
    expect(payload).not.toContain(LOADING_GRANULAR_CONFIGURED);
  });

  test("role save stamps loading.granular_configured when loading keys present", () => {
    const payload = roleBusinessSavePayload(
      ["loading.update", "loading.confirm_match"],
      ["loading.update", "nav.tasks.loading", "nav.configured"]
    );
    expect(payload).toContain("loading.update");
    expect(payload).toContain("loading.confirm_match");
    expect(payload).toContain(LOADING_GRANULAR_CONFIGURED);
    expect(payload.filter((k) => k === LOADING_GRANULAR_CONFIGURED)).toHaveLength(1);
  });

  test("role save with confirm_match unchecked does not re-add it from loading.update", () => {
    const payload = roleBusinessSavePayload(
      ["loading.update"],
      ["loading.update", "loading.confirm_match", LOADING_GRANULAR_CONFIGURED]
    );
    expect(payload).toContain("loading.update");
    expect(payload).not.toContain("loading.confirm_match");
    expect(payload).toContain(LOADING_GRANULAR_CONFIGURED);
  });

  test("expandLoadingDraftForEdit shows compat children before configure stamp", () => {
    const draft = expandLoadingDraftForEdit(["loading.update", "po.view"]);
    expect(draft.has("loading.confirm_match")).toBe(true);
    expect(draft.has("loading.report_discrepancy")).toBe(true);
    expect(draft.has(LOADING_GRANULAR_CONFIGURED)).toBe(false);
  });

  test("sensitive marker is an explicit presentational allowlist", () => {
    expect(isSensitivePermissionKey("po.delete")).toBe(true);
    expect(isSensitivePermissionKey("po.cancel")).toBe(true);
    expect(isSensitivePermissionKey("po.view")).toBe(false);
    expect(isSensitivePermissionKey("sales.customer.manage")).toBe(false);
    expect(isSensitivePermissionKey("users.manage")).toBe(false);
  });

  test("filterRolePermissionModules search + Aktif/Tidak Aktif work together", () => {
    const mods = businessPermissionModules(catalog);
    const draft = new Set(["schedule.view", "route_optimizer.view"]);
    const byKey = filterRolePermissionModules(mods, { query: "schedule.edit", stateFilter: "all", draftSet: draft });
    expect(byKey).toEqual([["Produksi", [{ key: "schedule.edit", label: "Ubah jadwal" }]]]);
    const active = filterRolePermissionModules(mods, { query: "", stateFilter: "active", draftSet: draft });
    expect(active.flatMap(([, ps]) => ps.map((p) => p.key))).toEqual(["schedule.view", "route_optimizer.view"]);
    const inactiveRute = filterRolePermissionModules(mods, { query: "rute", stateFilter: "inactive", draftSet: draft });
    expect(inactiveRute).toEqual([]);
    const byCat = filterRolePermissionModules(mods, { query: "loading", stateFilter: "all", draftSet: draft });
    expect(byCat.map(([m]) => m)).toEqual(["Loading"]);
  });

  test("count helpers stay frontend-only and ignore nav keys in dirty count", () => {
    const mods = businessPermissionModules(catalog);
    expect(countRolePermissionTotals(mods, new Set(["schedule.view"]))).toEqual({ active: 1, total: 6 });
    expect(countRolePermissionDirty(
      new Set(["schedule.view", "nav.dashboard"]),
      new Set(["schedule.view", "nav.route_optimizer"])
    )).toBe(0);
    expect(countRolePermissionDirty(new Set(["schedule.view", "schedule.edit"]), new Set(["schedule.view"]))).toBe(1);
  });

  test("expandLoadingDraftForEdit does not invent children after configure stamp", () => {
    const draft = expandLoadingDraftForEdit([
      "loading.update",
      LOADING_GRANULAR_CONFIGURED,
    ]);
    expect(draft.has("loading.confirm_match")).toBe(false);
    expect(draft.has("loading.update")).toBe(true);
    expect(draft.has(LOADING_GRANULAR_CONFIGURED)).toBe(false);
  });
});
