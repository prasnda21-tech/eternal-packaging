import fs from "fs";
import path from "path";
import { nextSidebarMenu } from "../lib/sidebarFlyout";

describe("sidebar flyout interaction", () => {
  const shell = fs.readFileSync(path.join(__dirname, "AppShell.jsx"), "utf8");
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
    expect(shell).toMatch(/style=\{\{ left: flyoutPos\.left, top: flyoutPos\.top \}\}/);
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
    expect(shell).toMatch(/Jadwal Produksi/);
    expect(shell).toMatch(/Jadwal Pengiriman/);
    expect(shell).toMatch(/Jadwal Pengiriman Ulang/);
    expect(shell).not.toMatch(/Rute Saya/);
    expect(shell).not.toMatch(/rute-saya/);
    expect(shell).toMatch(/Tugas Produksi/);
    expect(shell).toMatch(/Penjualan/);
    expect(shell).toMatch(/Pembelian/);
    expect(shell).toMatch(/Persediaan/);
    expect(shell).toMatch(/Barang\/Jasa/);
    expect(shell).toMatch(/Laporan/);
    expect(shell).toMatch(/Pengaturan/);
    expect(shell).toMatch(/TASK_NAV_LABELS\[id\]/);
    expect(shell).toMatch(/to="\/produksi\/jadwal-pengiriman"/);
    expect(shell).toMatch(/to="\/persediaan\/master-barang-jasa"/);
    expect(shell).toMatch(/to="\/laporan\/gaji-operator"/);
    expect(shell).toMatch(/productionNavVisible\(hasPerm\)/);
    expect(shell).toMatch(/salesNavVisible\(hasPerm\)/);
    expect(shell).toMatch(/hasPerm\(NAV_KEYS\.dashboard\)/);
    expect(shell).toMatch(/TASK_NAV_ORDER\.map/);
    expect(shell).not.toMatch(/Tugas per Divisi/);
  });
});
