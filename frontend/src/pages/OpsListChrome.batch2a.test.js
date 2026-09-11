import fs from "fs";
import path from "path";

/**
 * Contract tests for Batch 2A — ops-list filter / button / form control standard.
 * Protects compact toolbar hierarchy + cascade reclaim (not pixel snapshots).
 */
describe("Ops-list chrome Batch 2A", () => {
  const root = path.join(__dirname, "..");
  const appCss = fs.readFileSync(path.join(root, "App.css"), "utf8");
  const uiCss = fs.readFileSync(path.join(root, "ui-system.css"), "utf8");
  const indexCss = fs.readFileSync(path.join(root, "index.css"), "utf8");
  const statusPo = fs.readFileSync(path.join(root, "pages/StatusPO.jsx"), "utf8");
  const delivery = fs.readFileSync(path.join(root, "pages/DeliverySchedulePage.jsx"), "utf8");
  const prediksi = fs.readFileSync(path.join(root, "pages/PrediksiOrder.jsx"), "utf8");
  const fixtures = fs.readFileSync(
    path.join(root, "lib/deliveryScheduleStatusLokasiFixtures.js"),
    "utf8",
  );

  function batch2aSlice() {
    const start = appCss.indexOf("Batch 2A:");
    const end = appCss.indexOf("Batch 2B:");
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    return appCss.slice(start, end);
  }

  test("compact ops toolbar controls keep intended shared classes", () => {
    for (const src of [statusPo, delivery, prediksi]) {
      expect(src).toMatch(/ship-status-toolbar/);
      expect(src).toMatch(/ship-filter-field/);
      expect(src).toMatch(/ship-icon-btn/);
      expect(src).toMatch(/ship-reset-btn/);
    }
    expect(delivery).toMatch(/ship-tool-btn/);
    expect(delivery).toMatch(/primary sm/);
    expect(delivery).toMatch(/data-testid="ds-filter-reset"/);
    expect(delivery).toMatch(/data-testid="ds-refresh"/);
    expect(delivery).toMatch(/data-testid="ds-optimasi-rute"/);
  });

  test("primary vs secondary button hierarchy remains in Batch 2A CSS", () => {
    const slice = batch2aSlice();
    expect(slice).toMatch(/\.ship-status-toolbar \.primary\.sm/);
    expect(slice).toMatch(/background:var\(--ep-primary\)/);
    expect(slice).toMatch(/\.ship-status-toolbar \.ship-reset-btn/);
    expect(slice).toMatch(/\.ship-status-toolbar \.ship-tool-btn/);
    expect(slice).toMatch(/\.ship-status-toolbar \.ship-icon-btn/);
    expect(slice).toMatch(/\.ship-reset-btn[\s\S]*?background:var\(--surface,#fff\)/);
    expect(slice).not.toMatch(/box-shadow:\s*0 1px 2px/);
  });

  test("compact form-control standard reclaim beats colorful 42px globals", () => {
    const slice = batch2aSlice();
    expect(slice).toMatch(
      /\.content \.ship-status-page \.ship-status-toolbar input:not\(\[type="checkbox"\]\)/,
    );
    expect(slice).toMatch(/min-height:var\(--ship-ctrl-h\)/);
    expect(slice).toMatch(/border:1px solid var\(--ship-border\)/);
    expect(slice).toMatch(/border-radius:var\(--ship-radius\)/);
    expect(slice).toMatch(/:focus-visible/);
    expect(slice).toMatch(/:disabled/);
    expect(indexCss).toMatch(/--ctrl-h-compact:\s*28px/);
    expect(indexCss).toMatch(/--radius-sm:\s*4px/);
    expect(uiCss).toMatch(/min-height:42px/);
  });

  test("filter / refresh / reset logic hooks remain unchanged", () => {
    expect(delivery).toMatch(/onClick=\{resetFilters\}/);
    expect(delivery).toMatch(/onClick=\{load\}/);
    expect(delivery).toMatch(/data-testid="ds-filter-from"/);
    expect(delivery).toMatch(/data-testid="ds-filter-status"/);
    expect(statusPo).toMatch(/ship-reset-btn/);
    expect(statusPo).toMatch(/data-testid="status-po-refresh"/);
    expect(statusPo).toMatch(/data-testid="status-po-filter-reset"/);
    expect(prediksi).toMatch(/ship-reset-btn/);
    expect(prediksi).toMatch(/ship-icon-btn/);
  });

  test("dev preview cannot activate in production and uses UI2 dummy IDs", () => {
    expect(delivery).toMatch(/process\.env\.NODE_ENV\s*===\s*["']development["']/);
    expect(delivery).toMatch(/previewStatusLokasi/);
    expect(delivery).toMatch(/BATCH2_UI_PREVIEW_DUMMY_ROWS/);
    expect(fixtures).toMatch(/DUMMY-UI2-PO-001/);
    expect(fixtures).toMatch(/DUMMY-UI2-PO-006/);
    expect(fixtures).toMatch(/export const BATCH2_UI_PREVIEW_DUMMY_ROWS/);
    expect(fixtures).not.toMatch(/\bfetch\(|\baxios\b|api\./);
  });

  test("Batch 2A does not introduce new !important", () => {
    expect(batch2aSlice()).not.toMatch(/!important/);
  });
});
