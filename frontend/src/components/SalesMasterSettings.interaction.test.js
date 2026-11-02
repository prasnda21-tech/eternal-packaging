/**
 * @jest-environment jsdom
 */
import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesMasterList: jest.fn(),
    salesMasterCreate: jest.fn(),
    salesMasterUpdate: jest.fn(),
    salesMasterAliases: jest.fn(async () => ({ items: [] })),
    adminListUsers: jest.fn(),
    branches: jest.fn(),
    crmDailyReportOptionsSettings: jest.fn(),
    updateCrmDailyReportOptionsSettings: jest.fn(),
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { id: "u-test", name: "Tester" }, hasPerm: () => true }),
}));

import api from "../lib/api";
import SalesMasterSettings from "./SalesMasterSettings";
import CrmDailyReportOptionsSettings from "./CrmDailyReportOptionsSettings";

function typeCharByChar(input, text) {
  let value = "";
  for (const ch of text) {
    value += ch;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe(value);
  }
  return value;
}

describe("Master Sales / CRM options continuous typing", () => {
  let container;
  let root;

  beforeEach(() => {
    api.salesMasterList.mockResolvedValue({ items: [] });
    api.salesMasterAliases.mockResolvedValue({ items: [] });
    api.adminListUsers.mockResolvedValue({ users: [] });
    api.branches.mockResolvedValue({ items: [] });
    api.crmDailyReportOptionsSettings.mockResolvedValue({
      settings: {
        doc_version: 1,
        no_order_reasons: [{ id: "__other__", label: "Lainnya", active: true, sort_order: 9999, is_other: true }],
        complaint_categories: [{ id: "__other__", label: "Lainnya", active: true, sort_order: 9999, is_other: true }],
        issue_categories: [{ id: "__other__", label: "Lainnya", active: true, sort_order: 9999, is_other: true }],
      },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  test("SalesMaster name input continuous typing ABCDE then 123", async () => {
    await act(async () => {
      root.render(<SalesMasterSettings canEdit notify={() => {}} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    await act(async () => {
      container.querySelector('[data-testid="sales-master-add"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const name = document.querySelector('[data-testid="sales-master-name"]');
    expect(name).toBeTruthy();
    expect(document.querySelector('[data-testid="sales-master-drawer"]')).toBeTruthy();
    name.focus();
    typeCharByChar(name, "ABCDE");
    expect(name.value).toBe("ABCDE");
    let value = "ABCDE";
    for (const ch of "123") {
      value += ch;
      act(() => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(name, value);
        name.dispatchEvent(new Event("input", { bubbles: true }));
      });
      expect(document.activeElement).toBe(name);
      expect(name.value).toBe(value);
    }
    expect(name.value).toBe("ABCDE123");
    expect(document.querySelector('[data-testid="sales-master-name"]')).toBe(name);
  });

  test("rename warning requires ack before save", async () => {
    api.salesMasterList.mockResolvedValue({
      items: [{
        id: "s1",
        sales_name: "Ayub MS",
        user_id: null,
        branch_ids: [],
        active: true,
        sort_order: 10,
        doc_version: 1,
      }],
    });
    api.salesMasterUpdate.mockResolvedValue({ item: { id: "s1" } });
    await act(async () => {
      root.render(<SalesMasterSettings notify={jest.fn()} canEdit />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      container.querySelector('[data-testid="sales-master-edit-s1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const name = document.querySelector('[data-testid="sales-master-name"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(name, "Ayub Renamed");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(document.querySelector('[data-testid="sales-master-rename-warning"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="sales-master-save"]').disabled).toBe(true);
    await act(async () => {
      document.querySelector('[data-testid="sales-master-save"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(api.salesMasterUpdate).not.toHaveBeenCalled();

    await act(async () => {
      document.querySelector('[data-testid="sales-master-cancel"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(document.querySelector('[data-testid="sales-master-drawer"]')).toBeNull();
    expect(api.salesMasterUpdate).not.toHaveBeenCalled();

    api.salesMasterUpdate.mockClear();
    await act(async () => {
      container.querySelector('[data-testid="sales-master-edit-s1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const name2 = document.querySelector('[data-testid="sales-master-name"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(name2, "Ayub Renamed");
      name2.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(document.querySelector('[data-testid="sales-master-save"]').disabled).toBe(true);
    await act(async () => {
      const ack = document.querySelector('[data-testid="sales-master-rename-ack"]');
      // Mimic user toggle: native click updates checked then React onChange reads it.
      ack.click();
    });
    expect(document.querySelector('[data-testid="sales-master-rename-ack"]').checked).toBe(true);
    expect(document.querySelector('[data-testid="sales-master-save"]').disabled).toBe(false);
    await act(async () => {
      document.querySelector('[data-testid="sales-master-save"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    expect(api.salesMasterUpdate).toHaveBeenCalled();
    const payload = api.salesMasterUpdate.mock.calls[0][1];
    expect(payload.sales_name).toBe("Ayub Renamed");
    expect(payload.doc_version).toBe(1);
  });

  test("CRM option label continuous typing", async () => {
    await act(async () => {
      root.render(<CrmDailyReportOptionsSettings canEdit notify={() => {}} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    await act(async () => {
      container.querySelector('[data-testid="crm-opt-add-no_order_reasons"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const label = container.querySelector('[data-testid="crm-opt-label-no_order_reasons-0"]');
    expect(label).toBeTruthy();
    label.focus();
    typeCharByChar(label, "ABCDE");
    let value = "ABCDE";
    for (const ch of "123") {
      value += ch;
      act(() => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(label, value);
        label.dispatchEvent(new Event("input", { bubbles: true }));
      });
      expect(document.activeElement).toBe(label);
      expect(label.value).toBe(value);
    }
    expect(label.value).toBe("ABCDE123");
    expect(container.querySelector('[data-testid="crm-opt-label-no_order_reasons-0"]')).toBe(label);
  });
});
