globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import HrPayroll from "./HrPayroll";
import api from "../lib/api";

const mockAuth = { perms: new Set() };

jest.mock("react-router-dom", () => ({
  useSearchParams: () => [new URLSearchParams(""), jest.fn()],
  useLocation: () => ({ pathname: "/hr-payroll/penggajian", search: "", state: null }),
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (key) => mockAuth.perms.has(key), user: { id: "ui-user", name: "UI" } }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    hrPayrollPeriods: jest.fn(),
    hrPayrollPeriod: jest.fn(),
    hrPayrollLines: jest.fn(),
    hrPayrollAttendance: jest.fn(),
    hrPayrollEmployees: jest.fn(),
    hrPayrollKasbonSchedules: jest.fn(),
    hrPayrollPeriodJournal: jest.fn(),
    hrPayrollAccountingReadiness: jest.fn(),
    hrPayrollPeriodPayment: jest.fn(),
    hrPayrollCancelPayment: jest.fn(),
    hrPayrollCreatePayment: jest.fn(),
    hrPayrollApprovePayment: jest.fn(),
    hrPayrollPostPayment: jest.fn(),
    hrPayrollReversePayment: jest.fn(),
    hrPayrollPieceIntegration: jest.fn(),
    hrPayrollSavePieceInputs: jest.fn(),
    cashBankAccounts: jest.fn(),
  },
}));

const period = {
  id: "period-ui",
  year: 2096,
  month: 4,
  start_date: "2096-04-01",
  end_date: "2096-04-30",
  status: "FINAL",
  period_number: "PR-2096-04",
  accounting_journal_id: "accrual-journal",
  accounting_posted_at: "2096-04-30T00:00:00Z",
};

function activeOf(rows) {
  return rows.find((row) => ["DRAFT", "APPROVED", "PAID"].includes(row.status)) || null;
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function buttonByText(container, text) {
  return [...container.querySelectorAll("button")].find((node) => node.textContent.includes(text));
}

function setControl(node, value) {
  const prototype = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value").set;
  setter.call(node, value);
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("payroll payment UI smoke", () => {
  let rows;

  beforeEach(() => {
    mockAuth.perms = new Set([
      "payroll.view",
      "payroll_payment.view",
      "payroll_payment.create",
      "payroll_payment.approve",
      "payroll_payment.post",
      "payroll_payment.reverse",
      "payroll_payment.cancel",
    ]);
    rows = [{
      id: "pay-1",
      payment_number: "PYB-1",
      status: "DRAFT",
      total_amount: 1000000,
      cash_bank_account_id: "bank-1",
      journal_number: null,
    }];
    api.hrPayrollPeriods.mockResolvedValue({ periods: [period] });
    api.hrPayrollPeriod.mockResolvedValue({ period });
    api.hrPayrollLines.mockResolvedValue({ lines: [{ id: "line-1", net: 1000000, gross: 1000000, total_deductions: 0, employee_snapshot: { name: "Andi" } }] });
    api.hrPayrollAttendance.mockResolvedValue({ attendance: [] });
    api.hrPayrollEmployees.mockResolvedValue({ employees: [] });
    api.hrPayrollKasbonSchedules.mockResolvedValue({ schedules: [] });
    api.hrPayrollPeriodJournal.mockResolvedValue({ journal_id: "accrual-journal", journal_number: "JE-ACCRUAL" });
    api.hrPayrollAccountingReadiness.mockResolvedValue({ readiness: { accounting_ready: true, summary: { gross: 1000000, net: 1000000 }, planned_lines: [] } });
    api.hrPayrollPeriodPayment.mockImplementation(async () => ({ payment: activeOf(rows), payments: rows }));
    api.cashBankAccounts.mockResolvedValue({ items: [{ id: "bank-1", name: "Bank UI", active: true }] });
    api.hrPayrollCancelPayment.mockImplementation(async (id, reason) => {
      const row = rows.find((item) => item.id === id);
      row.status = "CANCELLED";
      row.cancellation_reason = reason;
      return { payment: row };
    });
    api.hrPayrollCreatePayment.mockImplementation(async () => {
      const row = {
        id: `pay-${rows.length + 1}`,
        payment_number: `PYB-${rows.length + 1}`,
        status: "DRAFT",
        total_amount: 1000000,
        cash_bank_account_id: "bank-1",
        journal_number: null,
      };
      rows.push(row);
      return { payment: row };
    });
    api.hrPayrollApprovePayment.mockImplementation(async (id) => {
      const row = rows.find((item) => item.id === id);
      row.status = "APPROVED";
      return { payment: row };
    });
    api.hrPayrollPostPayment.mockImplementation(async (id) => {
      const row = rows.find((item) => item.id === id);
      row.status = "PAID";
      row.journal_number = "JE-20260930-001";
      return { journal_id: "journal-pay", journal_number: row.journal_number, payment_status: "PAID", message: "Pembayaran payroll diposting ke accounting." };
    });
    api.hrPayrollReversePayment.mockImplementation(async (id, reason) => {
      const row = rows.find((item) => item.id === id);
      row.status = "REVERSED";
      row.reversal_reason = reason;
      return { payment: row, payment_status: "REVERSED" };
    });
  });

  async function openPeriod() {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<HrPayroll mode="payroll" />);
    });
    await flush();
    const periodButton = buttonByText(container, "PR-2096-04");
    await act(async () => {
      periodButton.click();
    });
    await flush();
    return { container, root };
  }

  afterEach(() => {
    document.body.innerHTML = "";
  });

  test("cancel, recreate, approve, post, reload, reverse, and recreate", async () => {
    const { container } = await openPeriod();
    expect(container.querySelector("[data-testid='hr-payroll-cancel-payment']")).not.toBeNull();
    expect(container.querySelector("[data-testid='hr-payroll-payment-visibility-label']").textContent).toBe("Menunggu Persetujuan");
    expect(container.textContent).toMatch(/PYB-1 · Menunggu Persetujuan|PYB-1 · DRAFT/);

    await act(async () => {
      container.querySelector("[data-testid='hr-payroll-cancel-payment']").click();
    });
    const cancelReason = container.querySelector("[data-testid='hr-payment-cancel-reason']");
    await act(async () => {
      setControl(cancelReason, "bank salah");
    });
    await act(async () => {
      container.querySelector("[data-testid='hr-payroll-finance-confirm']").click();
    });
    await flush();
    expect(api.hrPayrollCancelPayment).toHaveBeenCalledWith("pay-1", "bank salah");
    expect(container.textContent).toContain("CANCELLED");
    expect(buttonByText(container, "Buat Payment")).toBeTruthy();

    const date = container.querySelector("[data-testid='hr-payment-date']");
    const bank = container.querySelector("[data-testid='hr-payment-bank']");
    await act(async () => {
      setControl(date, "2096-04-18");
      setControl(bank, "bank-1");
    });
    await act(async () => {
      buttonByText(container, "Buat Payment").click();
    });
    await act(async () => {
      container.querySelector("[data-testid='hr-payroll-finance-confirm']").click();
    });
    await flush();
    expect(api.hrPayrollCreatePayment).toHaveBeenCalled();
    expect(buttonByText(container, "Setujui")).toBeTruthy();

    await act(async () => {
      buttonByText(container, "Setujui").click();
    });
    await act(async () => {
      container.querySelector("[data-testid='hr-payroll-finance-confirm']").click();
    });
    await flush();
    expect(buttonByText(container, "Post Payment")).toBeTruthy();

    await act(async () => {
      buttonByText(container, "Post Payment").click();
    });
    await act(async () => {
      container.querySelector("[data-testid='hr-payroll-finance-confirm']").click();
    });
    await flush();
    expect(container.querySelector("[data-testid='hr-payroll-payment-journal-number']").textContent).toBe("JE-20260930-001");

    await act(async () => {
      api.hrPayrollPeriodPayment.mockClear();
      buttonByText(container, "PR-2096-04").click();
    });
    await flush();
    expect(api.hrPayrollPeriodPayment).toHaveBeenCalled();
    expect(container.querySelector("[data-testid='hr-payroll-payment-journal-number']").textContent).toBe("JE-20260930-001");

    await act(async () => {
      buttonByText(container, "Reverse").click();
    });
    const reverseReason = container.querySelector("[data-testid='hr-payment-reverse-reason']");
    await act(async () => {
      setControl(reverseReason, "koreksi bank");
    });
    await act(async () => {
      container.querySelector("[data-testid='hr-payroll-finance-confirm']").click();
    });
    await flush();
    expect(container.textContent).toContain("REVERSED");
    expect(container.textContent).toContain("CANCELLED");
    expect(buttonByText(container, "Buat Payment")).toBeTruthy();
  });

  test("cancel permission is separate from approve and post", async () => {
    mockAuth.perms = new Set(["payroll.view", "payroll_payment.approve", "payroll_payment.post"]);
    const withoutCancel = await openPeriod();
    expect(withoutCancel.container.querySelector("[data-testid='hr-payroll-cancel-payment']")).toBeNull();
    expect(buttonByText(withoutCancel.container, "Setujui")).toBeTruthy();
    await act(async () => {
      withoutCancel.root.unmount();
    });

    mockAuth.perms = new Set(["payroll.view", "payroll_payment.cancel"]);
    rows = [{
      id: "pay-1",
      payment_number: "PYB-1",
      status: "DRAFT",
      total_amount: 1000000,
      cash_bank_account_id: "bank-1",
      journal_number: null,
    }];
    const cancelOnly = await openPeriod();
    expect(cancelOnly.container.querySelector("[data-testid='hr-payroll-cancel-payment']")).not.toBeNull();
    expect(buttonByText(cancelOnly.container, "Setujui")).toBeFalsy();
    expect(buttonByText(cancelOnly.container, "Post Payment")).toBeFalsy();
  });
});

describe("piece payroll compact UI", () => {
  const piecePeriod = {
    id: "piece-ui",
    period_scope: "piece",
    period_number: "PR-PIECE-1",
    status: "DRAFT",
    start_date: "2026-10-01",
    end_date: "2026-10-06",
    piece_prepared_from: "gaji_operator",
    piece_bonus: { start_date: "2026-10-01", end_date: "2026-10-06" },
    calculation_version: 0,
    piece_sources: [],
    piece_blockers: [],
    piece_exclusions: [],
  };

  beforeEach(() => {
    mockAuth.perms = new Set([
      "payroll.view",
      "payroll.calculate",
      "payroll.review",
      "payroll.approve",
      "payroll.finalize",
      "payroll.piece_holding.edit",
      "payroll_accounting.post",
    ]);
    api.hrPayrollPeriods.mockResolvedValue({ periods: [piecePeriod] });
    api.hrPayrollPeriod.mockResolvedValue({ period: piecePeriod });
    api.hrPayrollLines.mockResolvedValue({ lines: [] });
    api.hrPayrollAttendance.mockResolvedValue({ attendance: [] });
    api.hrPayrollEmployees.mockResolvedValue({ employees: [] });
    api.hrPayrollKasbonSchedules.mockResolvedValue({ schedules: [] });
    api.hrPayrollPeriodJournal.mockResolvedValue({ journal_id: "", journal_number: "" });
    api.hrPayrollAccountingReadiness.mockResolvedValue({ readiness: { accounting_ready: false, summary: {}, planned_lines: [] } });
    api.hrPayrollPeriodPayment.mockResolvedValue({ payment: null, payments: [] });
    api.hrPayrollPieceIntegration.mockResolvedValue({ integration: { start_date: "2026-09-30", locked: false } });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  test("shows gaji badge locked dates stepper and hitung as the primary next action", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<HrPayroll mode="payroll" />);
    });
    await flush();
    const periodButton = buttonByText(container, "PR-PIECE-1");
    await act(async () => {
      periodButton.click();
    });
    await flush();

    expect(container.querySelector("[data-testid='hr-piece-from-gaji']").textContent).toContain("Dibuat dari Gaji Operator");
    expect(container.querySelector("[data-testid='hr-piece-kpi']")).not.toBeNull();
    expect(container.querySelector("[data-testid='hr-piece-period-settings']")).not.toBeNull();
    expect(container.querySelector("[data-testid='hr-piece-wage-start']").disabled).toBe(true);
    expect(container.querySelector("[data-testid='hr-piece-wage-end']").disabled).toBe(true);
    expect(container.querySelector("[data-testid='hr-piece-stepper']").textContent).toContain("Hitung");
    const calc = container.querySelector("[data-testid='hr-calc-btn']");
    expect(calc).not.toBeNull();
    expect(calc.className).toContain("primary");
    const review = container.querySelector("[data-testid='hr-transition-review']");
    expect(review).not.toBeNull();
    expect(review.className).toContain("ghost");
    expect(review.disabled).toBe(true);
    expect(container.textContent).toContain("01/10/2026");
    expect(container.textContent).toContain("06/10/2026");
    expect(container.querySelector("[data-testid='hr-piece-wage-label']").textContent).toContain("01/10/2026");
    expect(container.querySelector("[data-testid='hr-piece-save']")?.className || "ghost").toMatch(/ghost/);
    expect(container.querySelector("[data-testid='hr-payroll-post-accounting']")).toBeNull();
    expect(container.querySelector("[data-testid='hr-piece-recovery']")).toBeNull();
    expect(buttonByText(container, "Lihat Detail Operator")).toBeTruthy();
  });
});
