import {
  canOpenDrill,
  dashboardRincianPath,
  deliveryPlanDrillPath,
  deliveryScheduleDrillPath,
  fakturPembelianDrillPath,
  fakturPenjualanDrillPath,
  fakturPenjualanOpsPath,
  labaRugiDrillPath,
  mutasiKasDrillPath,
} from "./dashboardDrillNav";

test("canOpenDrill mirrors status-po gate (any + also)", () => {
  const drill = {
    path: dashboardRincianPath({ widget: "po_aktif", dashboard: "produksi", basis: "snapshot" }),
    any_of: ["nav.reports.status_po"],
    also_any_of: ["schedule.view"],
  };
  expect(canOpenDrill(drill, (k) => k === "nav.reports.status_po")).toBe(false);
  expect(canOpenDrill(drill, (k) => k === "schedule.view")).toBe(false);
  expect(canOpenDrill(drill, (k) => k === "nav.reports.status_po" || k === "schedule.view")).toBe(true);
});

test("dashboard cards open Rincian Dashboard not ops lists", () => {
  expect(fakturPenjualanDrillPath({
    dateFrom: "2026-10-01",
    dateTo: "2026-10-24",
    widget: "omzet",
  })).toBe(
    "/dashboard/rincian?dashboard=penjualan&widget=omzet&start_date=2026-10-01&end_date=2026-10-24",
  );
  expect(fakturPenjualanDrillPath({
    dateFrom: "2026-10-27",
    dateTo: "2026-10-27",
    datePreset: "today",
    widget: "omzet",
  })).toContain("widget=today");
});

test("ops helper still seeds faktur list for non-dashboard deep links", () => {
  const path = fakturPenjualanOpsPath({
    dateFrom: "2026-10-01",
    dateTo: "2026-10-24",
    financialStatus: "finalized",
    sales: "S1",
    customerId: "C1",
  });
  expect(path).toContain("/penjualan/faktur-penjualan?");
  expect(path).toContain("sales=S1");
  expect(path).toContain("customer_id=C1");
});

test("delivery schedule and plan drills target rincian", () => {
  expect(deliveryScheduleDrillPath("hari_ini")).toBe(
    "/dashboard/rincian?dashboard=penjualan&widget=shipToday&status=hari_ini",
  );
  expect(deliveryPlanDrillPath({ status: "terlambat", unit: "PCS" })).toBe(
    "/dashboard/rincian?dashboard=produksi&widget=delivery_qty_overdue&unit=PCS&status=terlambat",
  );
});

test("cash purchase and laba rugi drills seed WIB dates on rincian", () => {
  expect(mutasiKasDrillPath({ start: "2026-10-01", end: "2026-10-24" })).toBe(
    "/dashboard/rincian?dashboard=keuangan&widget=cash&start_date=2026-10-01&end_date=2026-10-24",
  );
  expect(labaRugiDrillPath({ start: "2026-01-01", end: "2026-10-24" })).toBe(
    "/dashboard/rincian?dashboard=keuangan&widget=profit&start_date=2026-01-01&end_date=2026-10-24",
  );
  expect(fakturPembelianDrillPath({ dateFrom: "2026-10-01", dateTo: "2026-10-24" })).toContain(
    "widget=purchases",
  );
});
