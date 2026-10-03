import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import {
  EMPTY_SALES_ORDER_COLUMN_FILTERS,
  applySalesOrderColumnFilters,
  salesOrderStageLabel,
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
    expect(container.querySelector('[data-testid="sales-order-title"]').textContent).toBe("Seluruh Pesanan Penjualan");
    expect(container.textContent).not.toContain("Penjualan · Pesanan Penjualan");
    expect(container.textContent).not.toContain("Daftar PO Penjualan dan pembuatan Sales Order baru");
    expect(container.textContent).not.toContain("Daftar pesanan penjualan dari pelanggan.");
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

  test("zero shipped quantity leaves the progress bar empty", async () => {
    api.listSalesOrders.mockResolvedValue({
      orders: [{
        id: "z",
        po_number: "SO-0",
        order_date: "2026-04-01",
        customer: "Nol Kirim",
        outlet: "Gudang",
        sales: "Citra",
        total_quantity: 80,
        quantity_by_unit: [{ unit: "pcs", quantity: 80 }],
        delivery_summary: {
          total_ordered: 80,
          total_shipped: 0,
          total_remaining: 80,
          status: "BELUM DIKIRIM",
        },
        current_stage: 3,
        status_label: "Design",
      }],
      total: 1,
      capped: false,
    });
    await renderPage();
    const fill = container.querySelector('[data-testid="order-progress-z"] .so-progress-fill');
    expect(fill).toBeTruthy();
    expect(fill.style.width).toBe("0%");
    expect(container.querySelector('[data-testid="order-progress-z"]').textContent)
      .toContain("0 terkirim / 80 pesanan pcs");
    expect(container.querySelector('[data-testid="so-th-filter-outlet"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-sales"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="so-th-filter-delivery"]')).toBeTruthy();
  });

  test("Proses / Sales badge uses only the stage label helper", async () => {
    const blankStages = [null, "", "   ", "0", 99];
    const blanks = blankStages.map((current_stage, index) => ({
      id: `blank-${index}`,
      po_number: `SO-BLANK-${index}`,
      order_date: "2026-04-01",
      customer: "Tanpa Tahap",
      outlet: "Gudang",
      sales: "Citra",
      total_quantity: 10,
      current_stage,
    }));
    const legacy = {
      id: "legacy",
      po_number: "SO-LEGACY",
      order_date: "2026-04-02",
      customer: "Label Lama",
      outlet: "Gudang",
      sales: "Citra",
      total_quantity: 10,
      current_stage: 99,
      status_label: "Label Lama",
    };
    const scheduled = {
      id: "scheduled",
      po_number: "SO-SCHED",
      order_date: "2026-04-03",
      customer: "Jadwal",
      outlet: "Gudang",
      sales: "Citra",
      total_quantity: 10,
      current_stage: 5,
      status_label: "Produksi",
    };
    const cancelled = {
      id: "cancelled",
      po_number: "SO-CANCEL",
      order_date: "2026-04-04",
      customer: "Batal",
      outlet: "Gudang",
      sales: "Citra",
      total_quantity: 10,
      cancelled: true,
      hold: true,
      current_stage: 5,
      status_label: "Penjadwalan",
    };
    const held = {
      id: "held",
      po_number: "SO-HOLD",
      order_date: "2026-04-05",
      customer: "Tahan",
      outlet: "Gudang",
      sales: "Citra",
      total_quantity: 10,
      hold: true,
      current_stage: 5,
      status_label: "Penjadwalan",
    };
    const orders = [...blanks, legacy, scheduled, cancelled, held];
    blanks.forEach((order) => {
      expect(salesOrderStageLabel(order)).toBe("");
    });
    expect(applySalesOrderColumnFilters(blanks, {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      stageText: "Tahap",
    })).toEqual([]);
    expect(applySalesOrderColumnFilters(blanks, {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      stageText: "—",
    })).toEqual([]);
    expect(salesOrderStageLabel(legacy)).toBe("Label Lama");
    expect(salesOrderStageLabel(scheduled)).toBe("Penjadwalan");
    expect(salesOrderStageLabel(cancelled)).toBe("Cancelled");
    expect(salesOrderStageLabel(held)).toBe("HOLD");

    api.listSalesOrders.mockResolvedValue({ orders });
    await renderPage();

    const badge = (id) => container.querySelector(`[data-testid="order-row-${id}"] .so-list-badge`);
    blanks.forEach((order) => {
      const node = badge(order.id);
      expect(node.textContent).toBe("—");
      expect(node.textContent).not.toContain("Tahap");
      expect(node.className).toBe(order.current_stage >= 14 ? "so-list-badge is-done" : "so-list-badge is-open");
    });
    expect(badge("legacy").textContent).toBe("Label Lama");
    expect(badge("legacy").className).toBe("so-list-badge is-done");
    expect(badge("scheduled").textContent).toBe("Penjadwalan");
    expect(badge("scheduled").className).toBe("so-list-badge is-open");
    expect(badge("cancelled").textContent).toBe("Cancelled");
    expect(badge("cancelled").className).toBe("so-list-badge is-cancel");
    expect(badge("held").textContent).toBe("HOLD");
    expect(badge("held").className).toBe("so-list-badge is-hold");

    const stageText = container.querySelector('[data-testid="so-filter-stage-text"]');
    const setStageText = async (value) => {
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(stageText, value);
        stageText.dispatchEvent(new Event("input", { bubbles: true }));
      });
    };
    await setStageText("Penjadwalan");
    expect(container.querySelector('[data-testid="order-row-scheduled"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="order-row-cancelled"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="order-row-held"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="order-row-blank-0"]')).toBeFalsy();
    await setStageText("Produksi");
    expect(container.querySelector('[data-testid="order-row-scheduled"]')).toBeFalsy();
    await setStageText("Label Lama");
    expect(container.querySelector('[data-testid="order-row-legacy"]')).toBeTruthy();
    expect(container.querySelectorAll("[data-testid^='order-row-']").length).toBe(1);
  });
});

