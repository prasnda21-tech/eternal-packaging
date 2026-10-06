import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import {
  EMPTY_SALES_ORDER_COLUMN_FILTERS,
  SALES_ORDER_DELIVERY_STATUS_OPTIONS,
  salesOrderColumnFiltersAreActive,
} from "../lib/pesananPenjualanColumnFilters";
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
    listSalesOrders: jest.fn(),
    salesOrderOptions: jest.fn().mockResolvedValue({ sales_options: ["Andi", "Budi"] }),
  },
}));

jest.mock("../components/ui/popover", () => ({
  Popover: ({ children, open }) => <div data-open={open ? "1" : "0"}>{children}</div>,
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children }) => <div data-testid="hsf-popover">{children}</div>,
}));

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const ORDERS = [
  {
    id: "1",
    po_number: "SO-1001",
    order_date: "2026-04-01",
    tanggal_pengiriman: "2026-04-05",
    customer: "PT Maju Jaya",
    outlet: "Jakarta Pusat",
    sales: "Andi",
    total_quantity: 100,
    delivery_summary: { total_remaining: 40, status: "SEBAGIAN" },
    current_stage: 5,
    status_label: "Produksi",
  },
];

describe("Pesanan Penjualan selectable header filters", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.listSalesOrders.mockReset();
    api.listSalesOrders.mockResolvedValue({ orders: ORDERS, total: 1, page: 1, page_size: 10 });
    api.salesOrderOptions.mockClear();
    mockSetSearchParams.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const renderPage = async () => {
    await act(async () => {
      root.render(<PesananPenjualan />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  test("funnels only on approved columns and send server params", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="so-th-filter-ship-date"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-delivery"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-sales"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-stage"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-filter-customer"]')).toBeNull();
    expect(container.querySelector('[data-testid="so-filter-qty-min"]')).toBeNull();
    expect(container.querySelector('thead input[type="text"]')).toBeNull();
    expect(container.querySelector('thead input[type="number"]')).toBeNull();
    expect(container.querySelector('thead input[type="date"]')).toBeNull();

    await act(async () => {
      container.querySelector('[data-testid="so-th-filter-ship-date-option-today"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    const last = api.listSalesOrders.mock.calls.at(-1)[0];
    expect(last.tanggal_pengiriman_preset).toBe("today");
    expect(last.page).toBe(1);

    await act(async () => {
      container.querySelector('[data-testid="so-th-filter-delivery-option-SEBAGIAN"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesOrders.mock.calls.at(-1)[0].delivery_status).toBe("SEBAGIAN");

    expect(api.salesOrderOptions).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="so-th-filter-sales"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="search-sales-orders-input"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sales-order-reset-filters"]').textContent).toMatch(/Reset Filter/);
    expect(SALES_ORDER_DELIVERY_STATUS_OPTIONS.map((o) => o.value)).toEqual([
      "BELUM DIKIRIM",
      "SEBAGIAN",
      "SELESAI",
      "DIBATALKAN",
    ]);
    expect(salesOrderColumnFiltersAreActive(EMPTY_SALES_ORDER_COLUMN_FILTERS, "all")).toBe(false);
  });
});
