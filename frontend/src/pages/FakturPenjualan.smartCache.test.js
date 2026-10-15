import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import {
  __resetSalesInvoiceListCacheForTests,
  buildSalesInvoiceListCacheKey,
  clearSalesInvoiceListCache,
  getSalesInvoiceListCache,
  setSalesInvoiceListCache,
} from "../lib/salesInvoiceListCache";
import { LIVE_SEARCH_DEBOUNCE_MS } from "../hooks/useDebouncedLiveSearch";
import FakturPenjualan from "./FakturPenjualan";

const mockAuth = {
  user: { id: "u1", name: "Admin", role: "admin" },
  allow: null,
};

let mockSearch = "";
let mockSetSearchParams;

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: "/penjualan/faktur-penjualan", search: mockSearch, state: null }),
  useSearchParams: () => [new URLSearchParams(mockSearch.replace(/^\?/, "")), mockSetSearchParams],
  useParams: () => ({}),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: mockAuth.user,
    hasPerm: (perm) => (mockAuth.allow == null ? true : mockAuth.allow.has(perm)),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listSalesInvoices: jest.fn(),
    bulkDeleteSalesInvoices: jest.fn(),
    deleteSalesInvoice: jest.fn(),
    businessCancelSalesInvoice: jest.fn(),
    downloadSalesInvoicePdf: jest.fn(),
    salesInvoiceLifecycleCapability: jest.fn(),
    salesInvoiceChargeAccounts: jest.fn(),
    getSalesInvoice: jest.fn(),
  },
}));

jest.mock("../components/ui/popover", () => ({
  Popover: ({ children }) => <div>{children}</div>,
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children }) => <div>{children}</div>,
}));

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function row(overrides) {
  return {
    id: "inv-1",
    invoice_number: "INV-1",
    customer: "PT Aktif",
    po_id: "po-1",
    po_number: "PO-1",
    invoice_date: "2026-03-01",
    grand_total: 1000,
    outstanding_amount: 1000,
    financial_status: "finalized",
    accounting_status: "posted",
    accounting_posted: true,
    accounting_journal_id: "j1",
    payment_status: "BELUM DIBAYAR",
    can_delete: false,
    list_tab: "active",
    ...overrides,
  };
}

function listResponse(rows, listTab = "active") {
  return {
    invoices: rows,
    total: rows.length,
    tab_total: rows.length,
    all_total: rows.length,
    five_tab_active_total: listTab === "active" ? rows.length : 0,
    completed_total: listTab === "completed" ? rows.length : 0,
    pending_total: 0,
    cancelled_total: 0,
    list_tab: listTab,
    page: 1,
    page_size: 50,
  };
}

describe("Faktur Penjualan smart tab cache", () => {
  let container;
  let root;

  beforeEach(() => {
    jest.useFakeTimers();
    __resetSalesInvoiceListCacheForTests();
    mockSearch = "";
    mockSetSearchParams = jest.fn((next) => {
      if (typeof next === "string") mockSearch = next.startsWith("?") ? next : `?${next}`;
      else if (next && typeof next.toString === "function") {
        const value = next.toString();
        mockSearch = value ? `?${value}` : "";
      } else {
        mockSearch = "";
      }
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockAuth.user = { id: "u1", name: "Admin", role: "admin" };
    mockAuth.allow = null;
    api.listSalesInvoices.mockReset();
    api.listSalesInvoices.mockImplementation(async (params = {}) => {
      const tab = params.list_tab || "active";
      if (tab === "completed") {
        return listResponse([row({
          id: "inv-done",
          invoice_number: "INV-DONE",
          outstanding_amount: 0,
          payment_status: "LUNAS",
          list_tab: "completed",
        })], "completed");
      }
      return listResponse([row({})], "active");
    });
    api.salesInvoiceLifecycleCapability.mockResolvedValue({});
    api.salesInvoiceChargeAccounts.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.useRealTimers();
  });

  async function renderPage() {
    await act(async () => {
      root.render(<FakturPenjualan />);
    });
    await act(async () => {
      await Promise.resolve();
    });
  }

  test("first load fetches backend and keeps rows for warm Aktif tab", async () => {
    await renderPage();
    expect(api.listSalesInvoices).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="invoice-row-inv-1"]')).toBeTruthy();

    await act(async () => {
      container.querySelector('[data-testid="tab-faktur-selesai"]')?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="invoice-row-inv-done"]')).toBeTruthy();
    expect(container.textContent).not.toContain("INV-1");

    const callsBeforeReturn = api.listSalesInvoices.mock.calls.length;
    await act(async () => {
      container.querySelector('[data-testid="tab-faktur-aktif"]')?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    // Cached Aktif rows render immediately (no blank-only loading state).
    expect(container.querySelector('[data-testid="invoice-row-inv-1"]')).toBeTruthy();
    expect(container.textContent).not.toMatch(/Memuat daftar Faktur Penjualan/);
    // Fresh TTL → no extra network for warm cache.
    expect(api.listSalesInvoices.mock.calls.length).toBe(callsBeforeReturn);
  });

  test("uncached tab does not keep previous tab rows while loading", async () => {
    let resolveCompleted;
    api.listSalesInvoices.mockImplementation(async (params = {}) => {
      const tab = params.list_tab || "active";
      if (tab === "completed") {
        await new Promise((resolve) => {
          resolveCompleted = resolve;
        });
        return listResponse([row({
          id: "inv-done",
          invoice_number: "INV-DONE",
          outstanding_amount: 0,
          list_tab: "completed",
        })], "completed");
      }
      return listResponse([row({})], "active");
    });

    await renderPage();
    expect(container.querySelector('[data-testid="invoice-row-inv-1"]')).toBeTruthy();

    await act(async () => {
      container.querySelector('[data-testid="tab-faktur-selesai"]')?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('[data-testid="invoice-row-inv-1"]')).toBeFalsy();
    expect(container.textContent).toMatch(/Memuat daftar Faktur Penjualan/);

    await act(async () => {
      resolveCompleted();
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="invoice-row-inv-done"]')).toBeTruthy();
  });

  test("search debounce does not request every keystroke; continuous typing keeps focus and value", async () => {
    await renderPage();
    api.listSalesInvoices.mockClear();
    const input = container.querySelector('[data-testid="search-invoice-input"]');
    expect(input).toBeTruthy();
    const setNativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;

    await act(async () => {
      input.focus();
      let value = "";
      for (const ch of "ABCDE") {
        value += ch;
        setNativeValue.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });

    expect(document.activeElement).toBe(input);
    expect(input.value).toBe("ABCDE");
    expect(api.listSalesInvoices).not.toHaveBeenCalled();

    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS - 20);
    });
    expect(api.listSalesInvoices).not.toHaveBeenCalled();

    await act(async () => {
      let value = input.value;
      for (const ch of "123") {
        value += ch;
        setNativeValue.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });

    expect(document.activeElement).toBe(input);
    expect(input.value).toBe("ABCDE123");

    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS + 20);
      await Promise.resolve();
    });

    expect(api.listSalesInvoices).toHaveBeenCalledTimes(1);
    expect(api.listSalesInvoices.mock.calls[0][0]).toEqual(expect.objectContaining({ q: "ABCDE123" }));
    expect(input.value).toBe("ABCDE123");
    expect(container.querySelector('[data-testid="search-invoice-input"]')).toBe(input);
  });

  test("failed background refresh keeps cached rows and shows sync error", async () => {
    const key = buildSalesInvoiceListCacheKey({
      userId: "u1",
      listTab: "active",
      q: "",
      columnFilters: { datePreset: "all", financialStatus: "", accountingStatus: "", paymentStatus: "" },
      page: 1,
      pageSize: 50,
    });
    setSalesInvoiceListCache(key, {
      invoices: [row({ id: "cached-1", invoice_number: "INV-CACHE" })],
      listMeta: {
        total: 1,
        tabTotal: 1,
        capped: false,
        counts: { all: 1, active: 1, completed: 0, pending: 0, cancelled: 0 },
      },
    }, Date.now() - 60_000);

    api.listSalesInvoices.mockRejectedValueOnce({
      response: { data: { detail: "sinkron gagal" } },
    });

    await renderPage();
    expect(container.querySelector('[data-testid="invoice-row-cached-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="invoice-list-sync-error"]')?.textContent).toMatch(/sinkron gagal/i);
    expect(container.textContent).toMatch(/belum disinkronkan/i);
  });

  test("logout cache clear hides prior financial rows for same user id", () => {
    const key = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active" });
    setSalesInvoiceListCache(key, { invoices: [row({})], listMeta: {} });
    expect(getSalesInvoiceListCache(key)).toBeTruthy();
    clearSalesInvoiceListCache();
    expect(getSalesInvoiceListCache(key)).toBeNull();
  });

  test("auth logout clears sales invoice list cache", () => {
    const auth = require("fs").readFileSync(require("path").join(__dirname, "../lib/auth.jsx"), "utf8");
    expect(auth).toMatch(/clearSalesInvoiceListCache\(\)/);
  });
});
