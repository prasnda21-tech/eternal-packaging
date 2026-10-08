import {
  clearCashBankWorkspaceTabs,
  closeCashBankTab,
  loadCashBankWorkspaceTabs,
  openOrActivateCashBankTab,
  saveCashBankWorkspaceTabs,
  cashBankWorkspaceStorageKey,
  CASH_BANK_WORKSPACE_TABS,
} from "./cashBankWorkspace";

describe("cashBankWorkspace tabs", () => {
  const user = { id: "u-1", email: "ops@eternal.test" };

  beforeEach(() => {
    sessionStorage.clear();
  });

  test("catalog has Kas & Bank menus including owner recon", () => {
    expect(CASH_BANK_WORKSPACE_TABS.map((t) => t.path)).toEqual([
      "/kas-bank/daftar",
      "/kas-bank/penerimaan",
      "/kas-bank/pembayaran",
      "/kas-bank/transfer",
      "/kas-bank/mutasi",
      "/kas-bank/rekonsiliasi",
      "/kas-bank/rekonsiliasi-owner",
    ]);
  });

  test("openOrActivate does not duplicate", () => {
    const a = openOrActivateCashBankTab([], "/kas-bank/daftar");
    expect(a.tabs).toHaveLength(1);
    const b = openOrActivateCashBankTab(a.tabs, "/kas-bank/daftar");
    expect(b.tabs).toHaveLength(1);
    expect(b.activated.id).toBe("daftar");
    const c = openOrActivateCashBankTab(b.tabs, "/kas-bank/penerimaan");
    expect(c.tabs.map((t) => t.id)).toEqual(["daftar", "penerimaan"]);
  });

  test("close active moves to nearest neighbor", () => {
    const tabs = CASH_BANK_WORKSPACE_TABS.slice(0, 3);
    const mid = closeCashBankTab(tabs, "penerimaan", "/kas-bank/penerimaan");
    expect(mid.tabs.map((t) => t.id)).toEqual(["daftar", "pembayaran"]);
    expect(mid.navigateTo).toBe("/kas-bank/pembayaran");

    const last = closeCashBankTab(tabs, "pembayaran", "/kas-bank/pembayaran");
    expect(last.navigateTo).toBe("/kas-bank/penerimaan");
  });

  test("close last active navigates home", () => {
    const tabs = [CASH_BANK_WORKSPACE_TABS[0]];
    const r = closeCashBankTab(tabs, "daftar", "/kas-bank/daftar");
    expect(r.tabs).toEqual([]);
    expect(r.navigateTo).toBe("/");
  });

  test("sessionStorage restore is user-scoped", () => {
    saveCashBankWorkspaceTabs(user, CASH_BANK_WORKSPACE_TABS.slice(0, 2));
    expect(cashBankWorkspaceStorageKey(user)).toMatch(/ep\.cashBank\.workspaceTabs\./);
    expect(loadCashBankWorkspaceTabs(user).map((t) => t.id)).toEqual(["daftar", "penerimaan"]);
    expect(loadCashBankWorkspaceTabs({ id: "other" })).toEqual([]);
  });

  test("clear on logout removes user key", () => {
    saveCashBankWorkspaceTabs(user, CASH_BANK_WORKSPACE_TABS);
    clearCashBankWorkspaceTabs(user);
    expect(loadCashBankWorkspaceTabs(user)).toEqual([]);
  });
});
