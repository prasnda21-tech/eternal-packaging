import { act } from "react";
import { createRoot } from "react-dom/client";
import { LIVE_SEARCH_DEBOUNCE_MS } from "../hooks/useDebouncedLiveSearch";
import { salesDeliveryListCache } from "../lib/smartListCaches";
import api from "../lib/api";
import PengirimanPesanan, { DELIVERY_DEFAULT_PAGE_SIZE } from "./PengirimanPesanan";

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
  useLocation: () => ({ pathname: "/penjualan/pengiriman-pesanan", search: mockRouter.search, state: null }),
  useSearchParams: () => [mockRouter.params, mockSetSearchParams],
  useParams: () => ({}),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", name: "Sales", role: "admin", branch_id: "b1" },
    hasPerm: () => true,
  }),
}));

jest.mock("../components/HeaderSelectFilter", () => () => null);

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function deliveryRow(id, overrides = {}) {
  return {
    id,
    sj_number: `SJ-${id}`,
    po_id: `po-${id}`,
    po_number: `PO-${id}`,
    customer: `Cust ${id}`,
    outlet: "Utama",
    delivery_date: "2026-03-01",
    total_qty: 10,
    status: "SEBAGIAN",
    created_by_name: "Sales",
    is_cancelled: false,
    ...overrides,
  };
}

describe("Pengiriman Pesanan list UX unification", () => {
  let container;
  let root;

  beforeEach(() => {
    jest.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    salesDeliveryListCache.clear();
    mockSetSearchParams.mockClear();
    mockRouter.params = new URLSearchParams();
    mockRouter.search = "";
    jest.spyOn(api, "listSalesDeliveries").mockResolvedValue({ deliveries: [deliveryRow("1")] });
    jest.spyOn(api, "salesDeliveryFilterOptions").mockResolvedValue({ created_by_options: [] });
    jest.spyOn(api, "getSalesDelivery").mockResolvedValue({
      delivery: deliveryRow("1"),
      po_summary: {
        total_ordered: 20,
        total_shipped: 10,
        total_remaining: 10,
        delivery_status: "SEBAGIAN",
      },
      lines: [],
      related_shipments: [],
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.useRealTimers();
    jest.restoreAllMocks();
    salesDeliveryListCache.clear();
  });

  const renderPage = async (query = "") => {
    mockRouter.params = new URLSearchParams(query);
    mockRouter.search = query ? `?${query}` : "";
    await act(async () => {
      root.render(<PengirimanPesanan />);
    });
    await act(async () => {
      await Promise.resolve();
    });
  };

  const typeSearch = async (value) => {
    const input = container.querySelector('[data-testid="search-deliveries-input"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };

  test("header refresh calls authoritative reload without inventing force semantics", async () => {
    await renderPage();
    expect(api.listSalesDeliveries).toHaveBeenCalledTimes(1);
    const refresh = container.querySelector('[data-testid="refresh-delivery-btn"]');
    expect(refresh).toBeTruthy();
    expect(refresh.className).toMatch(/sd-list-refresh/);
    await act(async () => {
      refresh.click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesDeliveries.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector('[data-testid="search-deliveries-input"]').value).toBe("");
    expect(container.querySelector('[data-testid="filter-status-all"]').getAttribute("aria-selected")).toBe("true");
  });

  test("status strip keeps Semua/Sebagian/Selesai and lives in sd-list-chrome", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="delivery-list-tabs"]')).toBeTruthy();
    expect(container.querySelector(".sd-list-chrome")).toBeTruthy();
    expect(container.querySelector('[data-testid="filter-status-all"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="filter-status-SEBAGIAN"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="filter-status-SELESAI"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="filter-status-active"]')).toBeNull();
    await act(async () => {
      container.querySelector('[data-testid="filter-status-SEBAGIAN"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesDeliveries).toHaveBeenCalledWith(
      expect.objectContaining({ status: "SEBAGIAN" }),
    );
  });

  test("search input updates immediately; API and URL settle after debounce", async () => {
    await renderPage();
    api.listSalesDeliveries.mockClear();
    mockSetSearchParams.mockClear();

    await typeSearch("A");
    await typeSearch("AB");
    await typeSearch("ABC");
    expect(container.querySelector('[data-testid="search-deliveries-input"]').value).toBe("ABC");
    expect(api.listSalesDeliveries).not.toHaveBeenCalled();

    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(api.listSalesDeliveries).toHaveBeenCalledTimes(1);
    expect(api.listSalesDeliveries).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "ABC" }),
    );
    expect(mockSetSearchParams).toHaveBeenCalled();
    const lastUrl = mockSetSearchParams.mock.calls.at(-1)[0];
    const params = lastUrl instanceof URLSearchParams ? lastUrl : new URLSearchParams(lastUrl);
    expect(params.get("q")).toBe("ABC");
  });

  test("URL restore of q and status seeds list filters", async () => {
    await renderPage("q=SJ-9&status=SELESAI");
    expect(container.querySelector('[data-testid="search-deliveries-input"]').value).toBe("SJ-9");
    expect(container.querySelector('[data-testid="filter-status-SELESAI"]').getAttribute("aria-selected")).toBe("true");
    expect(api.listSalesDeliveries).toHaveBeenCalledWith(
      expect.objectContaining({ q: "SJ-9", status: "SELESAI" }),
    );
  });

  test("deep-link create is not overwritten by list URL sync", async () => {
    jest.spyOn(api, "listSalesOrders").mockResolvedValue({ orders: [] });
    jest.spyOn(api, "getPoDeliverySummary").mockResolvedValue({
      po_id: "po-1",
      po_number: "PO-1",
      customer: "PT A",
      customer_phone: "081111111111",
      recipient: "Budi",
      outlet: "Utama",
      address: "Jl. 1",
      delivery_zone: "Barat",
      sales: "Vira",
      expedition: "Internal fleet",
      lines: [{
        line_idx: 0,
        line_id: "po-1-l0",
        name: "Cup",
        unit: "pcs",
        remaining_qty: 5,
        ordered_qty: 5,
        shipped_qty: 0,
      }],
      can_create_delivery: true,
      total_remaining: 5,
      suggested_sj_number: "SJ/po-1",
      next_shipment_index: 1,
    });
    await renderPage("action=create&po_id=po-1");
    expect(container.querySelector('[data-testid="btn-submit-delivery-order"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="search-deliveries-input"]')).toBeNull();
    const wroteListOnly = mockSetSearchParams.mock.calls.some((call) => {
      const params = call[0] instanceof URLSearchParams ? call[0] : new URLSearchParams(call[0]);
      return params.get("q") != null && !params.get("action") && !params.get("po_id");
    });
    expect(wroteListOnly).toBe(false);
  });

  test("cache hit shows rows while background refresh runs; sync error keeps cache warning", async () => {
    const key = salesDeliveryListCache.buildKey({
      u: "u1",
      b: "b1",
      q: "",
      status: "all",
      period: "all",
      createdBy: "",
      page: 1,
      pageSize: DELIVERY_DEFAULT_PAGE_SIZE,
    });
    salesDeliveryListCache.set(key, {
      deliveries: [deliveryRow("cached")],
      res: {},
      total: 1,
      statusCounts: { all: 1, SEBAGIAN: 1, SELESAI: 0 },
    });

    let resolveList;
    api.listSalesDeliveries.mockImplementation(() => new Promise((resolve, reject) => {
      resolveList = { resolve, reject };
    }));

    await renderPage();
    expect(container.querySelector('[data-testid="delivery-row-cached"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="delivery-list-refreshing"]')).toBeTruthy();

    await act(async () => {
      resolveList.reject({ response: { data: { detail: "sync gagal" } } });
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="delivery-list-sync-error"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="delivery-row-cached"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="list-error-banner"]')).toBeNull();
  });

  test("stale search response is ignored when a newer query settles", async () => {
    let resolvers = [];
    api.listSalesDeliveries.mockImplementation(() => new Promise((resolve) => {
      resolvers.push(resolve);
    }));
    await renderPage();
    // initial load pending
    expect(resolvers.length).toBe(1);

    await typeSearch("OLD");
    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(resolvers.length).toBe(2);

    await typeSearch("NEW");
    await act(async () => {
      jest.advanceTimersByTime(LIVE_SEARCH_DEBOUNCE_MS);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(resolvers.length).toBe(3);

    await act(async () => {
      resolvers[1]({ deliveries: [deliveryRow("old", { sj_number: "SJ-OLD" })] });
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="sj-number-SJ-OLD"]')).toBeNull();

    await act(async () => {
      resolvers[2]({ deliveries: [deliveryRow("new", { sj_number: "SJ-NEW" })] });
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="sj-number-SJ-NEW"]')).toBeTruthy();
  });

  test("dense status badge classes render without padding override on status cell", async () => {
    api.listSalesDeliveries.mockResolvedValue({
      deliveries: [deliveryRow("1", { status: "SELESAI" }), deliveryRow("2", { status: "SEBAGIAN" })],
    });
    await renderPage();
    const selesai = container.querySelector('[data-testid="delivery-status-1"]');
    const sebagian = container.querySelector('[data-testid="delivery-status-2"]');
    expect(selesai.className).toMatch(/is-selesai/);
    expect(sebagian.className).toMatch(/is-sebagian/);
    expect(selesai.closest("td").getAttribute("style") || "").not.toMatch(/padding/);
  });

  test("empty state uses sd-list-empty and primary CTA", async () => {
    api.listSalesDeliveries.mockResolvedValue({ deliveries: [] });
    await renderPage();
    expect(container.querySelector('[data-testid="empty-deliveries"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="btn-empty-create-delivery"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="btn-empty-create-delivery"]').className).toMatch(/sd-btn-primary/);
  });

  test("error state without cache shows sd-banner and not stale rows as fresh", async () => {
    api.listSalesDeliveries.mockRejectedValue({ response: { data: { detail: "gagal muat" } } });
    await renderPage();
    expect(container.querySelector('[data-testid="list-error-banner"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="empty-deliveries"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="delivery-list-sync-error"]')).toBeNull();
  });

  test("detail modal opens and closes", async () => {
    await renderPage();
    await act(async () => {
      container.querySelector('[data-testid="btn-view-delivery-1"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="delivery-detail-modal"]')).toBeTruthy();
    expect(container.querySelector(".sd-modal-card")).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="btn-close-delivery-modal"]').click();
    });
    expect(container.querySelector('[data-testid="delivery-detail-modal"]')).toBeNull();
  });

  test("footer shows authoritative server page range and omits API-cap note", async () => {
    const rows = Array.from({ length: DELIVERY_DEFAULT_PAGE_SIZE }, (_, i) => deliveryRow(String(i + 1)));
    api.listSalesDeliveries.mockResolvedValue({
      deliveries: rows,
      total: 1000,
      page: 1,
      page_size: DELIVERY_DEFAULT_PAGE_SIZE,
      total_pages: 20,
      counts: { all: 1000, SEBAGIAN: 600, SELESAI: 400 },
    });
    await renderPage();
    const footer = container.querySelector('[data-testid="delivery-list-footer"]');
    expect(footer.textContent).toMatch(/Menampilkan 1–50 dari 1\.000 pengiriman|Menampilkan 1–50 dari 1000 pengiriman/);
    expect(container.querySelector('[data-testid="delivery-list-api-cap-note"]')).toBeNull();
    expect(container.querySelector('[data-testid="delivery-page-next"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="delivery-page-size"]')).toBeTruthy();
  });
});
