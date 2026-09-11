import fs from "fs";
import path from "path";
import {
  STATUS_LOKASI_SORT_DUMMY_ROWS,
  BATCH2_UI_PREVIEW_DUMMY_ROWS,
} from "../lib/deliveryScheduleStatusLokasiFixtures";
import { locationSetupStatusLabel } from "../lib/deliveryRouteBridge";

/**
 * Contract tests for Batch 2B — ops-list semantic status + feedback presentation.
 * Protects pill semantics + feedback classes; does not change business labels.
 */
describe("Ops-list chrome Batch 2B", () => {
  const root = path.join(__dirname, "..");
  const appCss = fs.readFileSync(path.join(root, "App.css"), "utf8");
  const delivery = fs.readFileSync(path.join(root, "pages/DeliverySchedulePage.jsx"), "utf8");
  const statusPo = fs.readFileSync(path.join(root, "pages/StatusPO.jsx"), "utf8");
  const prediksi = fs.readFileSync(path.join(root, "pages/PrediksiOrder.jsx"), "utf8");
  const tabs = fs.readFileSync(path.join(root, "components/WorkspaceTabs.jsx"), "utf8");
  const fixtures = fs.readFileSync(
    path.join(root, "lib/deliveryScheduleStatusLokasiFixtures.js"),
    "utf8",
  );

  function batch2bSlice() {
    const start = appCss.indexOf("Batch 2B:");
    const end = appCss.indexOf("Batch 1B:");
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    return appCss.slice(start, end);
  }

  test("semantic ops-list pill classes remain present and distinguishable", () => {
    const slice = batch2bSlice();
    expect(slice).toMatch(/\.ship-status-page \.pill-ok\{/);
    expect(slice).toMatch(/\.ship-status-page \.pill-warn\{/);
    expect(slice).toMatch(/\.ship-status-page \.pill-cancel\{/);
    expect(slice).toMatch(/\.ship-status-page \.pill-muted\{/);
    expect(slice).toMatch(/border-radius:var\(--ship-radius\)/);
    expect(slice).toMatch(/border:1px solid/);
    expect(delivery).toMatch(/pill-ok/);
    expect(delivery).toMatch(/pill-warn/);
    expect(delivery).toMatch(/className=\{\"pill sm \"/);
  });

  test("Belum Diatur / Sudah Diatur remain distinguishable on sort dummies", () => {
    const labels = STATUS_LOKASI_SORT_DUMMY_ROWS.map((r) => locationSetupStatusLabel(r));
    expect(labels).toContain("Belum Diatur");
    expect(labels).toContain("Sudah Diatur");
    expect(new Set(labels).size).toBeGreaterThanOrEqual(2);
    expect(delivery).toMatch(/pill-ok/);
    expect(delivery).toMatch(/pill-warn/);
  });

  test("feedback / error / empty / loading classes remain presentation-only", () => {
    const slice = batch2bSlice();
    expect(slice).toMatch(/\.ship-status-page \.error\{/);
    expect(slice).toMatch(/td\.empty-state/);
    expect(slice).toMatch(/\.spin/);
    expect(statusPo).toMatch(/className="error"/);
    expect(delivery).toMatch(/className="error"/);
    expect(prediksi).toMatch(/className="error"/);
    expect(statusPo).toMatch(/empty-state/);
    expect(delivery).toMatch(/empty-state/);
    expect(slice).not.toMatch(/fetch\(|retry|setTimeout/);
  });

  test("qty-status / iro-status / hr-status / ea-badge remain untouched by Batch 2B", () => {
    const slice = batch2bSlice();
    expect(slice).not.toMatch(/\.qty-status/);
    expect(slice).not.toMatch(/\.iro-status/);
    expect(slice).not.toMatch(/\.hr-status/);
    expect(slice).not.toMatch(/\.ea-badge/);
    expect(appCss).toMatch(/\.qty-status\{/);
    expect(appCss).toMatch(/\.iro-status/);
    expect(appCss).toMatch(/\.hr-status/);
    expect(appCss).toMatch(/\.ea-badge/);
  });

  test("workspace tabs and prod-table remain untouched by Batch 2B", () => {
    const slice = batch2bSlice();
    expect(slice).not.toMatch(/kb-workspace-tab/);
    expect(slice).not.toMatch(/\.prod-table/);
    expect(tabs).toMatch(/kb-workspace-tab/);
    expect(tabs).toMatch(/WorkspaceTabs/);
    expect(appCss).toMatch(/\.prod-table\{/);
    expect(appCss).toMatch(/\.kb-workspace-tabs\{/);
  });

  test("Batch 2 preview dummies stay local-only with mixed progress", () => {
    expect(BATCH2_UI_PREVIEW_DUMMY_ROWS).toHaveLength(6);
    expect(BATCH2_UI_PREVIEW_DUMMY_ROWS.map((r) => r.order_number)).toEqual([
      "DUMMY-UI2-PO-001",
      "DUMMY-UI2-PO-002",
      "DUMMY-UI2-PO-003",
      "DUMMY-UI2-PO-004",
      "DUMMY-UI2-PO-005",
      "DUMMY-UI2-PO-006",
    ]);
    const loc = BATCH2_UI_PREVIEW_DUMMY_ROWS.map((r) => locationSetupStatusLabel(r));
    expect(loc).toContain("Belum Diatur");
    expect(loc).toContain("Sudah Diatur");
    const progresses = BATCH2_UI_PREVIEW_DUMMY_ROWS.map((r) => r.percent_delivered);
    expect(Math.min(...progresses)).toBe(0);
    expect(Math.max(...progresses)).toBe(100);
    expect(fixtures).not.toMatch(/\bfetch\(|\baxios\b/);
  });

  test("Batch 2B introduces no new !important", () => {
    expect(batch2bSlice()).not.toMatch(/!important/);
  });
});
