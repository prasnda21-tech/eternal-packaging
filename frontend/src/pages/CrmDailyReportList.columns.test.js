/**
 * @jest-environment jsdom
 */
import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";
import {
  CRM_DAILY_COL_STORAGE_KEY,
  CRM_DAILY_DEFAULT_COL_ORDER,
  CRM_DAILY_DEFAULT_COL_WIDTHS,
  clearCrmDailyColPrefs,
} from "../lib/crmDailyReportColumns";

const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  Link: ({ to, children, ...rest }) => <a href={to} {...rest}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listCrmDailyReports: jest.fn(),
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", name: "CRM Preview", role: "crm" },
    hasPerm: (k) => [
      "crm.daily_report.create",
      "crm.daily_report.view_own",
      "crm.daily_report.edit_own",
      "nav.sales.crm_daily_report",
    ].includes(k),
  }),
}));

jest.setTimeout(15000);

import api from "../lib/api";
import CrmDailyReportList from "./CrmDailyReportList";

const SAMPLE = {
  items: [{
    id: "r1",
    report_date: "2026-04-16",
    created_by_name: "CRM Preview",
    outlet_name: "Toko Maju",
    whatsapp: "0812",
    po_number: "PO-1",
    sales_label: "AYUB",
    status_order: "ORDER",
    status_customer: "New",
    no_order_reason: "",
    has_complaint: false,
    has_issue: false,
    can_edit: true,
  }],
  total: 1,
  page: 1,
  page_count: 1,
  page_size: 10,
  summary: { total: 1, order_count: 1, no_order_count: 0, issue_count: 0 },
};

describe("CrmDailyReportList columns", () => {
  let container;
  let root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    window.localStorage.clear();
    clearCrmDailyColPrefs(window.localStorage);
    api.listCrmDailyReports.mockReset();
    api.listCrmDailyReports.mockResolvedValue(SAMPLE);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => { root.unmount(); });
    container.remove();
  });

  async function renderList() {
    act(() => { root.render(<CrmDailyReportList />); });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    // Ensure list API settled and table mounted
    for (let i = 0; i < 20 && !container.querySelector('[data-testid="crm-daily-report-list"]'); i += 1) {
      // eslint-disable-next-line no-await-in-loop
      await act(async () => { await Promise.resolve(); });
    }
  }

  test("header order matches row cell data-col alignment", async () => {
    await renderList();
    expect(container.querySelector('[data-testid="crm-daily-report-list"]')).toBeTruthy();
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.getAttribute("data-testid")?.replace("cdr-th-", ""));
    expect(headers).toEqual(CRM_DAILY_DEFAULT_COL_ORDER);
    const cells = [...container.querySelectorAll('[data-testid="cdr-row-r1"] td')].map((td) => td.getAttribute("data-col"));
    expect(cells).toEqual(headers);
    const row = container.querySelector('[data-testid="cdr-row-r1"]');
    expect(row.querySelector('[data-col="sales"]')?.textContent).toContain("AYUB");
    expect(row.querySelector('[data-col="customer"]')?.textContent).toContain("Toko Maju");
  });

  test("keyboard move reorders columns and keeps row alignment", async () => {
    await renderList();
    const moveSales = container.querySelector('[data-testid="cdr-move-sales"]');
    expect(moveSales).toBeTruthy();
    act(() => {
      moveSales.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    });
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.getAttribute("data-testid")?.replace("cdr-th-", ""));
    expect(headers.indexOf("sales")).toBe(headers.indexOf("po") - 1);
    const cells = [...container.querySelectorAll('[data-testid="cdr-row-r1"] td')].map((td) => td.getAttribute("data-col"));
    expect(cells).toEqual(headers);
    expect(JSON.parse(window.localStorage.getItem(CRM_DAILY_COL_STORAGE_KEY)).order).toEqual(headers);
  });

  test("reset columns restores excel default", async () => {
    await renderList();
    const moveSales = container.querySelector('[data-testid="cdr-move-sales"]');
    expect(moveSales).toBeTruthy();
    act(() => {
      moveSales.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
      moveSales.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    });
    act(() => {
      container.querySelector('[data-testid="cdr-reset-columns"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.getAttribute("data-testid")?.replace("cdr-th-", ""));
    expect(headers).toEqual(CRM_DAILY_DEFAULT_COL_ORDER);
    expect(window.localStorage.getItem(CRM_DAILY_COL_STORAGE_KEY)).toBeNull();
  });

  test("resize handle exists and updates stored width", async () => {
    await renderList();
    const handle = container.querySelector('[data-testid="cdr-resize-customer"]');
    expect(handle).toBeTruthy();
    const startX = 200;
    const Ev = window.PointerEvent || window.MouseEvent;
    const fire = (type, clientX) => {
      handle.dispatchEvent(new Ev(type, { pointerId: 1, button: 0, clientX, bubbles: true }));
    };
    act(() => {
      fire("pointerdown", startX);
      fire("pointermove", startX + 80);
      fire("pointerup", startX + 80);
    });
    const stored = JSON.parse(window.localStorage.getItem(CRM_DAILY_COL_STORAGE_KEY) || "{}");
    expect(stored.widths?.customer).toBeGreaterThan(CRM_DAILY_DEFAULT_COL_WIDTHS.customer);
  });

  test("header cells expose full label tooltip", async () => {
    await renderList();
    expect(container.querySelector('[data-testid="cdr-th-status_order"]')?.getAttribute("title")).toBe("Status Order");
    expect(container.querySelector('[data-testid="cdr-th-customer"]')?.getAttribute("title")).toBe("Customer/Outlet");
    expect(container.querySelector('[data-testid="cdr-table-shell"]')?.getAttribute("data-rows")).toBe("1");
  });

  test("filter controls remain after column ops", async () => {
    await renderList();
    expect(container.querySelector('[data-testid="cdr-filter-status-order"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-pager-label"]')?.textContent).toMatch(/1 laporan/);
  });

  test("empty state is compact when no rows", async () => {
    api.listCrmDailyReports.mockResolvedValue({
      ...SAMPLE,
      items: [],
      total: 0,
      summary: { total: 0, order_count: 0, no_order_count: 0, issue_count: 0 },
    });
    await renderList();
    expect(container.querySelector('[data-testid="cdr-empty-state"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-table-shell"]')?.getAttribute("data-rows")).toBe("0");
  });
});
