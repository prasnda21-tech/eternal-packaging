/**
 * @jest-environment jsdom
 */
import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";
import {
  SALES_MASTER_DEFAULT_COL_ORDER,
  clearSalesMasterColPrefs,
  salesMasterColStorageKey,
} from "../lib/salesMasterColumns";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesMasterList: jest.fn(),
    salesMasterCreate: jest.fn(),
    salesMasterUpdate: jest.fn(),
    adminListUsers: jest.fn(),
    branches: jest.fn(),
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { id: "u-test", name: "Tester" }, hasPerm: () => true }),
}));

import api from "../lib/api";
import SalesMasterSettings from "./SalesMasterSettings";

const SAMPLE = {
  items: [
    { id: "s1", sales_name: "Ayub MS", branch_ids: ["jkt"], user_id: "u1", active: true, sort_order: 10, doc_version: 1 },
    { id: "s2", sales_name: "Vira", branch_ids: [], user_id: null, active: false, sort_order: 20, doc_version: 1 },
    { id: "s3", sales_name: "Budi", branch_ids: ["sby"], user_id: "u2", active: true, sort_order: 5, doc_version: 1 },
  ],
};

describe("SalesMasterSettings interactive columns", () => {
  let container;
  let root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    window.localStorage.clear();
    clearSalesMasterColPrefs(window.localStorage, "u-test");
    api.salesMasterList.mockResolvedValue(SAMPLE);
    api.adminListUsers.mockResolvedValue({
      users: [
        { id: "u1", name: "User One", email: "a@x.com", role: "sales", status: "active" },
        { id: "u2", name: "User Two", email: "b@x.com", role: "sales", status: "active" },
      ],
    });
    api.branches.mockResolvedValue({
      items: [
        { id: "jkt", name: "Jakarta", active: true },
        { id: "sby", name: "Surabaya", active: true },
      ],
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    document.querySelectorAll(".sm-filter-popover").forEach((el) => el.remove());
  });

  async function render() {
    await act(async () => {
      root.render(<SalesMasterSettings canEdit notify={() => {}} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  }

  test("header/row data-col stay aligned after keyboard reorder", async () => {
    await render();
    const move = container.querySelector('[data-testid="sm-move-status"]');
    expect(move).toBeTruthy();
    act(() => {
      move.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    });
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.getAttribute("data-col"));
    expect(headers).toContain("status");
    expect(headers.indexOf("status")).toBeLessThan(headers.indexOf("user"));
    const cells = [...container.querySelectorAll('[data-testid="sm-row-s1"] td')].map((td) => td.getAttribute("data-col"));
    expect(cells).toEqual(headers);
    expect(container.querySelector('[data-testid="sm-row-s1"] [data-col="name"]')?.textContent).toContain("Ayub MS");
    expect(JSON.parse(window.localStorage.getItem(salesMasterColStorageKey("u-test"))).order).toEqual(headers);
  });

  test("name filter and reset filters", async () => {
    await render();
    act(() => {
      container.querySelector('[data-testid="sm-filter-name"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const input = document.querySelector('[data-testid="sm-filter-value-name"]');
    expect(input).toBeTruthy();
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "Ayub");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      document.querySelector('[data-testid="sm-filter-apply-name"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="sm-row-s1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sm-row-s2"]')).toBeNull();
    act(() => {
      container.querySelector('[data-testid="sales-master-reset-filters"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="sm-row-s2"]')).toBeTruthy();
  });

  test("status filter AND with loaded rows", async () => {
    await render();
    act(() => {
      container.querySelector('[data-testid="sm-filter-status"]').click();
    });
    const statusSel = document.querySelector('[data-testid="sm-filter-value-status"]');
    act(() => {
      statusSel.value = "inactive";
      statusSel.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      document.querySelector('[data-testid="sm-filter-apply-status"]').click();
    });
    expect(container.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(container.querySelector('[data-testid="sm-row-s2"]')).toBeTruthy();
  });

  test("reset tampilan restores default order", async () => {
    await render();
    act(() => {
      container.querySelector('[data-testid="sm-move-name"]').dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
      );
    });
    act(() => {
      container.querySelector('[data-testid="sales-master-reset-layout"]').click();
    });
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.getAttribute("data-col"));
    expect(headers).toEqual(SALES_MASTER_DEFAULT_COL_ORDER);
    expect(window.localStorage.getItem(salesMasterColStorageKey("u-test"))).toBeNull();
  });

  test("resize handle updates stored width with minimum clamp", async () => {
    await render();
    const handle = container.querySelector('[data-testid="sm-resize-name"]');
    expect(handle).toBeTruthy();
    const Ev = window.PointerEvent || window.MouseEvent;
    const fire = (type, clientX) => {
      handle.dispatchEvent(new Ev(type, { pointerId: 1, button: 0, clientX, bubbles: true }));
    };
    act(() => { fire("pointerdown", 200); });
    act(() => { fire("pointermove", 40); });
    act(() => { fire("pointerup", 40); });
    const stored = JSON.parse(window.localStorage.getItem(salesMasterColStorageKey("u-test")));
    expect(stored.widths.name).toBeGreaterThanOrEqual(160);
  });

  test("edit button still targets correct row after reorder", async () => {
    await render();
    act(() => {
      container.querySelector('[data-testid="sm-move-name"]').dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
      );
    });
    act(() => {
      container.querySelector('[data-testid="sm-move-status"]').dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }),
      );
    });
    act(() => {
      container.querySelector('[data-testid="sales-master-edit-s2"]').click();
    });
    expect(document.querySelector('[data-testid="sales-master-drawer"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="sales-master-name"]').value).toBe("Vira");
  });
});
