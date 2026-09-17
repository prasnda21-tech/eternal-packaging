import fs from "fs";
import path from "path";
import { NAV_KEYS } from "../lib/navAccess";

describe("Prediksi Order page", () => {
  const src = fs.readFileSync(path.join(__dirname, "PrediksiOrder.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "App.js"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "..", "lib", "navAccess.js"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "..", "lib", "api.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "..", "components", "AppShell.jsx"), "utf8");

  test("menu Prediksi Order is grouped under Penjualan while its route and permission stay stable", () => {
    expect(NAV_KEYS.orderPrediction).toBe("nav.reports.order_prediction");
    expect(nav).toMatch(/Prediksi Order/);
    expect(nav).toMatch(/parent: NAV_KEYS\.sales/);
    expect(nav).toMatch(/\/laporan\/prediksi-order/);
    expect(nav).toMatch(/top-nav-prediksi-order/);
    expect(shell).toMatch(/salesReportActive/);
  });

  test("permission filtering uses nav key and view perm", () => {
    expect(app).toMatch(/NAV_KEYS\.orderPrediction/);
    expect(app).toMatch(/reports\.order_prediction\.view/);
    expect(app).toMatch(/\/laporan\/prediksi-order/);
    expect(app).toMatch(/r\.orderPrediction/);
  });

  test("unauthorized remains hidden via reportsMenuItems", () => {
    expect(nav).toMatch(/orderPrediction/);
    expect(nav).toMatch(/canSeeNav\(hasPerm, NAV_KEYS\.orderPrediction\)/);
  });

  test("kpi summary table search status pattern detail history", () => {
    expect(src).toMatch(/prediksi-kpi/);
    expect(src).toMatch(/prediksi-table/);
    expect(src).toMatch(/prediksi-filter-search/);
    expect(src).toMatch(/prediksi-filter-status/);
    expect(src).toMatch(/prediksi-filter-pattern/);
    expect(src).toMatch(/prediksi-detail/);
    expect(src).toMatch(/prediksi-detail-history/);
    expect(src).toMatch(/prediksi-detail-outlet/);
    expect(src).toMatch(/textOverflow/);
    expect(src).toMatch(/prediksi-tab-orders/);
    expect(src).toMatch(/prediksi-refresh/);
  });

  test("compact analysis keeps nine operational columns and all detail evidence", () => {
    expect(src).toMatch(/prediksi-compact-table/);
    expect(src).toMatch(/Customer \/ Outlet/);
    expect(src).toMatch(/Prediksi Order/);
    expect(src).toMatch(/prediksi-detail-button/);
    expect(src).toMatch(/colSpan=\{9\}/);
    expect(src).toMatch(/prediksi-customer-cell/);
    expect(src).toMatch(/followupTone/);
    expect(src).toMatch(/analysis\.median_gap/);
    expect(src).toMatch(/analysis\.prediction_start/);
    expect(src).toMatch(/prediksi-detail-history/);
  });

  test("compact header and controls preserve import and every existing filter", () => {
    expect(src).toMatch(/prediksi-heading-copy/);
    expect(src).toMatch(/prediksi-controls-row/);
    expect(src).toMatch(/prediksi-import-button/);
    expect(src).toMatch(/prediksi-filter-search/);
    expect(src).toMatch(/prediksi-filter-status/);
    expect(src).toMatch(/prediksi-filter-pattern/);
    expect(src).toMatch(/prediksi-filter-confidence/);
    expect(src).toMatch(/prediksi-filter-sales/);
  });

  test("omzet displays dash and there is no manual data entry", () => {
    expect(src).toMatch(/dashOmzet/);
    expect(src).toMatch(/prediksi-omzet-/);
    expect(src).not.toMatch(/SYNC EXCEL|TAMBAH ORDER/);
    expect(src).toMatch(/prediksi-import-button/);
    expect(src).not.toMatch(/<form/);
    expect(src).not.toMatch(/api\.(create|add|import)/);
  });

  test("api is read-only derived report", () => {
    expect(api).toMatch(/\/reports\/order-prediction/);
    expect(api).toMatch(/orderPredictionDetail/);
    expect(shell).toMatch(/NAV_KEYS\.orderPrediction/);
  });
});
