/**
 * @jest-environment jsdom
 */
import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

const mockNavigate = jest.fn();
const mockParams = { id: undefined };
const mockSearchParams = new URLSearchParams();

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useParams: () => mockParams,
  useSearchParams: () => [mockSearchParams],
  Link: ({ to, children, ...rest }) => <a href={to} {...rest}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    crmDailyReportMeta: jest.fn(),
    crmDailyReportCustomers: jest.fn(),
    crmDailyReportOrders: jest.fn(),
    createCrmDailyReport: jest.fn(),
    getCrmDailyReport: jest.fn(),
    updateCrmDailyReport: jest.fn(),
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", name: "Andi Dharma", role: "crm" },
    hasPerm: (k) => [
      "crm.daily_report.create",
      "crm.daily_report.view_own",
      "crm.daily_report.edit_own",
    ].includes(k),
  }),
}));

import api from "../lib/api";
import CrmDailyReportForm from "./CrmDailyReportForm";

function typeCharByChar(input, text) {
  let value = "";
  for (const ch of text) {
    value += ch;
    act(() => {
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      nativeInputValueSetter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe(value);
  }
}

function typeTextareaCharByChar(input, text) {
  let value = "";
  for (const ch of text) {
    value += ch;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe(value);
  }
}

describe("CrmDailyReportForm interaction", () => {
  let container;
  let root;

  beforeEach(() => {
    mockParams.id = undefined;
    mockNavigate.mockReset();
    api.crmDailyReportMeta.mockResolvedValue({
      today_wib: "2026-04-16",
      can_create: true,
      can_view_all: false,
      can_edit_own: true,
      reporter: { id: "u1", name: "Andi Dharma" },
    });
    api.crmDailyReportCustomers.mockResolvedValue({ customers: [] });
    api.crmDailyReportOrders.mockResolvedValue({ orders: [] });
    api.createCrmDailyReport.mockImplementation(async (payload) => ({ report: { id: "r1", ...payload } }));
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("continuous typing keeps focus and accumulates values without remount", async () => {
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await act(async () => { await Promise.resolve(); });

    const wa = container.querySelector('[data-testid="cdr-whatsapp"]');
    const sales = container.querySelector('[data-testid="cdr-sales"]');
    const outlet = container.querySelector('[data-testid="cdr-outlet-name"]');
    expect(wa).toBeTruthy();
    expect(sales).toBeTruthy();

    wa.focus();
    typeCharByChar(wa, "0812345");
    expect(wa.value).toBe("0812345");

    outlet.focus();
    typeCharByChar(outlet, "Toko");
    expect(outlet.value).toBe("Toko");

    sales.focus();
    typeCharByChar(sales, "AYUB");
    expect(sales.value).toBe("AYUB");

    await act(async () => {
      container.querySelector('[data-testid="cdr-order-no"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const reason = container.querySelector('[data-testid="cdr-no-order-reason"]');
    expect(reason).toBeTruthy();
    reason.focus();
    typeTextareaCharByChar(reason, "Budget");
    expect(reason.value).toBe("Budget");

    expect(container.querySelector('[data-testid="cdr-whatsapp"]')).toBe(wa);
    expect(container.querySelector('[data-testid="cdr-sales"]')).toBe(sales);
  });

  test("conditional New/Repeat and komplain/kendala gates", async () => {
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await act(async () => { await Promise.resolve(); });

    expect(container.querySelector('[data-testid="cdr-whatsapp"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-customer-search"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="cdr-status-repeat"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="cdr-customer-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-whatsapp"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="cdr-complaint-yes"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      container.querySelector('[data-testid="cdr-issue-yes"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="cdr-complaint-desc"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-issue-desc"]')).toBeTruthy();
  });

  test("save uses Simpan Laporan only and posts payload", async () => {
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await act(async () => { await Promise.resolve(); });

    expect(container.textContent).not.toMatch(/Simpan Draft|Kirim Laporan/);

    const wa = container.querySelector('[data-testid="cdr-whatsapp"]');
    const sales = container.querySelector('[data-testid="cdr-sales"]');
    await act(async () => {
      const setInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setInput.call(wa, "081212345678");
      wa.dispatchEvent(new Event("input", { bubbles: true }));
      setInput.call(sales, "AYUB");
      sales.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => {
      const form = container.querySelector('[data-testid="crm-daily-report-form"]');
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(api.createCrmDailyReport).toHaveBeenCalled();
    const payload = api.createCrmDailyReport.mock.calls[0][0];
    expect(payload.whatsapp).toBe("081212345678");
    expect(payload.sales_label).toBe("AYUB");
    expect(payload.status_customer).toBe("New");
  });
});
