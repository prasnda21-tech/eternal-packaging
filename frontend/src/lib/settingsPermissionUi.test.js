import {
  SIDEBAR_NAV_MODULE,
  businessPermissionModules,
  isSidebarNavPermissionKey,
  roleBusinessSavePayload,
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

  test("businessPermissionModules drops Akses Menu and leaked nav.* keys", () => {
    const mods = businessPermissionModules(catalog);
    const names = mods.map(([m]) => m);
    expect(names).not.toContain("Akses Menu");
    expect(names).toEqual(expect.arrayContaining(["Produksi", "Route Optimizer", "Sales"]));
    const keys = mods.flatMap(([, ps]) => ps.map((p) => p.key));
    expect(keys).toEqual(["schedule.view", "schedule.edit", "route_optimizer.view", "sales_activity.view"]);
    expect(keys).not.toContain("nav.route_optimizer");
    expect(keys).not.toContain("nav.production");
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
  });
});
