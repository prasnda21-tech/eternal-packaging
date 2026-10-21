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

const mockHasPerm = (k) => [
  "crm.daily_report.create",
  "crm.daily_report.view_own",
  "crm.daily_report.edit_own",
  "po.view",
].includes(k);
const mockAuthUser = { id: "u1", name: "Andi Dharma", role: "crm" };

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: mockAuthUser,
    hasPerm: mockHasPerm,
  }),
}));

import api from "../lib/api";
import {
  buildCrmDailyDraftSnapshot,
  crmDailyDraftStorageKey,
  writeCrmDailyDraft,
} from "../lib/crmDailyReport";
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

async function flush(ms = 160) {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, ms));
  });
}

async function flushDraft() {
  await flush(350);
}

describe("CrmDailyReportForm interaction", () => {
  let container;
  let root;

  beforeEach(() => {
    mockParams.id = undefined;
    mockNavigate.mockReset();
    [...mockSearchParams.keys()].forEach((k) => mockSearchParams.delete(k));
    window.sessionStorage.clear();
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
    api.getCrmDailyReport.mockReset();
    api.updateCrmDailyReport.mockReset();
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

  test("create draft survives remount (list/tab navigation) and clears after success", async () => {
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await flush();
    const wa = container.querySelector('[data-testid="cdr-whatsapp"]');
    wa.focus();
    typeCharByChar(wa, "ABCDE123");
    await flushDraft();
    expect(window.sessionStorage.getItem("cdr-draft:v1:u1:create:new")).toBeTruthy();

    await act(async () => { root.unmount(); });
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await flush();
    expect(container.querySelector('[data-testid="cdr-draft-banner"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-whatsapp"]').value).toBe("ABCDE123");
    expect(document.activeElement).not.toBe(null);

    const sales = container.querySelector('[data-testid="cdr-sales"]');
    await act(async () => {
      const setInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setInput.call(sales, "Ayub");
      sales.dispatchEvent(new Event("input", { bubbles: true }));
      sales.dispatchEvent(new Event("focus", { bubbles: true }));
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="cdr-sales-results"] button')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="crm-daily-report-form"]')
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(api.createCrmDailyReport).toHaveBeenCalled();
    expect(window.sessionStorage.getItem("cdr-draft:v1:u1:create:new")).toBeNull();
  });

  test("failed submit keeps create draft", async () => {
    api.createCrmDailyReport.mockRejectedValueOnce({ response: { data: { detail: "server down" } } });
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await flush();
    const wa = container.querySelector('[data-testid="cdr-whatsapp"]');
    await act(async () => {
      const setInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setInput.call(wa, "08129999");
      wa.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const sales = container.querySelector('[data-testid="cdr-sales"]');
    await act(async () => {
      const setInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setInput.call(sales, "Ayub");
      sales.dispatchEvent(new Event("input", { bubbles: true }));
      sales.dispatchEvent(new Event("focus", { bubbles: true }));
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="cdr-sales-results"] button')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="crm-daily-report-form"]')
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(window.sessionStorage.getItem("cdr-draft:v1:u1:create:new")).toBeTruthy();
    expect(JSON.parse(window.sessionStorage.getItem("cdr-draft:v1:u1:create:new")).form.whatsapp).toBe("08129999");
  });

  test("edit shows server first and restores draft only after explicit choice", async () => {
    mockParams.id = "rep-1";
    const draftForm = {
      report_date: "2026-04-16",
      status_customer: "New",
      customer_id: "",
      outlet_name: "Draft Outlet",
      whatsapp: "08120000",
      order_id: "",
      po_number: "",
      sales_label: "Sholahudin Al - Ayubi",
      sales_user_id: "sales-ayub",
      sales_source: "existing",
      sales_choice: "sales-ayub",
      sales_locked_from_po: false,
      sales_note: "",
      status_order: "ORDER",
      no_order_reason: "",
      no_order_reason_choice: "",
      has_complaint: false,
      complaint_category: "",
      complaint_description: "",
      legacy_complaint: false,
      has_issue: false,
      issue_description: "",
      notes: "from-draft",
    };
    writeCrmDailyDraft(
      crmDailyDraftStorageKey({ id: "u1" }, "edit", "rep-1"),
      buildCrmDailyDraftSnapshot(draftForm, {
        customerQ: "", poQ: "", salesQ: "Sholahudin Al - Ayubi", selectedCustomer: null, selectedOrder: null,
      }),
    );
    api.getCrmDailyReport.mockResolvedValue({
      report: {
        id: "rep-1",
        report_date: "2026-04-16",
        status_customer: "New",
        outlet_name: "Server Outlet",
        whatsapp: "08121111",
        status_order: "ORDER",
        sales_label: "Sholahudin Al - Ayubi",
        sales_user_id: "sales-ayub",
        sales_source: "existing",
        notes: "from-server",
        can_edit: true,
      },
    });
    await act(async () => {
      root.render(<CrmDailyReportForm />);
    });
    await flush();
    expect(api.getCrmDailyReport).toHaveBeenCalledWith("rep-1");
    // Detail first (no ?edit=1): server data wins; open Edit to choose draft.
    expect(container.querySelector('[data-testid="crm-daily-report-detail"]')).toBeTruthy();
    expect(container.textContent).toMatch(/from-server|08121111|Server Outlet/);
    await act(async () => {
      container.querySelector('[data-testid="cdr-detail-edit"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="cdr-edit-draft-choice"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cdr-whatsapp"]').value).toBe("08121111");
    expect(container.querySelector('[data-testid="cdr-notes"]').value).toBe("from-server");
    await act(async () => {
      container.querySelector('[data-testid="cdr-draft-continue"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="cdr-whatsapp"]').value).toBe("08120000");
    expect(container.querySelector('[data-testid="cdr-notes"]').value).toBe("from-draft");
  });

  test("manual sales + reason Lainnya restore from draft", async () => {
    writeCrmDailyDraft(
      crmDailyDraftStorageKey({ id: "u1" }, "create"),
      buildCrmDailyDraftSnapshot({
        report_date: "2026-04-16",
        status_customer: "New",
        customer_id: "",
        outlet_name: "",
        whatsapp: "08123333",
        order_id: "",
        po_number: "",
        sales_label: "SalesX",
        sales_user_id: "",
        sales_source: "manual",
        sales_choice: "__manual__",
        sales_locked_from_po: false,
        sales_note: "",
        status_order: "TIDAK ORDER",
        no_order_reason: "Budget",
        no_order_reason_choice: "Lainnya",
        has_complaint: true,
        complaint_category: "CUP CACAT",
        complaint_description: "retak",
        legacy_complaint: false,
        has_issue: true,
        issue_description: "kirim telat",
        notes: "catatan",
      }, { salesQ: "", customerQ: "", poQ: "", selectedCustomer: null, selectedOrder: null }),
    );

    await act(async () => { root.render(<CrmDailyReportForm />); });
    await flush();
    expect(container.querySelector('[data-testid="cdr-whatsapp"]').value).toBe("08123333");
    expect(container.querySelector('[data-testid="cdr-sales-manual-name"]').value).toBe("SalesX");
    expect(container.querySelector('[data-testid="cdr-no-order-reason-lainnya"]').value).toBe("Budget");
    expect(container.querySelector('[data-testid="cdr-complaint-desc"]').value).toBe("retak");
    expect(container.querySelector('[data-testid="cdr-issue-desc"]').value).toBe("kirim telat");
  });
});
