import { TABS, WIDGET_IDS } from "./widgetDashboardCatalog";
import {
  legacyHashTab,
  resolveWidgetLocation,
  widgetDashboardAliasTarget,
  widgetDashboardFinanceTarget,
} from "./widgetDashboardLocation";

const allow = (...keys) => (key) => keys.includes(key);

describe("widget dashboard route selection", () => {
  test("keeps three tabs, five panels, and twenty widgets", () => {
    expect(TABS.map((tab) => tab.id)).toEqual(["penjualan", "operasi", "keuangan"]);
    expect(TABS.flatMap((tab) => tab.panels)).toHaveLength(5);
    expect(WIDGET_IDS).toHaveLength(20);
  });

  test("alias replace target is the primary route and cannot point at itself", () => {
    const target = widgetDashboardAliasTarget({
      pathname: "/dashboard/widget",
      search: "?tab=operasi&panel=jadwal",
      hash: "#penjualan",
    });
    expect(target).toEqual({
      pathname: "/dashboard/penjualan",
      search: "?tab=operasi&panel=jadwal",
      hash: "#penjualan",
    });
    expect(target.pathname).not.toBe("/dashboard/widget");
    expect(widgetDashboardAliasTarget({ pathname: "/dashboard/widget" }).pathname).toBe("/dashboard/penjualan");
  });

  test("finance bookmark keeps its query and forces the keuangan hash", () => {
    expect(widgetDashboardFinanceTarget({ search: "?preset=month" })).toEqual({
      pathname: "/dashboard/penjualan",
      search: "?preset=month",
      hash: "#keuangan",
    });
  });

  test("legacy hashes select penjualan and keuangan only", () => {
    expect(legacyHashTab("#penjualan")).toBe("penjualan");
    expect(legacyHashTab("#keuangan")).toBe("keuangan");
    expect(legacyHashTab("#operasi")).toBe("");
    expect(legacyHashTab("")).toBe("");
  });

  test("a sales user opening the menu lands on penjualan", () => {
    const view = resolveWidgetLocation({
      search: "",
      hash: "",
      user: { role: "sales" },
      hasPerm: allow("dashboard.sales.view"),
    });
    expect(view.tab.id).toBe("penjualan");
    expect(view.panel.id).toBe("ringkasan");
    expect(view.widgetId).toBe("omzet");
  });

  test("legacy hashes survive as the selected tab when that panel is allowed", () => {
    const penjualan = resolveWidgetLocation({
      search: "",
      hash: "#penjualan",
      user: { role: "sales" },
      hasPerm: allow("dashboard.sales.view"),
    });
    const keuangan = resolveWidgetLocation({
      search: "",
      hash: "#keuangan",
      user: { role: "finance" },
      hasPerm: allow("dashboard.finance.view"),
    });
    expect(penjualan.tab.id).toBe("penjualan");
    expect(keuangan.tab.id).toBe("keuangan");
    expect(keuangan.panel.id).toBe("ringkasan");
    expect(keuangan.widgetId).toBe("cash");
  });

  test("an inaccessible initial panel moves to the first allowed panel", () => {
    const ops = resolveWidgetLocation({
      search: "",
      hash: "",
      user: { role: "ops" },
      hasPerm: allow("po.view"),
    });
    expect(ops.tab.id).toBe("operasi");
    expect(ops.widgetId).toBe("shipToday");
    const financeHashForSales = resolveWidgetLocation({
      search: "",
      hash: "#keuangan",
      user: { role: "sales" },
      hasPerm: allow("dashboard.sales.view"),
    });
    expect(financeHashForSales.tab.id).toBe("penjualan");
    const admin = resolveWidgetLocation({
      search: "",
      hash: "#penjualan",
      user: { role: "admin" },
      hasPerm: allow(),
    });
    expect(admin.tab.id).toBe("operasi");
    expect(admin.widgetId).toBe("shipLate");
  });

  test("an explicit tab stays even when that panel is forbidden", () => {
    const view = resolveWidgetLocation({
      search: "?tab=penjualan&panel=ringkasan",
      hash: "#keuangan",
      user: { role: "ops" },
      hasPerm: allow("po.view"),
    });
    expect(view.explicitTab).toBe(true);
    expect(view.tab.id).toBe("penjualan");
    expect(view.panel.id).toBe("ringkasan");
  });

  test("the same location resolves the same tab after refresh", () => {
    const location = { search: "", hash: "#keuangan", user: { role: "finance" }, hasPerm: allow("dashboard.finance.view") };
    expect(resolveWidgetLocation(location).tab.id).toBe(resolveWidgetLocation(location).tab.id);
    expect(resolveWidgetLocation(location).tab.id).toBe("keuangan");
  });
});
