import fs from "fs";
import path from "path";

/**
 * Contract tests for Batch 3A+3B+3C — ops-list page chrome composition.
 * Protects header hierarchy, filter grouping, and toolbar-to-table rhythm
 * without changing filter/query/table behavior or earlier batch baselines.
 */
describe("Ops-list chrome Batch 3A+3B+3C", () => {
  const root = path.join(__dirname, "..");
  const appCss = fs.readFileSync(path.join(root, "App.css"), "utf8");
  const uiCss = fs.readFileSync(path.join(root, "ui-system.css"), "utf8");
  const statusPo = fs.readFileSync(path.join(root, "pages/StatusPO.jsx"), "utf8");
  const delivery = fs.readFileSync(path.join(root, "pages/DeliverySchedulePage.jsx"), "utf8");
  const prediksi = fs.readFileSync(path.join(root, "pages/PrediksiOrder.jsx"), "utf8");
  const tabs = fs.readFileSync(path.join(root, "components/WorkspaceTabs.jsx"), "utf8");

  function batch3Slice() {
    const start = appCss.indexOf("Batch 3A+3B+3C:");
    const end = appCss.indexOf(".append-entries{");
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    return appCss.slice(start, end);
  }

  test("target pages keep shared chrome / heading / toolbar / grid structure", () => {
    for (const src of [statusPo, delivery, prediksi]) {
      expect(src).toMatch(/ship-status-page/);
      expect(src).toMatch(/ship-status-chrome/);
      expect(src).toMatch(/ship-status-heading/);
      expect(src).toMatch(/ship-status-toolbar/);
      expect(src).toMatch(/ship-filter-field/);
      expect(src).toMatch(/ship-status-grid/);
      expect(src).toMatch(/ship-table/);
    }
  });

  test("Batch 3A header hierarchy rules are page-scoped", () => {
    const slice = batch3Slice();
    expect(slice).toMatch(/\.ship-status-page \.ship-status-heading\{/);
    expect(slice).toMatch(/\.ship-status-page \.ship-status-heading h1\{/);
    expect(slice).toMatch(/\.ship-status-page \.ship-status-heading \.eyebrow\{/);
    expect(slice).toMatch(/\.ship-status-page \.ship-status-heading \.muted\.small\{/);
    expect(delivery).toMatch(/className="eyebrow"/);
    expect(statusPo).toMatch(/muted small/);
    expect(prediksi).toMatch(/muted small/);
  });

  test("Batch 3A neutralizes Prediksi page-heading card chrome without JSX change", () => {
    const slice = batch3Slice();
    expect(prediksi).toMatch(/ship-status-heading page-heading/);
    expect(uiCss).toMatch(/\.page-heading\s*\{/);
    expect(uiCss).toMatch(/\.page-heading::before/);
    expect(slice).toMatch(/\.prediksi-order-page \.ship-status-heading\.page-heading\{/);
    expect(slice).toMatch(/border-radius:0/);
    expect(slice).toMatch(/box-shadow:none/);
    expect(slice).toMatch(/\.prediksi-order-page \.ship-status-heading\.page-heading::before\{/);
    expect(slice).toMatch(/content:none/);
  });

  test("Batch 3B filter section grouping stays presentation-only", () => {
    const slice = batch3Slice();
    expect(slice).toMatch(/\.ship-status-page \.ship-status-toolbar\{/);
    expect(slice).toMatch(/border:1px solid var\(--ship-border/);
    expect(slice).toMatch(/border-radius:var\(--ship-radius\)/);
    expect(slice).toMatch(/background:#F8FAFC/);
    expect(slice).toMatch(/flex-wrap:wrap/);
    expect(slice).toMatch(/\.ship-status-page \.ship-status-toolbar \.ship-filter-field\{/);
    expect(slice).not.toMatch(/min-height:\s*42px/);
    expect(slice).not.toMatch(/height:\s*42px/);
  });

  test("Batch 3C toolbar-to-table rhythm selectors remain present", () => {
    const slice = batch3Slice();
    expect(slice).toMatch(/\.ship-status-page \.ship-status-chrome\{/);
    expect(slice).toMatch(/\.ship-status-page \.ship-status-chrome > \.bulk-bar\{/);
    expect(slice).toMatch(/\.ship-status-page \.ship-status-chrome > \.error\{/);
    expect(slice).toMatch(/\.ship-status-page \.ship-status-grid\{/);
    expect(slice).toMatch(/margin-top:0/);
    expect(slice).toMatch(/\.prediksi-order-page \.prediksi-kpi\{margin:0 0 10px\}/);
    expect(slice).toMatch(/\.prediksi-order-page \.prediksi-tabs\{margin:0 0 10px\}/);
  });

  test("Batch 3 does not restyle ship-table internals or non-target families", () => {
    const slice = batch3Slice();
    expect(slice).not.toMatch(/\.ship-table\s+thead/);
    expect(slice).not.toMatch(/\.ship-table\s+tbody/);
    expect(slice).not.toMatch(/tr\.is-selected/);
    expect(slice).not.toMatch(/\.prod-table/);
    expect(slice).not.toMatch(/\.qty-status/);
    expect(slice).not.toMatch(/\.iro-status/);
    expect(slice).not.toMatch(/\.hr-status/);
    expect(slice).not.toMatch(/\.ea-badge/);
    expect(slice).not.toMatch(/kb-workspace-tab/);
    expect(slice).not.toMatch(/\.kpi-card\{/);
    expect(appCss).toMatch(/\.qty-status\{/);
    expect(appCss).toMatch(/\.iro-status/);
    expect(appCss).toMatch(/\.hr-status/);
    expect(appCss).toMatch(/\.ea-badge/);
    expect(appCss).toMatch(/\.prod-table\{/);
    expect(tabs).toMatch(/kb-workspace-tab/);
    expect(tabs).toMatch(/WorkspaceTabs/);
  });

  test("Batch 3 introduces no new !important and stays after prior batch markers", () => {
    const slice = batch3Slice();
    expect(slice).not.toMatch(/!important/);
    const i2b = appCss.indexOf("Batch 2B:");
    const i1b = appCss.indexOf("Batch 1B:");
    const i3 = appCss.indexOf("Batch 3A+3B+3C:");
    expect(i2b).toBeGreaterThan(-1);
    expect(i1b).toBeGreaterThan(i2b);
    expect(i3).toBeGreaterThan(i1b);
  });

  test("filter and refresh hooks on target pages remain unchanged by this batch", () => {
    expect(statusPo).toMatch(/data-testid="status-po-filter-reset"/);
    expect(statusPo).toMatch(/data-testid="status-po-refresh"/);
    expect(delivery).toMatch(/data-testid="ds-filter-reset"/);
    expect(delivery).toMatch(/data-testid="ds-refresh"/);
    expect(prediksi).toMatch(/data-testid="prediksi-filter-reset"/);
    expect(prediksi).toMatch(/data-testid="prediksi-refresh"/);
  });
});
