/** Map app routes to visual module identities. Visual only — no RBAC meaning. */

export const UI_MODULES = {
  dashboard: "dashboard",
  production: "production",
  sales: "sales",
  purchasing: "purchasing",
  inventory: "inventory",
  hr: "hr",
  payroll: "payroll",
  admin: "admin",
  route: "route",
  settings: "settings",
  reports: "reports",
};

export function moduleFromPath(pathname = "") {
  const path = String(pathname || "");
  if (path.startsWith("/penjualan") || path.startsWith("/admin/import")) return UI_MODULES.sales;
  if (path.startsWith("/pembelian")) return UI_MODULES.purchasing;
  if (path.startsWith("/persediaan")) return UI_MODULES.inventory;
  if (path.startsWith("/hr-payroll/penggajian") || path.startsWith("/hr-payroll/slip-gaji") || path.startsWith("/hr-payroll/komponen-gaji") || path.startsWith("/hr-payroll/riwayat")) {
    return UI_MODULES.payroll;
  }
  if (path.startsWith("/hr-payroll")) return UI_MODULES.hr;
  if (path.startsWith("/administrasi-karyawan")) return UI_MODULES.admin;
  if (path.startsWith("/optimasi-rute")) return UI_MODULES.route;
  if (path.startsWith("/settings")) return UI_MODULES.settings;
  if (path.startsWith("/laporan")) return UI_MODULES.reports;
  if (path.startsWith("/buku-besar")) return UI_MODULES.reports;
  if (
    path.startsWith("/produksi")
    || path.startsWith("/tasks")
    || path.startsWith("/po")
    || path.startsWith("/analytics")
  ) {
    return UI_MODULES.production;
  }
  if (path.startsWith("/dashboard")) return UI_MODULES.dashboard;
  return UI_MODULES.dashboard;
}
