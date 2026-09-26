import { act } from "react";
import { createRoot } from "react-dom/client";
import { LIVE_SEARCH_DEBOUNCE_MS } from "../hooks/useDebouncedLiveSearch";
import api from "../lib/api";
import PesananPenjualan from "./PesananPenjualan";

const mockSetSearchParams = jest.fn();

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: "/penjualan/pesanan-penjualan", search: "", state: null }),
  useSearchParams: () => [new URLSearchParams(), mockSetSearchParams],
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
    mockSetSearchParams.mockReset();
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
});
