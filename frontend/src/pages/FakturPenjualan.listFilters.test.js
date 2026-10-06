import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import FakturPenjualan from "./FakturPenjualan";

const mockAuth = {
  user: { id: "u1", name: "Admin", role: "admin" },
  allow: null,
};

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: "/penjualan/faktur-penjualan", search: "", state: null }),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
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
    downloadSalesInvoicePdf: jest.fn(),
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

jest.mock("../lib/salesDatePresets", () => {
  const actual = jest.requireActual("../lib/salesDatePresets");
  return {
    ...actual,
    wibTodayKey: () => "2026-10-06",
    invoiceDatePresetRange: (preset) => {
      if (preset === "today") return { date_from: "2026-10-06", date_to: "2026-10-06" };
      if (preset === "this_month") return { date_from: "2026-10-01", date_to: "2026-10-31" };
      if (preset === "last_month") return { date_from: "2026-09-01", date_to: "2026-09-30" };
      if (preset === "this_week") return { date_from: "2026-10-05", date_to: "2026-10-11" };
      return { date_from: "", date_to: "" };
    },
  };
});

global.IS_REACT_ACT_ENVIRONMENT = true;

function row(overrides) {
  return {
    id: "draft-1",
    invoice_number: "INV-DRAFT",
    customer: "PT Aktif",
    po_id: "po-1",
    po_number: "PO-1",
    invoice_date: "2026-03-01",
    grand_total: 1000,
    outstanding_amount: 1000,
    financial_status: "draft",
    accounting_status: "not_posted",
    payment_status: "BELUM DIBAYAR",
    can_delete: true,
    receipt_count: 0,
    ...overrides,
  };
}

describe("Faktur Penjualan selectable header filters", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockAuth.allow = null;
    api.listSalesInvoices.mockReset();
    api.listSalesInvoices.mockResolvedValue({
      invoices: [row({})],
      total: 1,
      tab_total: 1,
      active_total: 1,
      cancelled_total: 0,
      page: 1,
      page_size: 20,
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const renderPage = async () => {
    await act(async () => {
      root.render(<FakturPenjualan />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  test("no free-text/amount header inputs; date/status/payment are selectable", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="fp-th-filter-date"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="fp-th-filter-financial"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="fp-th-filter-payment"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="filter-invoice-party"]')).toBeNull();
    expect(container.querySelector('[data-testid="filter-invoice-total-min"]')).toBeNull();
    expect(container.querySelector('thead input[type="text"]')).toBeNull();
    expect(container.querySelector('thead input[type="date"]')).toBeNull();

    await act(async () => {
      container.querySelector('[data-testid="fp-th-filter-date-option-today"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    let last = api.listSalesInvoices.mock.calls.at(-1)[0];
    expect(last.date_from).toBe("2026-10-06");
    expect(last.date_to).toBe("2026-10-06");
    expect(last.page).toBe(1);

    await act(async () => {
      container.querySelector('[data-testid="fp-th-filter-financial-option-draft"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    last = api.listSalesInvoices.mock.calls.at(-1)[0];
    expect(last.financial_status).toBe("draft");

    await act(async () => {
      container.querySelector('[data-testid="fp-th-filter-payment-option-LUNAS"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    last = api.listSalesInvoices.mock.calls.at(-1)[0];
    expect(last.payment_status).toBe("LUNAS");

    const cancelledTab = container.querySelector('[data-testid="tab-faktur-dibatalkan"]');
    await act(async () => {
      cancelledTab.click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.listSalesInvoices.mock.calls.at(-1)[0].scope).toBe("cancelled");
    expect(cancelledTab.getAttribute("aria-selected")).toBe("true");

    await act(async () => {
      container.querySelector('[data-testid="reset-invoice-filters"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    last = api.listSalesInvoices.mock.calls.at(-1)[0];
    expect(last.date_from).toBeUndefined();
    expect(last.financial_status).toBeUndefined();
    expect(last.payment_status).toBeUndefined();
    expect(last.q).toBeUndefined();
    expect(last.scope).toBe("cancelled");
    expect(last.page).toBe(1);
  });
});
