import { act } from "react";
import { createRoot } from "react-dom/client";
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
    listSalesOrders: jest.fn(),
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

const ORDERS = [
  {
    id: "1",
    po_number: "SO-1001",
    order_date: "2026-04-01",
    customer: "PT Maju Jaya",
    outlet: "Jakarta Pusat",
    sales: "Andi",
    total_quantity: 100,
    delivery_summary: { total_remaining: 40, status: "SEBAGIAN" },
    current_stage: 5,
    status_label: "Produksi",
  },
  {
    id: "2",
    po_number: "SO-2002",
    order_date: "2026-04-10",
    customer: "CV Sejahtera",
    outlet: "Bandung",
    sales: "Budi",
    total_quantity: 20,
    cancelled: true,
    current_stage: 2,
  },
];

describe("Pesanan Penjualan header + column filters", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.listSalesOrders.mockReset();
    api.listSalesOrders.mockResolvedValue({ orders: ORDERS });
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
    });
  };

  test("renders compact title without overline or list description", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="sales-order-title"]').textContent).toBe("Pesanan Penjualan");
    expect(container.textContent).not.toContain("Penjualan · Pesanan Penjualan");
    expect(container.textContent).not.toContain("Daftar PO Penjualan dan pembuatan Sales Order baru");
    expect(container.querySelector('[data-testid="sales-order-refresh-button"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="create-sales-order-btn"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-po"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="order-row-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="order-row-2"]')).toBeTruthy();
  });

  test("column filters compose and clear", async () => {
    await renderPage();
    const customer = container.querySelector('[data-testid="so-filter-customer"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(customer, "maju");
      customer.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="order-row-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="order-row-2"]')).toBeFalsy();

    const qtyMin = container.querySelector('[data-testid="so-filter-qty-min"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(qtyMin, "150");
      qtyMin.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="empty-sales-orders"]')).toBeTruthy();

    await act(async () => {
      container.querySelector('[data-testid="sales-order-clear-column-filters"]').click();
    });
    expect(container.querySelector('[data-testid="order-row-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="order-row-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-filter-customer"]').value).toBe("");
  });
});
