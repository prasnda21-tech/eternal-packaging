import fs from "fs";
import path from "path";

describe("Penjualan workspace / UI contract", () => {
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const auth = fs.readFileSync(path.join(__dirname, "../lib/auth.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const ui = fs.readFileSync(path.join(__dirname, "../ui-system.css"), "utf8");
  const tabs = fs.readFileSync(path.join(__dirname, "../components/WorkspaceTabs.jsx"), "utf8");
  const hook = fs.readFileSync(path.join(__dirname, "../hooks/useSalesWorkspaceTabs.js"), "utf8");
  const engine = fs.readFileSync(path.join(__dirname, "../lib/workspaceTabs.js"), "utf8");
  const sales = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
  const delivery = fs.readFileSync(path.join(__dirname, "PengirimanPesanan.jsx"), "utf8");
  const invoice = fs.readFileSync(path.join(__dirname, "FakturPenjualan.jsx"), "utf8");
  const receipt = fs.readFileSync(path.join(__dirname, "PenerimaanPenjualan.jsx"), "utf8");
  const importPo = fs.readFileSync(path.join(__dirname, "ImportPO.jsx"), "utf8");
  const activity = fs.readFileSync(path.join(__dirname, "SalesActivityPage.jsx"), "utf8");
  const outlet = fs.readFileSync(path.join(__dirname, "SalesActivityOutletDetail.jsx"), "utf8");

  test("AppShell reuses shared workspace tabs for Penjualan", () => {
    expect(shell).toMatch(/useGlobalWorkspaceTabs/);
    expect(shell).toMatch(/<WorkspaceTabs/);
    expect(shell).toMatch(/content-penjualan-workspace/);
    expect(shell).toMatch(/testid="workspace-tabs"/);
    expect(shell).not.toMatch(/testid="sales-workspace-tabs"/);
    expect(hook).toMatch(/useWorkspaceTabs/);
    expect(engine).toMatch(/createWorkspaceTabApi/);
    expect(tabs).toMatch(/kb-workspace-tabs/);
    expect(tabs).toMatch(/kb-workspace-tab-close/);
  });

  test("logout clears Penjualan workspace tabs", () => {
    expect(auth).toMatch(/clearGlobalWorkspaceTabs\(previous\)/);
    expect(auth).toMatch(/clearGlobalWorkspaceTabs\(null\)/);
    expect(auth).toMatch(/clearSalesWorkspaceTabs\(previous\)/);
    expect(auth).toMatch(/clearSalesWorkspaceTabs\(null\)/);
  });

  test("Kas & Bank workspace tab CSS tokens are reused", () => {
    expect(css).toMatch(/\.kb-workspace-tabs\{/);
    expect(css).toMatch(/\.kb-workspace-tab\.is-active\{/);
    expect(css).toMatch(/\.content\.content-penjualan-workspace/);
    expect(css).toMatch(/\[data-module="sales"\] \.penjualan-page/);
    expect(css).toMatch(/grid-template-columns:var\(--sidebar-rail\) minmax\(0,1fr\)/);
    expect(css).toMatch(/\.penjualan-page \.page-heading\{\s*flex-wrap:wrap/);
    expect(css).toMatch(/\.penjualan-page :has\(> table\)/);
    expect(css).not.toMatch(/html\s*,\s*body[^{]*\{[^}]*overflow-x:\s*hidden/);
    expect(ui).toMatch(/\[data-module="sales"\]/);
    expect(ui).toMatch(/--mod:#0D9488/);
  });

  test("active Penjualan pages opt into workspace chrome", () => {
    expect(sales).toMatch(/penjualan-page penjualan-workspace/);
    expect(delivery).toMatch(/penjualan-page penjualan-workspace/);
    expect(invoice).toMatch(/penjualan-page penjualan-workspace/);
    expect(receipt).toMatch(/penjualan-page penjualan-workspace/);
    expect(importPo).toMatch(/penjualan-page penjualan-workspace/);
    expect(activity).toMatch(/penjualan-page penjualan-workspace/);
    expect(outlet).toMatch(/penjualan-page penjualan-workspace/);
  });

  test("does not invent a second tab primitive", () => {
    expect(shell).not.toMatch(/CashBankWorkspaceTabs/);
    expect(shell).not.toMatch(/useCashBankWorkspaceTabs/);
    expect(shell).not.toMatch(/useSalesWorkspaceTabs/);
    expect(tabs).not.toMatch(/from "@radix-ui\/react-tabs"/);
  });
});
