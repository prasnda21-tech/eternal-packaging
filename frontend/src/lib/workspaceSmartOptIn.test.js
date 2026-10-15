import {
  WORKSPACE_REMEMBER_SEARCH_PATHS,
  WORKSPACE_SMART_EXCEPTIONS,
  workspacePathRemembersSearch,
  withWorkspaceRememberSearch,
} from "./workspaceSmartOptIn";
import { GLOBAL_WORKSPACE_TABS } from "./globalWorkspace";
import { SALES_WORKSPACE_TABS } from "./salesWorkspace";

describe("workspaceSmartOptIn", () => {
  test("sales list leaves remember search including pengiriman and penerimaan", () => {
    expect(workspacePathRemembersSearch("/penjualan/pengiriman-pesanan")).toBe(true);
    expect(workspacePathRemembersSearch("/penjualan/penerimaan-penjualan")).toBe(true);
    expect(SALES_WORKSPACE_TABS.find((t) => t.id === "pengiriman-pesanan")?.rememberSearch).toBe(true);
    expect(SALES_WORKSPACE_TABS.find((t) => t.id === "penerimaan-penjualan")?.rememberSearch).toBe(true);
  });

  test("production tasks and purchasing lists opt into rememberSearch via global catalog", () => {
    expect(GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/tasks/operator")?.rememberSearch).toBe(true);
    expect(GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/produksi")?.rememberSearch).toBe(true);
    expect(GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/pembelian/permintaan")?.rememberSearch).toBe(true);
    expect(GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/kas-bank/daftar")?.rememberSearch).toBe(true);
  });

  test("sensitive and authoritative surfaces stay excluded", () => {
    expect(workspacePathRemembersSearch("/kas-bank/rekonsiliasi")).toBe(false);
    expect(workspacePathRemembersSearch("/persediaan/stock-saat-ini")).toBe(false);
    expect(workspacePathRemembersSearch("/buku-besar/laba-rugi")).toBe(false);
    expect(workspacePathRemembersSearch("/hr-payroll/daftar-karyawan")).toBe(false);
    expect(WORKSPACE_SMART_EXCEPTIONS["/persediaan/stock-saat-ini"]).toMatch(/authoritative/i);
    expect(GLOBAL_WORKSPACE_TABS.find((t) => t.path === "/kas-bank/rekonsiliasi")?.rememberSearch).toBeFalsy();
  });

  test("withWorkspaceRememberSearch is idempotent and path-based", () => {
    const tab = { id: "x", path: "/penjualan/import-po", label: "Import PO" };
    const once = withWorkspaceRememberSearch(tab);
    expect(once.rememberSearch).toBe(true);
    expect(withWorkspaceRememberSearch(once).rememberSearch).toBe(true);
    expect(WORKSPACE_REMEMBER_SEARCH_PATHS.has("/penjualan/import-po")).toBe(true);
  });
});
