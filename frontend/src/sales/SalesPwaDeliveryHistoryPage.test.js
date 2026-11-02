/**
 * @jest-environment jsdom
 */
import React from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import SalesPwaDeliveryHistoryPage from "./SalesPwaDeliveryHistoryPage";
import {
  HISTORY_SEARCH_DEBOUNCE_MS,
  defaultHistoryFilters,
  historyDatePresetRange,
} from "../lib/salesPwaDeliveryHistory";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesPwaDeliveryHistory: jest.fn(),
    salesPwaDocumentFile: jest.fn(),
  },
}));

jest.mock("../components/deliveryPlan/DeliveryPlanDateRangeField", () => ({
  __esModule: true,
  default: function MockDateRange({ dateFrom, dateTo, onApply, testIdPrefix }) {
    return (
      <div data-testid={testIdPrefix}>
        <span data-testid={`${testIdPrefix}-value`}>{dateFrom || ""}|{dateTo || ""}</span>
        <button
          type="button"
          data-testid={`${testIdPrefix}-apply`}
          onClick={() => onApply("2026-10-01", "2026-10-15")}
        >
          Apply
        </button>
      </div>
    );
  },
}));

const api = require("../lib/api").default;

function setInput(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function row(id, overrides = {}) {
  return {
    id,
    order_id: "po-1",
    confirmed_at: "2026-11-02T10:00:00+07:00",
    confirmed_day: "2026-11-02",
    customer: "Kopi Nusantara",
    outlet: "Outlet A",
    po_number: "PO-1024",
    sj_number: "SJ-2108",
    item_name: "Cup PET 16oz 11gr",
    qty: 2000,
    unit: "pcs",
    status: "Terkirim",
    status_label: "Terkirim",
    proofs: [],
    ...overrides,
  };
}

async function renderPage() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<SalesPwaDeliveryHistoryPage />);
  });
  return { host, root };
}

describe("SalesPwaDeliveryHistoryPage", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    api.salesPwaDeliveryHistory.mockReset();
    api.salesPwaDocumentFile.mockReset();
    api.salesPwaDeliveryHistory.mockResolvedValue({
      rows: [row("r1"), row("r2", { id: "r2", item_name: "Lid Flat 16oz", qty: 500 })],
      total: 2,
      skip: 0,
      limit: 25,
    });
  });

  afterEach(() => {
    jest.useRealTimers();
    document.body.innerHTML = "";
  });

  test("defaults to Bulan Ini WIB and marks the preset active", async () => {
    const expected = defaultHistoryFilters();
    const { host, root } = await renderPage();
    await act(async () => { await Promise.resolve(); });
    const first = api.salesPwaDeliveryHistory.mock.calls[0][0];
    expect(first.date_from).toBe(expected.date_from);
    expect(first.date_to).toBe(expected.date_to);
    expect(first.customer_q).toBeUndefined();
    expect(first.item_q).toBeUndefined();
    expect(host.querySelector('[data-testid="sales-hist-preset-month"]').getAttribute("aria-pressed")).toBe("true");
    expect(host.querySelector('[data-testid="sales-hist-date-value"]').textContent).toBe(
      `${expected.date_from}|${expected.date_to}`,
    );
    await act(async () => { root.unmount(); });
  });

  test("custom range is respected and Reset returns to Bulan Ini", async () => {
    const { host, root } = await renderPage();
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      host.querySelector('[data-testid="sales-hist-date-apply"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    const custom = api.salesPwaDeliveryHistory.mock.calls.at(-1)[0];
    expect(custom.date_from).toBe("2026-10-01");
    expect(custom.date_to).toBe("2026-10-15");
    expect(host.querySelector('[data-testid="sales-hist-preset-month"]').getAttribute("aria-pressed")).toBe("false");

    await act(async () => {
      host.querySelector('[data-testid="sales-hist-reset"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    const resetParams = api.salesPwaDeliveryHistory.mock.calls.at(-1)[0];
    const month = historyDatePresetRange("month");
    expect(resetParams.date_from).toBe(month.date_from);
    expect(resetParams.date_to).toBe(month.date_to);
    expect(host.querySelector('[data-testid="sales-hist-preset-month"]').getAttribute("aria-pressed")).toBe("true");

    await act(async () => { root.unmount(); });
  });

  test("loads history table and opens detail drawer", async () => {
    const { host, root } = await renderPage();
    await act(async () => { await Promise.resolve(); });
    expect(host.querySelector('[data-testid="sales-pwa-delivery-history"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="sales-hist-table"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="sales-hist-scroll"]')).toBeTruthy();
    expect(host.textContent).toContain("Cup PET 16oz 11gr");

    await act(async () => {
      host.querySelector('[data-testid="sales-hist-detail-r1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(host.querySelector('[data-testid="sales-hist-detail"]')).toBeTruthy();

    await act(async () => { root.unmount(); });
  });

  test("debounces live search and ignores stale responses", async () => {
    let resolveSlow;
    api.salesPwaDeliveryHistory.mockReset();
    api.salesPwaDeliveryHistory
      .mockResolvedValueOnce({ rows: [row("first")], total: 1, skip: 0, limit: 25 })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSlow = resolve; }))
      .mockResolvedValue({ rows: [row("fresh", { item_name: "Fresh Item" })], total: 1, skip: 0, limit: 25 });

    const { host, root } = await renderPage();
    await act(async () => { await Promise.resolve(); });

    const itemInput = host.querySelector('[data-testid="sales-hist-filter-item"]');
    await act(async () => { setInput(itemInput, "pet 16"); });
    await act(async () => {
      jest.advanceTimersByTime(HISTORY_SEARCH_DEBOUNCE_MS);
      await Promise.resolve();
    });
    expect(api.salesPwaDeliveryHistory.mock.calls.at(-1)[0].item_q).toBe("pet 16");
    expect(api.salesPwaDeliveryHistory.mock.calls.at(-1)[0].skip).toBe(0);

    await act(async () => {
      host.querySelector('[data-testid="sales-hist-date-apply"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await act(async () => {
      resolveSlow({ rows: [row("stale", { item_name: "STALE ROW" })], total: 1, skip: 0, limit: 25 });
      await Promise.resolve();
    });
    expect(host.textContent).not.toContain("STALE ROW");
    expect(host.textContent).toContain("Fresh Item");

    await act(async () => { root.unmount(); });
  });

  test("mobile filter CSS stays compact in two rows outside table scroll", () => {
    const fs = require("fs");
    const path = require("path");
    const src = fs.readFileSync(path.join(__dirname, "SalesPwaDeliveryHistoryPage.jsx"), "utf8");
    const css = fs.readFileSync(path.join(__dirname, "salesPwa.css"), "utf8");
    expect(src).toMatch(/sales-hist-mobile-filters/);
    expect(src).toMatch(/defaultHistoryFilters/);
    expect(src).toMatch(/sales-hist-preset-\$\{preset\.id\}/);
    expect(src).toMatch(/id: "month"/);
    expect(src).toMatch(/spwa-hist-col-po/);
    expect(css).toMatch(/\.spwa-hist-mobile-filters\s*\{[^}]*grid-template-rows:\s*auto auto/s);
    expect(css).toMatch(/\.spwa-hist-desktop-filters\s*\{[^}]*display:\s*none/s);
    expect(css).toMatch(/\.spwa-hist-scroll\s*\{[^}]*overflow-x:\s*auto/s);
    expect(css).toMatch(/\.spwa-hist-scroll\s*\{[^}]*overflow-y:\s*auto/s);
    expect(css).toMatch(/\.spwa-hist-table\s*\{[^}]*table-layout:\s*auto/s);
    expect(css).not.toMatch(/\.spwa-hist-table\s*\{[^}]*table-layout:\s*fixed/s);
    expect(css).toMatch(/\.spwa-hist-table th,\s*\.spwa-hist-table td\s*\{[^}]*border-right:\s*1px solid/s);
    expect(css).toMatch(/\.spwa-hist-table th\s*\{[^}]*position:\s*sticky/s);
    expect(css).toMatch(/\.spwa-hist-toolbar\s*\{[^}]*flex-direction:\s*column/s);
  });
});
