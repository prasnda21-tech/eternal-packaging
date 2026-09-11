import fs from "fs";
import path from "path";
import {
  DELIVERY_SORT_KEYS,
  sortDeliveryRows,
} from "../lib/deliverySchedule";
import { locationSetupStatusLabel } from "../lib/deliveryRouteBridge";
import {
  STATUS_LOKASI_SORT_DUMMY_ROWS,
  TABLE_UI_PREVIEW_DUMMY_ROWS,
} from "../lib/deliveryScheduleStatusLokasiFixtures";

/**
 * Contract tests for Batch 1B — desktop `.ship-table` interaction density.
 * Protects header/hover/selected/sort/preview contracts (not pixel snapshots).
 */
describe("Ops-list tables Batch 1B", () => {
  const root = path.join(__dirname, "..");
  const appCss = fs.readFileSync(path.join(root, "App.css"), "utf8");
  const uiCss = fs.readFileSync(path.join(root, "ui-system.css"), "utf8");
  const delivery = fs.readFileSync(path.join(root, "pages/DeliverySchedulePage.jsx"), "utf8");
  const statusPo = fs.readFileSync(path.join(root, "pages/StatusPO.jsx"), "utf8");
  const prediksi = fs.readFileSync(path.join(root, "pages/PrediksiOrder.jsx"), "utf8");
  const tabs = fs.readFileSync(path.join(root, "components/WorkspaceTabs.jsx"), "utf8");
  const fixtures = fs.readFileSync(
    path.join(root, "lib/deliveryScheduleStatusLokasiFixtures.js"),
    "utf8",
  );

  /** Slice App.css ship-table interaction block for selector ownership checks. */
  function shipTableCssSlice() {
    const start = appCss.indexOf("/* ---- Batch 1B: ops-list `.ship-table` interaction density ---- */");
    const end = appCss.indexOf(".workflow-table-wrap");
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    return appCss.slice(start, end);
  }

  test("ship-table header cool-slate contract remains intact", () => {
    expect(appCss).toMatch(/--ship-head-bg:#475569/);
    expect(appCss).toMatch(/\.ship-table\s+thead\s+th\s*\{[^}]*var\(--ship-head-bg\)/s);
    expect(appCss).toMatch(/\.ship-table\s+thead\s+th\s*\{[^}]*position:sticky/s);
    expect(appCss).toMatch(/\.ship-table\s+thead\s+th\s*\{[^}]*text-transform:uppercase/s);
  });

  test("selected-row styling contract exists and wins over hover", () => {
    const slice = shipTableCssSlice();
    expect(slice).toMatch(/tr:hover td\{background:#EEF2F7\}/);
    expect(slice).toMatch(/tr\.is-selected td\{[\s\S]*?background:#E2E8F0/);
    expect(slice).toMatch(/tr\.is-selected:hover td\{background:#D9E2EC\}/);
    const hoverIdx = slice.indexOf("tr:hover td{background:#EEF2F7}");
    const selectedIdx = slice.indexOf("tr.is-selected td{");
    expect(selectedIdx).toBeGreaterThan(hoverIdx);
    expect(statusPo).toMatch(/is-selected/);
    expect(delivery).toMatch(/is-selected/);
  });

  test("sortable header affordance includes focus-visible and active state", () => {
    const slice = shipTableCssSlice();
    expect(slice).toMatch(/\.ship-th-sort:focus-visible/);
    expect(slice).toMatch(/\.ship-th-sort\.is-active/);
    expect(delivery).toMatch(/ship-th-sort/);
    expect(delivery).toMatch(/is-active/);
  });

  test("Status Lokasi ASC/DESC unchanged on original dummy POs", () => {
    const dummy = STATUS_LOKASI_SORT_DUMMY_ROWS;
    const asc = sortDeliveryRows(dummy, {
      key: DELIVERY_SORT_KEYS.location_status,
      direction: "asc",
    });
    expect(asc.map((r) => locationSetupStatusLabel(r))).toEqual([
      "Belum Diatur",
      "Belum Diatur",
      "Sudah Diatur",
      "Sudah Diatur",
    ]);
    expect(asc.map((r) => r.order_number)).toEqual([
      "DUMMY-PO-001",
      "DUMMY-PO-003",
      "DUMMY-PO-002",
      "DUMMY-PO-004",
    ]);

    const desc = sortDeliveryRows(dummy, {
      key: DELIVERY_SORT_KEYS.location_status,
      direction: "desc",
    });
    expect(desc.map((r) => locationSetupStatusLabel(r))).toEqual([
      "Sudah Diatur",
      "Sudah Diatur",
      "Belum Diatur",
      "Belum Diatur",
    ]);
    expect(desc.map((r) => r.order_number)).toEqual([
      "DUMMY-PO-002",
      "DUMMY-PO-004",
      "DUMMY-PO-001",
      "DUMMY-PO-003",
    ]);
  });

  test("dev preview cannot activate in production and uses table dummy IDs", () => {
    expect(delivery).toMatch(/process\.env\.NODE_ENV\s*===\s*["']development["']/);
    expect(delivery).toMatch(/previewStatusLokasi/);
    expect(delivery).toMatch(/TABLE_UI_PREVIEW_DUMMY_ROWS/);
    expect(fixtures).toMatch(/DUMMY-TABLE-PO-001/);
    expect(fixtures).toMatch(/DUMMY-TABLE-PO-005/);
    expect(TABLE_UI_PREVIEW_DUMMY_ROWS).toHaveLength(5);
    expect(fixtures).not.toMatch(/\bfetch\(|\baxios\b/);
  });

  test("prod-table selectors are untouched by Batch 1B ship-table block", () => {
    const slice = shipTableCssSlice();
    expect(slice).not.toMatch(/\.prod-table/);
    expect(uiCss).not.toMatch(/\.ship-table\s+thead\s+th\s*\{[^}]*!important/s);
  });

  test("workspace tab selectors remain present and outside ship-table edits", () => {
    expect(tabs).toMatch(/kb-workspace-tab/);
    expect(tabs).toMatch(/WorkspaceTabs/);
    const slice = shipTableCssSlice();
    expect(slice).not.toMatch(/kb-workspace-tab/);
  });

  test("checkbox and long-text ship-cust contracts exist on target pages", () => {
    expect(delivery).toMatch(/className="ship-check"/);
    expect(delivery).toMatch(/className="ship-cust"/);
    expect(statusPo).toMatch(/ship-check/);
    expect(statusPo).toMatch(/ship-cust/);
    expect(prediksi).toMatch(/ship-cust/);
    const slice = shipTableCssSlice();
    expect(slice).toMatch(/\.ship-cust\{/);
    expect(slice).toMatch(/text-overflow:ellipsis/);
    expect(slice).toMatch(/td\.empty-state/);
  });
});
