import fs from "fs";
import path from "path";
import { NAV_KEYS } from "../lib/navAccess";

describe("Prediksi Order page", () => {
  const src = fs.readFileSync(path.join(__dirname, "PrediksiOrder.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "App.js"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "..", "lib", "navAccess.js"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "..", "lib", "api.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "..", "components", "AppShell.jsx"), "utf8");

  test("menu Prediksi Order is a Laporan child", () => {
    expect(NAV_KEYS.orderPrediction).toBe("nav.reports.order_prediction");
    expect(nav).toMatch(/Prediksi Order/);
    expect(nav).toMatch(/\/laporan\/prediksi-order/);
    expect(nav).toMatch(/top-nav-prediksi-order/);
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
