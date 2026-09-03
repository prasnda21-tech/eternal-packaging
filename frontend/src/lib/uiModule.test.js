import { moduleFromPath, UI_MODULES } from "./uiModule";

describe("moduleFromPath", () => {
  test("maps sales and purchasing routes", () => {
    expect(moduleFromPath("/penjualan/pesanan-penjualan")).toBe(UI_MODULES.sales);
    expect(moduleFromPath("/penjualan/aktivitas-sales")).toBe(UI_MODULES.sales);
    expect(moduleFromPath("/admin/import")).toBe(UI_MODULES.sales);
    expect(moduleFromPath("/pembelian/permintaan")).toBe(UI_MODULES.purchasing);
  });

  test("maps hr vs payroll and admin", () => {
    expect(moduleFromPath("/hr-payroll/daftar-karyawan")).toBe(UI_MODULES.hr);
    expect(moduleFromPath("/hr-payroll/penggajian")).toBe(UI_MODULES.payroll);
    expect(moduleFromPath("/administrasi-karyawan/buat")).toBe(UI_MODULES.admin);
  });

  test("maps production, route, settings, dashboard", () => {
    expect(moduleFromPath("/produksi")).toBe(UI_MODULES.production);
    expect(moduleFromPath("/po/abc")).toBe(UI_MODULES.production);
    expect(moduleFromPath("/optimasi-rute/baru")).toBe(UI_MODULES.route);
    expect(moduleFromPath("/settings")).toBe(UI_MODULES.settings);
    expect(moduleFromPath("/")).toBe(UI_MODULES.dashboard);
  });
});
