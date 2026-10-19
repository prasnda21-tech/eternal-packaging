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
    crmDailyReportSales: jest.fn(),
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
      "po.view",
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

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 160));
  });
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
    api.crmDailyReportOrders.mockResolvedValue({
      orders: [
        {
          id: "ord-1",
          po_number: "PO/TEST/001",
          customer: "Toko Alpha",
          sales_verified: true,
          sales_user_id: "sales-ayub",
          sales_label_resolved: "Sholahudin Al - Ayubi",
          sales: "Sholahudin Al - Ayubi",
        },
      ],
    });
    api.crmDailyReportSales.mockResolvedValue({
      sales: [
        { user_id: "sales-ayub", label: "Sholahudin Al - Ayubi", name: "Ayub", email: "ayub@eternal.com" },
        { user_id: "sales-euis", label: "Euis Risnawati", name: "Euis", email: "euis@eternal.com" },
      ],
    });
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
    await flush();

    const wa = container.querySelector('[data-testid="cdr-whatsapp"]');
    const sales = container.querySelector('[data-testid="cdr-sales"]');
    const outlet = container.querySelector('[data-testid="cdr-outlet-name"]');
    const po = container.querySelector('[data-testid="cdr-po-search"]');
    expect(wa).toBeTruthy();
    expect(sales).toBeTruthy();
    expect(po).toBeTruthy();

    wa.focus();
    typeCharByChar(wa, "0812345");
    expect(wa.value).toBe("0812345");

    outlet.focus();
    typeCharByChar(outlet, "Toko");
    expect(outlet.value).toBe("Toko");

    sales.focus();
    typeCharByChar(sales, "AYUB");
    expect(sales.value).toBe("AYUB");

    po.focus();
    typeCharByChar(po, "PO/");
    expect(po.value).toBe("PO/");

    const statusOrder = container.querySelector('[data-testid="cdr-status-order"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
      setter.call(statusOrder, "TIDAK ORDER");
      statusOrder.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const reasonSelect = container.querySelector('[data-testid="cdr-no-order-reason"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
      setter.call(reasonSelect, "Lainnya");
      reasonSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const reasonLainnya = container.querySelector('[data-testid="cdr-no-order-reason-lainnya"]');
    reasonLainnya.focus();
    typeTextareaCharByChar(reasonLainnya, "Budget");
    expect(reasonLainnya.value).toBe("Budget");

    expect(container.querySelector('[data-testid="cdr-whatsapp"]')).toBe(wa);
    expect(container.querySelector('[data-testid="cdr-sales"]')).toBe(sales);
  });

  test("PO picker autofills sales; TIDAK ORDER shows manual option", async () => {
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await flush();

    const po = container.querySelector('[data-testid="cdr-po-search"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(po, "PO");
      po.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await flush();
    const poBtn = container.querySelector('[data-testid="cdr-po-results"] button');
    expect(poBtn).toBeTruthy();
    await act(async () => {
      poBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="cdr-po-selected"]')?.textContent).toMatch(/PO\/TEST\/001/);
    expect(container.querySelector('[data-testid="cdr-sales"]').value).toMatch(/Ayubi|Ayub/i);
    expect(container.querySelector('[data-testid="cdr-sales"]').disabled).toBe(true);

    await act(async () => {
      container.querySelector('[data-testid="cdr-po-clear"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const statusOrder = container.querySelector('[data-testid="cdr-status-order"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
      setter.call(statusOrder, "TIDAK ORDER");
      statusOrder.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const sales = container.querySelector('[data-testid="cdr-sales"]');
    await act(async () => {
      sales.focus();
      sales.dispatchEvent(new Event("focus", { bubbles: true }));
    });
    await flush();
    const manualOpt = container.querySelector('[data-testid="cdr-sales-manual-option"]');
    expect(manualOpt).toBeTruthy();
    expect(manualOpt.textContent).toMatch(/\+\s*Input Manual \/ Sales Lainnya/);
    await act(async () => {
      manualOpt.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="cdr-sales-manual-box"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-sales-manual-name"]')).toBeTruthy();
    expect(container.textContent).toMatch(/Nama Sales Manual/);
    expect(container.querySelector('[data-testid="cdr-sales-back-to-list"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="cdr-sales-back-to-list"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="cdr-sales"]')).toBeTruthy();
  });

  test("save uses Simpan Laporan only and posts payload", async () => {
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await flush();

    expect(container.textContent).not.toMatch(/Simpan Draft|Kirim Laporan/);

    const wa = container.querySelector('[data-testid="cdr-whatsapp"]');
    const sales = container.querySelector('[data-testid="cdr-sales"]');
    await act(async () => {
      const setInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setInput.call(wa, "081212345678");
      wa.dispatchEvent(new Event("input", { bubbles: true }));
      setInput.call(sales, "Ayub");
      sales.dispatchEvent(new Event("input", { bubbles: true }));
      sales.dispatchEvent(new Event("focus", { bubbles: true }));
    });
    await flush();
    const salesBtn = container.querySelector('[data-testid="cdr-sales-results"] button');
    expect(salesBtn).toBeTruthy();
    await act(async () => {
      salesBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await act(async () => {
      const form = container.querySelector('[data-testid="crm-daily-report-form"]');
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();

    expect(api.createCrmDailyReport).toHaveBeenCalled();
    const payload = api.createCrmDailyReport.mock.calls[0][0];
    expect(payload.whatsapp).toBe("081212345678");
    expect(payload.sales_user_id).toBe("sales-ayub");
    expect(payload.sales_source).toBe("existing");
    expect(payload.status_customer).toBe("New");
  });
});
