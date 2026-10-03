import { act } from "react";
import { createRoot } from "react-dom/client";
import { LIVE_SEARCH_DEBOUNCE_MS } from "../hooks/useDebouncedLiveSearch";
import api from "../lib/api";
import { periodBounds } from "../lib/salesOrderListPresentation";
import { readPoReturnContext } from "../lib/poListReturn";
import PesananPenjualan from "./PesananPenjualan";

const mockRouter = {
  params: new URLSearchParams(),
  search: "",
};
const mockSetSearchParams = jest.fn((next) => {
  const params = next instanceof URLSearchParams ? new URLSearchParams(next) : new URLSearchParams(next);
  mockRouter.params = params;
  const qs = params.toString();
  mockRouter.search = qs ? `?${qs}` : "";
});

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, onClick, ...props }) => (
    <a
      href={to}
      {...props}
      onClick={(event) => {
        event.preventDefault();
        onClick?.(event);
      }}
    >
      {children}
    </a>
  ),
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: "/penjualan/pesanan-penjualan", search: mockRouter.search, state: null }),
  useSearchParams: () => [mockRouter.params, mockSetSearchParams],
  useParams: () => ({}),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", name: "Sales", role: "admin" },
    hasPerm: () => true,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listSalesOrders: jest.fn().mockResolvedValue({ orders: [] }),
    salesOrderOptions: jest.fn().mockResolvedValue({}),
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

describe("Pesanan Penjualan list search debounce", () => {
  let container;
  let root;

  beforeEach(() => {
    jest.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.listSalesOrders.mockReset();
    api.listSalesOrders.mockResolvedValue({ orders: [] });
    mockSetSearchParams.mockClear();
    mockRouter.params = new URLSearchParams();
    mockRouter.search = "";
    sessionStorage.clear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.useRealTimers();
  });

  const renderPage = async () => {
    await act(async () => {
      root.render(<PesananPenjualan />);
    });
    await act(async () => {
      await Promise.resolve();
    });
  };

  const typeSearch = async (value) => {
    const input = container.querySelector('[data-testid="search-sales-orders-input"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };

  test("typing A, AB, ABC inside the debounce window issues one settled search for ABC", async () => {
    await renderPage();
    expect(api.listSalesOrders).toHaveBeenCalledTimes(1);
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      { status: "aktif", page: 1, page_size: 10 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    await typeSearch("A");
    await typeSearch("AB");
    await typeSearch("ABC");
    expect(api.listSalesOrders).toHaveBeenCalledTimes(1);

    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS);
      await Promise.resolve();
    });
    expect(api.listSalesOrders).toHaveBeenCalledTimes(2);
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      { q: "ABC", status: "aktif", page: 1, page_size: 10 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  test("clearing search restores the unfiltered list request after settle", async () => {
    await renderPage();
    await typeSearch("ABC");
    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS);
      await Promise.resolve();
    });
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      { q: "ABC", status: "aktif", page: 1, page_size: 10 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    await act(async () => {
      container.querySelector('[data-testid="search-sales-orders-clear"]').click();
    });
    expect(container.querySelector('[data-testid="search-sales-orders-input"]').value).toBe("");
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesOrders.mock.calls.some((call) => {
      const params = call[0] || {};
      return !params.q;
    })).toBe(true);
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      { status: "aktif", page: 1, page_size: 10 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  const setQuery = (search) => {
    const raw = String(search || "").replace(/^\?/, "");
    mockRouter.params = new URLSearchParams(raw);
    mockRouter.search = raw ? `?${raw}` : "";
  };

  const listedOrder = (id, customer) => ({
    id,
    po_number: `PO-${id}`,
    customer,
    outlet: "Gudang",
    sales: "Citra",
    order_date: "2026-09-15",
    total_quantity: 10,
    quantity_by_unit: [{ unit: "pcs", quantity: 10 }],
    current_stage: 6,
    status_label: "Penjadwalan",
    cancelled: false,
    hold: false,
    delivery_summary: {
      status: "BELUM DIKIRIM",
      total_ordered: 10,
      total_shipped: 0,
      total_remaining: 10,
    },
  });

  const chooseStatus = async (value) => {
    const select = container.querySelector('[data-testid="sales-order-status-filter"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
      setter.call(select, value);
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
  };

  const lastParams = () => {
    const next = mockSetSearchParams.mock.calls.at(-1)?.[0];
    return next instanceof URLSearchParams ? next : new URLSearchParams(next || "");
  };

  test("Semua requests gabungan, keeps search and period, and writes tab=semua", async () => {
    api.listSalesOrders.mockResolvedValue({
      orders: [listedOrder("a", "Maju"), listedOrder("b", "Sumber")],
      total: 4,
      capped: false,
    });
    await renderPage();
    expect(container.querySelector('[data-testid="sales-order-aktif-count"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sales-order-tab-semua"]')).toBeTruthy();

    const period = container.querySelector('[data-testid="sales-order-period-filter"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
      setter.call(period, "last_month");
      period.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await typeSearch("kopi");
    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS);
      await Promise.resolve();
    });

    await act(async () => {
      container.querySelector('[data-testid="sales-order-tab-semua"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    const bounds = periodBounds("last_month");
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      {
        q: "kopi",
        status: "gabungan",
        date_from: bounds.date_from,
        date_to: bounds.date_to,
        page: 1,
        page_size: 10,
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(lastParams().get("tab")).toBe("semua");
    expect(lastParams().get("status")).toBeNull();
    expect(lastParams().get("q")).toBe("kopi");
    expect(lastParams().get("period")).toBe("last_month");
    expect(container.querySelector('[data-testid="sales-order-tab-semua"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="sales-order-aktif-count"]')).toBeFalsy();

    const customer = container.querySelector('[data-testid="so-filter-customer"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(customer, "maju");
      customer.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="order-row-a"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="order-row-b"]')).toBeFalsy();

    const pageSize = container.querySelector('[data-testid="sales-order-page-size"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
      setter.call(pageSize, "20");
      pageSize.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "gabungan", page: 1, page_size: 20, q: "kopi" }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  test("remount of tab=semua requests gabungan and opening detail stores that query", async () => {
    setQuery("tab=semua&q=kopi&period=last_month&page=2&page_size=20");
    api.listSalesOrders.mockResolvedValue({
      orders: [listedOrder("back", "Kopi")],
      total: 25,
      capped: false,
    });
    await renderPage();
    const bounds = periodBounds("last_month");
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      {
        q: "kopi",
        status: "gabungan",
        date_from: bounds.date_from,
        date_to: bounds.date_to,
        page: 2,
        page_size: 20,
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('[data-testid="sales-order-tab-semua"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="sales-order-aktif-count"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="po-link-PO-back"]').click();
    });
    const href = readPoReturnContext("u1", "back")?.href || "";
    const stored = new URL(href, "http://local");
    expect(stored.pathname).toBe("/penjualan/pesanan-penjualan");
    expect(stored.searchParams.get("tab")).toBe("semua");
    expect(stored.searchParams.get("q")).toBe("kopi");
    expect(stored.searchParams.get("period")).toBe("last_month");
    expect(stored.searchParams.get("page")).toBe("2");
    expect(stored.searchParams.get("page_size")).toBe("20");
    expect(stored.searchParams.get("status")).toBeNull();
  });

  test("reset on Semua stays on gabungan", async () => {
    await renderPage();
    await act(async () => {
      container.querySelector('[data-testid="sales-order-tab-semua"]').click();
    });
    await typeSearch("kopi");
    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS);
      await Promise.resolve();
    });
    await act(async () => {
      container.querySelector('[data-testid="sales-order-reset-filters"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      { status: "gabungan", page: 1, page_size: 10 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('[data-testid="sales-order-tab-semua"]').getAttribute("aria-selected")).toBe("true");
    expect(lastParams().get("tab")).toBe("semua");
    expect(lastParams().get("status")).toBeNull();
  });

  test("status dropdown and deep links keep the existing tab switch", async () => {
    await renderPage();
    await act(async () => {
      container.querySelector('[data-testid="sales-order-tab-semua"]').click();
    });
    await chooseStatus("selesai");
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "selesai", page: 1, page_size: 10 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('[data-testid="sales-order-tab-histori"]').getAttribute("aria-selected")).toBe("true");
    expect(lastParams().get("tab")).toBe("histori");
    expect(lastParams().get("status")).toBe("selesai");

    await chooseStatus("hold");
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "hold" }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('[data-testid="sales-order-tab-aktif"]').getAttribute("aria-selected")).toBe("true");
    expect(lastParams().get("tab")).toBeNull();
    expect(lastParams().get("status")).toBe("hold");

    await act(async () => {
      container.querySelector('[data-testid="sales-order-tab-semua"]').click();
    });
    const stage = container.querySelector('[data-testid="so-filter-stage-text"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(stage, "HOLD");
      stage.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="so-th-filter-stage"] .so-th-clear').click();
    });
    await chooseStatus("__tab__");
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      { status: "gabungan", page: 1, page_size: 10 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('[data-testid="sales-order-tab-semua"]').getAttribute("aria-selected")).toBe("true");

    act(() => root.unmount());
    setQuery("tab=semua&status=selesai");
    root = createRoot(container);
    await renderPage();
    expect(api.listSalesOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "selesai" }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('[data-testid="sales-order-tab-histori"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="sales-order-tab-semua"]').getAttribute("aria-selected")).toBe("false");
  });
});
