import fs from "fs";
import path from "path";

/**
 * Contract tests for Batch 1A — desktop ops-list chrome normalization.
 * Protects cascade ownership + shared class architecture (not pixel snapshots).
 */
describe("Ops-list chrome Batch 1A", () => {
  const root = path.join(__dirname, "..");
  const appCss = fs.readFileSync(path.join(root, "App.css"), "utf8");
  const uiCss = fs.readFileSync(path.join(root, "ui-system.css"), "utf8");
  const indexCss = fs.readFileSync(path.join(root, "index.css"), "utf8");
  const statusPo = fs.readFileSync(path.join(root, "pages/StatusPO.jsx"), "utf8");
  const delivery = fs.readFileSync(path.join(root, "pages/DeliverySchedulePage.jsx"), "utf8");
  const prediksi = fs.readFileSync(path.join(root, "pages/PrediksiOrder.jsx"), "utf8");
  const tabs = fs.readFileSync(path.join(root, "components/WorkspaceTabs.jsx"), "utf8");
  const fixtures = fs.readFileSync(path.join(root, "lib/deliveryScheduleStatusLokasiFixtures.js"), "utf8");

  test("target pages keep shared ship-status / ship-table ops-list classes", () => {
    for (const src of [statusPo, delivery, prediksi]) {
      expect(src).toMatch(/ship-status-page/);
      expect(src).toMatch(/ship-status-toolbar/);
      expect(src).toMatch(/ship-filter-field/);
      expect(src).toMatch(/ship-table/);
    }
    expect(delivery).toMatch(/ship-th-sort/);
    expect(delivery).toMatch(/pill-ok/);
    expect(delivery).toMatch(/pill-warn/);
  });

  test("ui-system no longer forces pastel ship-table headers with !important", () => {
    expect(uiCss).not.toMatch(/\.ship-table\s+thead\s+th\s*\{[^}]*!important/s);
    expect(uiCss).not.toMatch(/\.ship-table\s+thead\s+th\s*\{[^}]*--mod-soft/s);
    expect(uiCss).toMatch(/Ops-list `\.ship-table` chrome is owned by App\.css/);
  });

  test("App.css owns industrial cool-slate ship-table header", () => {
    expect(appCss).toMatch(/--ship-head-bg:#475569/);
    expect(appCss).toMatch(/\.ship-table\s+thead\s+th\s*\{[^}]*var\(--ship-head-bg\)/s);
    expect(appCss).toMatch(/--ctrl-h-compact/);
    expect(appCss).toMatch(/\.ship-status-toolbar/);
    expect(appCss).toMatch(/\.ship-th-sort\.is-active/);
  });

  test("semantic location pills remain green/amber on ops-list", () => {
    expect(appCss).toMatch(/\.pill-ok\{background:#ECFDF5;color:#047857\}/);
    expect(appCss).toMatch(/\.pill-warn\{background:#FFFBEB;color:#B45309\}/);
    expect(appCss).toMatch(/\.ship-status-page \.pill-ok\{border-color:#A7F3D0\}/);
    expect(appCss).toMatch(/\.ship-status-page \.pill-warn\{border-color:#FCD34D\}/);
    expect(delivery).toMatch(/className=\{\"pill sm \"/);
  });

  test("compact control tokens exist for ops toolbar", () => {
    expect(indexCss).toMatch(/--radius-sm:\s*4px/);
    expect(indexCss).toMatch(/--ctrl-h-compact:\s*28px/);
  });

  test("preview fixtures stay development-gated and use dummy IDs", () => {
    expect(delivery).toMatch(/process\.env\.NODE_ENV\s*===\s*["']development["']/);
    expect(delivery).toMatch(/previewStatusLokasi/);
    expect(delivery).toMatch(/deliveryScheduleStatusLokasiFixtures/);
    expect(fixtures).toMatch(/DUMMY-PO-001/);
    expect(fixtures).toMatch(/DUMMY-PO-002/);
    expect(fixtures).toMatch(/DUMMY-PO-003/);
    expect(fixtures).toMatch(/DUMMY-PO-004/);
    expect(fixtures).not.toMatch(/\bfetch\(|\baxios\b|api\./);
  });

  test("workspace tabs component remains present and unchanged by this batch intent", () => {
    expect(tabs).toMatch(/kb-workspace-tab/);
    expect(tabs).toMatch(/WorkspaceTabs/);
    expect(appCss).toMatch(/\.kb-workspace-tabs\{/);
  });

  test("ui-system no longer overrides shared .pill-ok/.pill-warn colors", () => {
    expect(uiCss).not.toMatch(/\.pill-ok\s*,/);
    expect(uiCss).not.toMatch(/\.pill-warn\s*,\s*\.pill-hold/);
  });
});
