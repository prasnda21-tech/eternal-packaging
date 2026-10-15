/**
 * @jest-environment jsdom
 */
import {
  createWorkspaceTabApi,
  normalizeWorkspaceSearch,
  workspaceTabHref,
} from "./workspaceTabs";

const CATALOG = [
  { id: "pengiriman-pesanan", path: "/penjualan/pengiriman-pesanan", label: "Pengiriman Pesanan" },
  {
    id: "faktur-penjualan",
    path: "/penjualan/faktur-penjualan",
    label: "Faktur Penjualan",
    rememberSearch: true,
  },
  { id: "dashboard", path: "/dashboard", label: "Dashboard" },
];

describe("workspaceTabs rememberSearch", () => {
  let api;
  let store;

  beforeEach(() => {
    store = {};
    global.sessionStorage = {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
      clear: () => { store = {}; },
      key: (i) => Object.keys(store)[i] || null,
      get length() { return Object.keys(store).length; },
    };
    api = createWorkspaceTabApi({
      catalog: CATALOG,
      storagePrefix: "ep.test.workspaceTabs.",
    });
  });

  test("normalizeWorkspaceSearch and workspaceTabHref", () => {
    expect(normalizeWorkspaceSearch("tab=pending")).toBe("?tab=pending");
    expect(normalizeWorkspaceSearch("?q=ABC&tab=pending")).toBe("?q=ABC&tab=pending");
    expect(normalizeWorkspaceSearch("")).toBe("");
    expect(workspaceTabHref({
      path: "/penjualan/faktur-penjualan",
      rememberSearch: true,
      search: "?tab=pending",
    })).toBe("/penjualan/faktur-penjualan?tab=pending");
    expect(workspaceTabHref({ path: "/dashboard" })).toBe("/dashboard");
  });

  test("stores and restores Faktur query without affecting other tabs", () => {
    const user = { id: "u1" };
    let tabs = api.openOrActivate([], "/penjualan/faktur-penjualan", null, "?tab=pending&q=kopi&page=2").tabs;
    tabs = api.openOrActivate(tabs, "/penjualan/pengiriman-pesanan", null, "").tabs;
    expect(tabs.find((t) => t.id === "faktur-penjualan").search).toBe("?tab=pending&q=kopi&page=2");
    expect(tabs.find((t) => t.id === "pengiriman-pesanan").search).toBeUndefined();

    api.save(user, tabs);
    const restored = api.load(user);
    expect(restored.find((t) => t.id === "faktur-penjualan").search).toBe("?tab=pending&q=kopi&page=2");
    expect(workspaceTabHref(restored.find((t) => t.id === "faktur-penjualan")))
      .toBe("/penjualan/faktur-penjualan?tab=pending&q=kopi&page=2");
  });

  test("enricher without search argument does not wipe remembered query", () => {
    let tabs = api.openOrActivate([], "/penjualan/faktur-penjualan", null, "?tab=pending").tabs;
    const mid = api.openOrActivate(tabs, "/penjualan/faktur-penjualan", { customer: "X" });
    expect(mid.activated.search).toBe("?tab=pending");
  });

  test("closing Faktur discards remembered search; neighbor href keeps own search", () => {
    let tabs = api.openOrActivate([], "/penjualan/faktur-penjualan", null, "?tab=pending").tabs;
    tabs = api.openOrActivate(tabs, "/penjualan/pengiriman-pesanan", null, "").tabs;
    const closed = api.closeTab(tabs, "faktur-penjualan", "/penjualan/faktur-penjualan");
    expect(closed.tabs.find((t) => t.id === "faktur-penjualan")).toBeFalsy();
    expect(closed.navigateTo).toBe("/penjualan/pengiriman-pesanan");

    // Reopen without query → fresh, no leaked search
    const reopened = api.openOrActivate(closed.tabs, "/penjualan/faktur-penjualan", null, "");
    expect(reopened.activated.search).toBeUndefined();
  });

  test("clear removes persisted rememberSearch for the user", () => {
    const user = { id: "u1" };
    const tabs = api.openOrActivate([], "/penjualan/faktur-penjualan", null, "?tab=pending").tabs;
    api.save(user, tabs);
    api.clear(user);
    expect(api.load(user)).toEqual([]);
  });

  test("pathname query string is adopted when search arg omitted (compat)", () => {
    const res = api.openOrActivate([], "/penjualan/faktur-penjualan?tab=completed&page=3");
    expect(res.activated.search).toBe("?tab=completed&page=3");
  });
});
