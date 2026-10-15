import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import {
  __resetSalesOrderListCacheForTests,
  buildSalesOrderListCacheKey,
  clearSalesOrderListCache,
  getSalesOrderListCache,
  setSalesOrderListCache,
} from "../lib/salesOrderListCache";
import { LIVE_SEARCH_DEBOUNCE_MS } from "../hooks/useDebouncedLiveSearch";
import PesananPenjualan from "./PesananPenjualan";

const mockAuth = {
  user: { id: "u1", name: "Admin", role: "admin", branch_id: "b1" },
  allow: null,
};

const mockRouter = {
  params: new URLSearchParams(),
  search: "",
};
let mockSetSearchParams;

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: "/penjualan/pesanan-penjualan", search: mockRouter.search, state: null }),
  useSearchParams: () => [mockRouter.params, mockSetSearchParams],
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
    listSalesOrders: jest.fn(),
    salesOrderOptions: jest.fn().mockResolvedValue({}),
    bulkDelete: jest.fn(),
    createSalesOrder: jest.fn(),
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

function orderRow(overrides = {}) {
  return {
    id: "po-1",
    po_number: "PO-1",
    customer: "PT Aktif",
    order_date: "2026-03-01",
    estimated_delivery: "2026-03-04",
    list_tab: "active",
    ...overrides,
  };
}

function listResponse(rows, listTab = "active") {
  return {
    orders: rows,
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

describe("Pesanan Penjualan smart tab cache", () => {
  let container;
  let root;

  beforeEach(() => {
    jest.useFakeTimers();
    __resetSalesOrderListCacheForTests();
    mockRouter.params = new URLSearchParams();
    mockRouter.search = "";
    mockSetSearchParams = jest.fn((next) => {
      const params = next instanceof URLSearchParams ? new URLSearchParams(next) : new URLSearchParams(next);
      mockRouter.params = params;
      const qs = params.toString();
      mockRouter.search = qs ? `?${qs}` : "";
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockAuth.user = { id: "u1", name: "Admin", role: "admin", branch_id: "b1" };
    mockAuth.allow = null;
    api.listSalesOrders.mockReset();
    api.listSalesOrders.mockImplementation(async (params = {}) => {
      const tab = params.list_tab || "active";
      if (tab === "completed") {
        return listResponse([orderRow({
          id: "po-done",
          po_number: "PO-DONE",
          list_tab: "completed",
        })], "completed");
      }
      return listResponse([orderRow({})], "active");
    });
    api.salesOrderOptions.mockResolvedValue({});
    api.bulkDelete.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.useRealTimers();
  });

  async function renderPage() {
    await act(async () => {
      root.render(<PesananPenjualan />);
    });
    await act(async () => {
      await Promise.resolve();
    });
  }

  test("cached Aktif rows paint without blank spinner; background still refetches", async () => {
    await renderPage();
    expect(api.listSalesOrders).toHaveBeenCalledTimes(1);
    expect(container.textContent).toMatch(/PO-1/);

    await act(async () => {
      container.querySelector('[data-testid="sales-order-tab-selesai"]')?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.textContent).toMatch(/PO-DONE/);
    expect(container.textContent).not.toContain("PO-1");

    const callsBeforeReturn = api.listSalesOrders.mock.calls.length;
    await act(async () => {
      container.querySelector('[data-testid="sales-order-tab-aktif"]')?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    // Cached Aktif rows render immediately (no blank-only loading state).
    expect(container.textContent).toMatch(/PO-1/);
    expect(container.textContent).not.toMatch(/Memuat daftar Pesanan Penjualan/);
    // Policy: always background-revalidate even when TTL fresh.
    expect(api.listSalesOrders.mock.calls.length).toBeGreaterThan(callsBeforeReturn);
  });

  test("uncached tab does not keep previous tab rows while loading", async () => {
    let resolveCompleted;
    api.listSalesOrders.mockImplementation(async (params = {}) => {
      const tab = params.list_tab || "active";
      if (tab === "completed") {
        await new Promise((resolve) => {
          resolveCompleted = resolve;
        });
        return listResponse([orderRow({
          id: "po-done",
          po_number: "PO-DONE",
          list_tab: "completed",
        })], "completed");
      }
      return listResponse([orderRow({})], "active");
    });

    await renderPage();
    expect(container.textContent).toMatch(/PO-1/);

    await act(async () => {
      container.querySelector('[data-testid="sales-order-tab-selesai"]')?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).not.toContain("PO-1");
    expect(container.textContent).toMatch(/Memuat daftar Pesanan Penjualan/);

    await act(async () => {
      resolveCompleted();
      await Promise.resolve();
    });
    expect(container.textContent).toMatch(/PO-DONE/);
  });

  test("out-of-order responses do not overwrite newer query", async () => {
    let resolveSlow;
    api.listSalesOrders.mockImplementation(async (params = {}) => {
      const q = String(params.q || "");
      if (q === "SLOW") {
        await new Promise((resolve) => {
          resolveSlow = resolve;
        });
        return listResponse([orderRow({ id: "po-slow", po_number: "PO-SLOW" })]);
      }
      if (q === "FAST") {
        return listResponse([orderRow({ id: "po-fast", po_number: "PO-FAST" })]);
      }
      return listResponse([orderRow({})]);
    });

    await renderPage();
    api.listSalesOrders.mockClear();

    const input = container.querySelector('[data-testid="search-sales-orders-input"]');
    expect(input).toBeTruthy();
    const setNativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;

    await act(async () => {
      setNativeValue.call(input, "SLOW");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS + 20);
      await Promise.resolve();
    });

    await act(async () => {
      setNativeValue.call(input, "FAST");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS + 20);
      await Promise.resolve();
    });

    expect(container.textContent).toMatch(/PO-FAST/);

    await act(async () => {
      resolveSlow();
      await Promise.resolve();
    });
    expect(container.textContent).toMatch(/PO-FAST/);
    expect(container.textContent).not.toContain("PO-SLOW");
  });

  test("failed background refresh keeps cached rows and shows sync error", async () => {
    const key = buildSalesOrderListCacheKey({
      userId: "u1",
      branchId: "b1",
      canViewInvoice: true,
      listTab: "active",
      period: "all",
      q: "",
      columnFilters: {},
      page: 1,
      pageSize: 50,
    });
    setSalesOrderListCache(key, {
      orders: [orderRow({ id: "cached-1", po_number: "PO-CACHE" })],
      listMeta: {
        total: 1,
        tabTotal: 1,
        capped: false,
        counts: { all: 1, active: 1, completed: 0, pending: 0, cancelled: 0 },
      },
    }, Date.now() - 60_000);

    api.listSalesOrders.mockRejectedValueOnce({
      response: { data: { detail: "sinkron gagal" } },
    });

    await renderPage();
    expect(container.textContent).toMatch(/PO-CACHE/);
    expect(container.querySelector('[data-testid="sales-order-list-sync-error"]')?.textContent)
      .toMatch(/sinkron gagal/i);
    expect(container.textContent).toMatch(/belum disinkronkan/i);
  });

  test("mutation invalidate forces authoritative reload", async () => {
    await renderPage();
    const key = buildSalesOrderListCacheKey({
      userId: "u1",
      branchId: "b1",
      canViewInvoice: true,
      listTab: "active",
      period: "all",
      page: 1,
      pageSize: 50,
    });
    expect(getSalesOrderListCache(key)).toBeTruthy();

    api.bulkDelete.mockResolvedValueOnce({});
    api.listSalesOrders.mockImplementation(async () => listResponse([]));

    await act(async () => {
      container.querySelector('[data-testid="order-checkbox-po-1"]')?.click();
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-hapus-terpilih"]')?.click();
    });
    expect(container.querySelector('[data-testid="btn-confirm-delete"]')).toBeTruthy();

    api.listSalesOrders.mockClear();
    await act(async () => {
      container.querySelector('[data-testid="btn-confirm-delete"]')?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.bulkDelete).toHaveBeenCalled();
    expect(api.listSalesOrders).toHaveBeenCalled();
    expect(getSalesOrderListCache(key)?.orders || []).toEqual([]);
  });

  test("draft search input is not reset during background refresh", async () => {
    let resolveRefresh;
    api.listSalesOrders.mockImplementation(async () => {
      await new Promise((resolve) => {
        resolveRefresh = resolve;
      });
      return listResponse([orderRow({})]);
    });

    await renderPage();
    // resolve first load
    await act(async () => {
      resolveRefresh();
      await Promise.resolve();
    });

    api.listSalesOrders.mockImplementation(async () => {
      await new Promise((resolve) => {
        resolveRefresh = resolve;
      });
      return listResponse([orderRow({ po_number: "PO-2" })]);
    });

    const input = container.querySelector('[data-testid="search-sales-orders-input"]');
    expect(input).toBeTruthy();
    const setNativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;

    await act(async () => {
      container.querySelector('[data-testid="sales-order-refresh-button"]')?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      setNativeValue.call(input, "DRAFT-KEEP");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(input.value).toBe("DRAFT-KEEP");

    await act(async () => {
      resolveRefresh();
      await Promise.resolve();
    });
    expect(input.value).toBe("DRAFT-KEEP");
  });

  test("logout cache clear hides prior rows for same user id", () => {
    const key = buildSalesOrderListCacheKey({ userId: "u1", branchId: "b1", listTab: "active" });
    setSalesOrderListCache(key, { orders: [orderRow({})], listMeta: {} });
    expect(getSalesOrderListCache(key)).toBeTruthy();
    clearSalesOrderListCache();
    expect(getSalesOrderListCache(key)).toBeNull();
  });

  test("auth logout clears sales order list cache", () => {
    const auth = require("fs").readFileSync(require("path").join(__dirname, "../lib/auth.jsx"), "utf8");
    expect(auth).toMatch(/clearSalesOrderListCache\(\)/);
  });
});
