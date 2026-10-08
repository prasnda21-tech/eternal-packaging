import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { TextEncoder, TextDecoder } from "util";

global.TextEncoder = global.TextEncoder || TextEncoder;
global.TextDecoder = global.TextDecoder || TextDecoder;

jest.mock("react-router-dom", () => {
  const React = require("react");
  return {
    Link: ({ to, children, ...props }) => React.createElement("a", { ...props, href: to }, children),
  };
}, { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    cashBankAccounts: jest.fn(),
    cashBankOwnerReconView: jest.fn(),
    cashBankPairSuggestions: jest.fn(),
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockPermissions.includes(perm) }),
}));

jest.mock("../hooks/useDebouncedLiveSearch", () => ({
  useDebouncedLiveSearch: () => {
    const React = require("react");
    const [input, setInput] = React.useState("");
    return {
      input,
      setInput,
      q: input,
      flush: () => {},
      clear: () => setInput(""),
    };
  },
}));

const api = require("../lib/api").default;
const RekonsiliasiOwnerKasBank = require("./RekonsiliasiOwnerKasBank").default;

const originalActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
beforeAll(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
});
afterAll(() => {
  global.IS_REACT_ACT_ENVIRONMENT = originalActEnvironment;
});

let mockPermissions;
let container;
let root;

const flush = () => act(async () => { await Promise.resolve(); });

const sample = {
  read_only: true,
  auto_match: false,
  legacy_rk_note: "Dokumen rekonsiliasi saldo lama (RK) terpisah. Finalized RK bukan berarti saldo cocok.",
  statement_closing_complete: true,
  statement_closing_note: null,
  book_ending_balance: 1000000,
  statement_closing: { amount: 1050000, status: "dari_kolom_saldo_impor" },
  difference: 50000,
  summary: {
    saldo_buku: 1000000,
    saldo_statement: 1050000,
    selisih: 50000,
    cocok: 1,
    belum_cocok: 2,
    perlu_review: 1,
  },
  items: [
    {
      id: "pair:1",
      date: "2026-09-10",
      bank: "Bank Uji",
      statement_text: "Transfer masuk",
      book_text: "JE-1",
      nominal: 100000,
      source_label: "Penerimaan",
      source_type: "cash_bank_receipt",
      source_id: "rcpt-1",
      source_path: "/kas-bank/penerimaan",
      voucher_number: "KM-1",
      status: "SESUAI",
      status_label: "Sesuai",
      statement: {
        id: "stmt-1", date: "2026-09-10", amount: 100000, direction: "in",
        description: "Transfer masuk", reference: "R1", bank_txn_id: "T1", duplicate_status: "clear",
      },
      book: {
        journal_number: "JE-1", date: "2026-09-10", absolute_amount: 100000, direction: "in",
        source_type: "cash_bank_receipt", description: "Penerimaan", status: "posted",
      },
      match: { id: "pair-1", claim_active: true, review_state: "sumber_sah" },
    },
    {
      id: "stmt:2",
      date: "2026-09-11",
      bank: "Bank Uji",
      statement_text: "Biaya admin",
      book_text: null,
      nominal: 5000,
      source_label: "—",
      status: "STATEMENT_TANPA_JURNAL",
      status_label: "Statement Tanpa Jurnal",
      statement: {
        id: "stmt-2", date: "2026-09-11", amount: 5000, direction: "out",
        description: "Biaya admin", reference: null, bank_txn_id: "T2", duplicate_status: "pending_review",
      },
      book: null,
      match: null,
    },
    {
      id: "book:3",
      date: "2026-09-12",
      bank: "Bank Uji",
      statement_text: null,
      book_text: "JE-2",
      nominal: 20000,
      source_label: "Pembayaran Payroll",
      source_type: "payroll_payment",
      source_path: null,
      status: "PERLU_REVIEW",
      status_label: "Perlu Review",
      statement: null,
      book: {
        journal_number: "JE-2", date: "2026-09-12", absolute_amount: 20000, direction: "out",
        source_type: "payroll_payment", description: "Gaji", status: "posted",
      },
      match: null,
    },
  ],
};

async function renderPage() {
  await act(async () => root.render(React.createElement(RekonsiliasiOwnerKasBank)));
  await flush();
  await flush();
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPermissions = ["cash_bank.view"];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  api.cashBankAccounts.mockResolvedValue({ items: [{ id: "bank-1", name: "Bank Uji", currency_code: "IDR" }] });
  api.cashBankOwnerReconView.mockResolvedValue(sample);
  api.cashBankPairSuggestions.mockResolvedValue({
    ambiguous: true,
    applied: false,
    suggestions: [{ journal_line_id: "x", reasons: ["Nominal sama", "Arah sama"] }],
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

test("table renders mixed statuses and compact summary", async () => {
  await renderPage();
  expect(container.querySelector("[data-testid=kas-bank-owner-recon-page]")).toBeTruthy();
  expect(container.querySelector("[data-testid=kas-bank-owner-summary]").textContent).toContain("Saldo Buku");
  expect(container.querySelector("[data-testid=kas-bank-owner-summary]").textContent).toContain("Cocok");
  expect(container.querySelector("[data-testid=kas-bank-owner-table]")).toBeTruthy();
  const rows = container.querySelectorAll("[data-testid=kas-bank-owner-row]");
  expect(rows.length).toBe(3);
  expect(container.textContent).toContain("Sesuai");
  expect(container.textContent).toContain("Statement Tanpa Jurnal");
  expect(container.textContent).toContain("Perlu Review");
  expect(container.textContent).toContain("Pembayaran Payroll");
});

test("status and bank filters call owner-view API", async () => {
  await renderPage();
  const status = container.querySelector("[data-testid=kas-bank-owner-status]");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(status, "SESUAI");
    status.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  expect(api.cashBankOwnerReconView).toHaveBeenCalled();
  const last = api.cashBankOwnerReconView.mock.calls.at(-1)[0];
  expect(last.status).toBe("SESUAI");
  expect(last.cash_bank_id).toBe("bank-1");
});

test("drawer shows sections; suggestions load once; no write actions", async () => {
  await renderPage();
  const openBtn = container.querySelector("[data-testid=kas-bank-owner-open]");
  await act(async () => openBtn.click());
  await flush();
  await flush();
  const drawer = container.querySelector("[data-testid=kas-bank-owner-drawer]");
  expect(drawer).toBeTruthy();
  expect(drawer.textContent).toContain("Statement");
  expect(drawer.textContent).toContain("Book");
  expect(drawer.textContent).toContain("Source");
  expect(drawer.textContent).toContain("Match");
  expect(drawer.textContent).toContain("Balance Context");
  expect(drawer.textContent).toContain("Buka dokumen");
  expect(api.cashBankPairSuggestions).toHaveBeenCalledTimes(1);
  expect(container.textContent).not.toMatch(/Cocokkan|Finalisasi|Simpan Draft|Import/);
  expect(container.querySelector("[data-testid=kas-bank-owner-legacy-note]").textContent).toMatch(/Finalized RK bukan berarti saldo cocok/);
});

test("safe deep link only when source_path exists", async () => {
  await renderPage();
  const buttons = [...container.querySelectorAll("[data-testid=kas-bank-owner-open]")];
  await act(async () => buttons[0].click());
  await flush();
  expect(container.querySelector("[data-testid=kas-bank-owner-source-link]")).toBeTruthy();
  await act(async () => container.querySelector("[data-testid=kas-bank-owner-drawer-close]").click());
  await flush();
  await act(async () => buttons[2].click());
  await flush();
  expect(container.querySelector("[data-testid=kas-bank-owner-source-link]")).toBeNull();
});

test("view-only hides matching shortcut; reconcile shows it", async () => {
  mockPermissions = ["cash_bank.view"];
  await renderPage();
  expect(container.querySelector("[data-testid=kas-bank-owner-recon-page]")).toBeTruthy();
  expect(container.textContent).not.toContain("Izin cash_bank.view diperlukan");
  expect(container.querySelector("[data-testid=kas-bank-owner-link-pair]")).toBeNull();
  expect(container.textContent).not.toContain("Buka pencocokan baris");

  mockPermissions = ["cash_bank.view", "cash_bank.reconcile"];
  await renderPage();
  expect(container.querySelector("[data-testid=kas-bank-owner-link-pair]")).toBeTruthy();
  expect(container.textContent).toContain("Buka pencocokan baris");
});

test("search placeholder is compact Cari", async () => {
  await renderPage();
  const search = container.querySelector("[data-testid=kas-bank-owner-search]");
  expect(search).toBeTruthy();
  expect(search.getAttribute("placeholder")).toBe("Cari");
  expect(search.getAttribute("aria-label")).toBe("Cari");
});

test("compact layout classes present for 1366 viewport", async () => {
  await renderPage();
  const page = container.querySelector("[data-testid=kas-bank-owner-recon-page]");
  expect(page.className).toMatch(/kb-owner-recon/);
  expect(container.querySelector(".kb-owner-summary")).toBeTruthy();
  expect(container.querySelector(".kb-owner-table-wrap")).toBeTruthy();
  expect(container.querySelector(".kb-owner-toolbar")).toBeTruthy();
});
