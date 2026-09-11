import {
  MOBILE_OPS_SHELL_KEY,
  markMobileOpsShell,
  navigateToOptimasiRute,
  optimasiRutePath,
  preferMobileOpsShell,
} from "./mobileOpsShell";

describe("mobileOpsShell", () => {
  test("optimasiRutePath is relative in-app path", () => {
    expect(optimasiRutePath("iro-1")).toBe("/optimasi-rute/iro-1");
    expect(optimasiRutePath("a/b")).toBe("/optimasi-rute/a%2Fb");
    expect(optimasiRutePath("")).toBe("/optimasi-rute");
  });

  test("navigateToOptimasiRute uses router navigate, never window.open", () => {
    const navigate = jest.fn();
    const open = jest.fn();
    const orig = window.open;
    window.open = open;
    navigateToOptimasiRute(navigate, "iro-9", { from: "/m/delivery-schedule" });
    expect(navigate).toHaveBeenCalledWith("/optimasi-rute/iro-9", { state: { from: "/m/delivery-schedule" } });
    expect(open).not.toHaveBeenCalled();
    window.open = orig;
  });

  test("preferMobileOpsShell follows standalone or session mark, not role name", () => {
    const storage = {
      store: {},
      getItem(k) { return this.store[k] ?? null; },
      setItem(k, v) { this.store[k] = String(v); },
    };
    expect(preferMobileOpsShell({ matchMedia: () => ({ matches: false }), navigator: {} }, storage)).toBe(false);
    markMobileOpsShell(storage);
    expect(storage.getItem(MOBILE_OPS_SHELL_KEY)).toBe("1");
    expect(preferMobileOpsShell({ matchMedia: () => ({ matches: false }), navigator: {} }, storage)).toBe(true);
    expect(preferMobileOpsShell({
      matchMedia: (q) => ({ matches: q.includes("standalone") }),
      navigator: {},
    }, { getItem: () => null })).toBe(true);
  });
});
