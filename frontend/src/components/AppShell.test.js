import fs from "fs";
import path from "path";
import { nextSidebarMenu } from "../lib/sidebarFlyout";

describe("sidebar flyout interaction", () => {
  const shell = fs.readFileSync(path.join(__dirname, "AppShell.jsx"), "utf8");
  const grid = fs.readFileSync(path.join(__dirname, "SidebarFlyoutGrid.jsx"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

  test("click parent opens, same parent closes, other parent switches", () => {
    expect(nextSidebarMenu(null, "produksi")).toBe("produksi");
    expect(nextSidebarMenu("produksi", "produksi")).toBe(null);
    expect(nextSidebarMenu("produksi", "reports")).toBe("reports");
    expect(shell).toMatch(/nextSidebarMenu\(cur, key\)/);
    expect(shell).toMatch(/sidebar-flyout-produksi/);
    expect(shell).toMatch(/sidebar-flyout-reports/);
  });

  test("click outside and Escape close the flyout", () => {
    expect(shell).toMatch(/sidebar-flyout-scrim/);
    expect(shell).toMatch(/e\.key === "Escape"/);
    expect(shell).toMatch(/setActiveSidebarMenu\(null\)/);
  });

  test("active route still marks parent and tooltip is preserved", () => {
    expect(shell).toMatch(/active=\{produksiActive\}/);
    expect(shell).toMatch(/is-active/);
    expect(shell).toMatch(/reportsActive/);
    expect(shell).toMatch(/onClick=\{closeFlyout\}/);
    expect(shell).toMatch(/title=\{label\}/);
    expect(shell).toMatch(/rail-tooltip/);
  });

  test("flyout is anchored to the right edge of the sidebar rail without covering it", () => {
    expect(shell).toMatch(/placeSidebarFlyout/);
    expect(shell).toMatch(/app-sidebar-slot/);
    expect(shell).toMatch(/style=\{flyoutStyle\}/);
    expect(shell).toMatch(/ref=\{sidebarRef\}/);
    expect(css).toMatch(/\.app-sidebar-slot\{/);
    expect(css).toMatch(/--sidebar-rail:64px/);
    expect(css).not.toMatch(/\.sidebar-flyout\{[^}]*top:12px/);
    expect(css).not.toMatch(/\.sidebar-flyout\{[^}]*left:8px;right:8px;top:56px/);
    expect(css).toMatch(/overflow-x:hidden/);
    expect(css).toMatch(/max-width:min\(320px,calc\(100vw - var\(--sidebar-rail\)\)\)/);
  });

  test("menu names routes and permission helpers stay the same", () => {
    expect(shell).toMatch(/Produksi/);
    expect(nav).toMatch(/Jadwal Produksi/);
    expect(shell).toMatch(/Pengiriman Hari Ini/);
    expect(nav).toMatch(/to: "\/produksi\/pengiriman-hari-ini"/);
    expect(nav).toMatch(/Jadwal Pengiriman/);
    expect(nav).toMatch(/Jadwal Pengiriman Ulang/);
    expect(shell).not.toMatch(/Rute Saya/);
    expect(shell).not.toMatch(/rute-saya/);
    expect(shell).toMatch(/Tugas Produksi/);
    expect(shell).toMatch(/Penjualan/);
    expect(shell).toMatch(/Pembelian/);
    expect(shell).toMatch(/Persediaan/);
    expect(nav).toMatch(/Barang\/Jasa/);
    expect(shell).toMatch(/Laporan/);
    expect(shell).toMatch(/Pengaturan/);
    expect(nav).toMatch(/TASK_NAV_ORDER/);
    expect(nav).toMatch(/"\/produksi\/jadwal-pengiriman"/);
    expect(nav).toMatch(/\/persediaan\/master-barang-jasa/);
    expect(nav).toMatch(/\/laporan\/gaji-operator/);
    expect(shell).toMatch(/productionNavVisible\(hasPerm\)/);
    expect(shell).toMatch(/productionMenuItems\(hasPerm\)/);
    expect(shell).toMatch(/taskMenuItems\(hasPerm\)/);
    expect(shell).toMatch(/salesNavVisible\(hasPerm\)/);
    expect(shell).toMatch(/hasPerm\(NAV_KEYS\.dashboard\)/);
    expect(shell).toMatch(/TASK_NAV_ORDER\.map/);
    expect(shell).not.toMatch(/Tugas per Divisi/);
    expect(shell).toMatch(/productionNav\.deliveryToday/);
    const reportsFlyout = shell.slice(shell.indexOf('id="sidebar-flyout-reports"'));
    expect(reportsFlyout).not.toMatch(/Pengiriman Hari Ini/);
    expect(shell).toMatch(/id="sidebar-flyout-produksi"[\s\S]*Pengiriman Hari Ini[\s\S]*id="sidebar-flyout-sales"/);
  });

  test("Penjualan flyout is a permission-filtered tile grid that still toggles closed", () => {
    expect(shell).toMatch(/testid="top-nav-sales"/);
    expect(shell).toMatch(/label="Penjualan"/);
    expect(shell).toMatch(/toggleMenu\("sales"\)/);
    expect(shell).toMatch(/salesMenuItems\(hasPerm\)/);
    expect(shell).toMatch(/salesOpen && salesItems\.length > 0/);
    expect(shell).toMatch(/title="Penjualan"/);
    expect(grid).toMatch(/<h2>\{title\}<\/h2>/);
    expect(grid).toMatch(/className="sidebar-flyout is-sales-grid"/);
    expect(grid).toMatch(/sales-menu-grid/);
    expect(grid).toMatch(/sales-menu-tile/);
    expect(grid).toMatch(/to=\{item\.to\}/);
    expect(grid).toMatch(/data-testid=\{item\.testid\}/);
    expect(grid).toMatch(/className=\{\(\{ isActive \}\) => "sales-menu-tile" \+ \(isActive \? " is-active" : ""\)\}/);
    expect(grid).toMatch(/aria-label=\{item\.label\}/);
    expect(shell).toMatch(/id="sidebar-flyout-produksi"/);
    expect(shell).toMatch(/id="sidebar-flyout-sales"/);
    expect(shell).toMatch(/id="sidebar-flyout-reports"/);
    expect(shell).not.toMatch(/Penawaran/);
    expect(shell).not.toMatch(/Faktur/);
    expect(shell).not.toMatch(/Komisi/);
    expect(css).toMatch(/\.sidebar-flyout\.is-sales-grid\{/);
    expect(css).toMatch(/grid-template-columns:repeat\(auto-fill,minmax\(132px,1fr\)\)/);
    expect(css).toMatch(/\.sales-menu-tile:focus-visible\{/);
    expect(css).toMatch(/\.sales-menu-tile\.is-active\{/);
  });

  test("every parent with children reuses the Penjualan tile grid", () => {
    expect(shell).toMatch(/id="sidebar-flyout-produksi"/);
    expect(shell).toMatch(/id="sidebar-flyout-sales"/);
    expect(shell).toMatch(/id="sidebar-flyout-purchasing"/);
    expect(shell).toMatch(/id="sidebar-flyout-inventory"/);
    expect(shell).toMatch(/id="sidebar-flyout-reports"/);
    expect(shell).toMatch(/produksiOpen && \(productionItems\.length \+ taskItems\.length\) > 0/);
    expect(shell).toMatch(/purchasingOpen && purchasingItems\.length > 0/);
    expect(shell).toMatch(/inventoryOpen && inventoryItems\.length > 0/);
    expect(shell).toMatch(/reportsOpen && reportsItems\.length > 0/);
    expect(shell.match(/<SidebarFlyoutGrid/g).length).toBe(5);
    expect(grid).toMatch(/if \(!blocks\.length\) return null/);
  });

  test("direct-link parents stay rail links without flyout", () => {
    expect(shell).toMatch(/to="\/dashboard"/);
    expect(shell).toMatch(/to="\/settings"/);
    expect(shell).not.toMatch(/sidebar-flyout-dashboard/);
    expect(shell).not.toMatch(/sidebar-flyout-settings/);
    expect(shell).not.toMatch(/toggleMenu\("dashboard"\)/);
    expect(shell).not.toMatch(/toggleMenu\("settings"\)/);
  });
});
