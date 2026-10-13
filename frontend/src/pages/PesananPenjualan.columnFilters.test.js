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
    salesOrderOptions: jest.fn().mockResolvedValue({
      sales_options: ["Andi", "Budi"],
      creator_options: [
        { value: "Andi Admin", label: "Andi Admin" },
        { value: "__unknown__", label: "Tidak diketahui" },
      ],
      qty_unit_options: ["pcs", "pack"],
      process_label_options: ["HOLD", "Cancelled", "Order Masuk", "Penjadwalan"],
    }),
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
    created_by_user: "Andi Admin",
    total_quantity: 100,
    quantity_by_unit: [{ unit: "pcs", quantity: 100 }],
    delivery_summary: { total_remaining: 40, status: "SEBAGIAN" },
    current_stage: 5,
    status_label: "Produksi",
    invoice_status: "sudah",
    invoice_status_label: "Sudah",
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
    api.listSalesOrders.mockResolvedValue({ orders: ORDERS, total: 1, page: 1, page_size: 50 });
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

  test("exposes header filters for all data columns and sends server params", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="so-th-filter-customer"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-order-date"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-ship-date"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-qty"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-delivery"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-stage"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-sales"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-creator"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-invoice"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-ship-date-option-today"]')).toBeNull();

    const setInputValue = (node, value) => {
      const proto = node.tagName === "INPUT"
        ? window.HTMLInputElement.prototype
        : window.HTMLTextAreaElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(node, value);
      node.dispatchEvent(new Event("input", { bubbles: true }));
      node.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const customerInput = container.querySelector('[data-testid="so-filter-customer"]');
    await act(async () => {
      setInputValue(customerInput, "Maju");
    });
    await act(async () => {
      container.querySelector('[data-testid="so-th-filter-customer-apply"]').click();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.listSalesOrders.mock.calls.at(-1)[0].customer_q).toBe("Maju");

    await act(async () => {
      setInputValue(container.querySelector('[data-testid="so-filter-ship-date-from"]'), "2026-04-01");
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.listSalesOrders.mock.calls.at(-1)[0].ship_date_from).toBe("2026-04-01");

    await act(async () => {
      container.querySelector('[data-testid="so-th-filter-delivery-option-SEBAGIAN"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesOrders.mock.calls.at(-1)[0].delivery_status).toBe("SEBAGIAN");

    await act(async () => {
      container.querySelector('[data-testid="so-th-filter-stage-option-HOLD"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesOrders.mock.calls.at(-1)[0].process_label).toBe("HOLD");
    expect(api.listSalesOrders.mock.calls.at(-1)[0].list_tab).toBe("active");

    expect(api.salesOrderOptions).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="sales-order-reset-filters"]').textContent).toMatch(/Reset Filter/);
    expect(SALES_ORDER_DELIVERY_STATUS_OPTIONS.map((o) => o.value)).toEqual([
      "BELUM DIKIRIM",
      "SEBAGIAN",
      "SELESAI",
      "DIBATALKAN",
    ]);
    expect(salesOrderColumnFiltersAreActive(EMPTY_SALES_ORDER_COLUMN_FILTERS)).toBe(false);
  });
});
