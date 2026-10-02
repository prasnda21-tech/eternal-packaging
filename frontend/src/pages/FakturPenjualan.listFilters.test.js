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

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}));

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

const DRAFT = row({});
const FINAL = row({
  id: "final-1",
  invoice_number: "INV-FINAL",
  customer: "PT Final",
  financial_status: "finalized",
  accounting_status: "posted",
  payment_status: "LUNAS",
  outstanding_amount: 0,
  can_delete: false,
});
const PAID_DRAFT = row({
  id: "paid-draft",
  invoice_number: "INV-DP",
  customer: "PT DP",
  can_delete: false,
  receipt_count: 1,
  payment_status: "DP",
  outstanding_amount: 400,
});
const CANCELLED = row({
  id: "cancel-1",
  invoice_number: "INV-BATAL",
  customer: "PT Batal",
  financial_status: "cancelled",
  can_delete: false,
  outstanding_amount: 0,
});
const SECOND = row({
  id: "draft-2",
  invoice_number: "INV-2",
  customer: "PT Dua",
});
const FAR = row({
  id: "far-1",
  invoice_number: "INV-JAUH",
  customer: "PT Halaman Jauh",
  po_number: "PO-JAUH",
});

function listPayload(invoices, extra = {}) {
  return {
    invoices,
    count: invoices.length,
    total: extra.total ?? invoices.length,
    tab_total: extra.tab_total ?? invoices.length,
    active_total: extra.active_total ?? 4,
    cancelled_total: extra.cancelled_total ?? 1,
    page: extra.page ?? 1,
    page_size: extra.page_size ?? 20,
  };
}

describe("Faktur Penjualan tabs, filters, and delete selection", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockAuth.user = { id: "u1", name: "Admin", role: "admin" };
    mockAuth.allow = null;
    api.listSalesInvoices.mockReset();
    api.bulkDeleteSalesInvoices.mockReset();
    api.listSalesInvoices.mockImplementation(async (params = {}) => {
      if (params.scope === "cancelled") {
        return listPayload([CANCELLED], { total: 1, tab_total: 1, active_total: 4, cancelled_total: 1 });
      }
      if (params.party) {
        return listPayload([FAR], { total: 1, tab_total: 4, active_total: 4, cancelled_total: 1, page: params.page });
      }
      return listPayload([DRAFT, SECOND, PAID_DRAFT, FINAL], { total: 4, tab_total: 4, page: params.page });
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
    });
  };

  const setValue = async (element, value) => {
    await act(async () => {
      const proto = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(element, value);
      element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
  };

  test("active tab is default and cancelled invoices stay on the other tab", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="tab-faktur-aktif"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="tab-count-active"]').textContent).toBe("4");
    expect(container.querySelector('[data-testid="tab-count-cancelled"]').textContent).toBe("1");
    expect(container.querySelector('[data-testid="invoice-row-draft-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="invoice-row-final-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="invoice-row-cancel-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="invoice-list-range"]').textContent).toContain("4 faktur pada tab Faktur Aktif");

    await act(async () => {
      container.querySelector('[data-testid="tab-faktur-dibatalkan"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="invoice-row-cancel-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="invoice-row-draft-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="invoice-row-final-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="btn-delete-invoice-cancel-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="invoice-checkbox-cancel-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="view-invoice-btn-cancel-1"]').textContent).toBe("Lihat");
    expect(container.querySelector('[data-testid="btn-print-pdf-cancel-1"]').textContent).toBe("Cetak PDF");
    const last = api.listSalesInvoices.mock.calls.at(-1)[0];
    expect(last.scope).toBe("cancelled");
    expect(last.page).toBe(1);
  });

  test("column filters and global search are sent together and can reach another page", async () => {
    await renderPage();
    await setValue(container.querySelector('[data-testid="search-invoice-input"]'), "Sentosa");
    await setValue(container.querySelector('[data-testid="filter-invoice-party"]'), "Halaman Jauh");
    await setValue(container.querySelector('[data-testid="filter-invoice-po"]'), "PO-JAUH");
    await setValue(container.querySelector('[data-testid="filter-invoice-date-from"]'), "2026-01-01");
    await setValue(container.querySelector('[data-testid="filter-invoice-date-to"]'), "2026-01-31");
    await setValue(container.querySelector('[data-testid="filter-invoice-total-min"]'), "0");
    await setValue(container.querySelector('[data-testid="filter-invoice-total-max"]'), "42");
    await setValue(container.querySelector('[data-testid="filter-invoice-outstanding-min"]'), "0");
    await setValue(container.querySelector('[data-testid="filter-invoice-outstanding-max"]'), "0");
    await setValue(container.querySelector('[data-testid="filter-invoice-financial-status"]'), "draft");
    await setValue(container.querySelector('[data-testid="filter-invoice-accounting-status"]'), "not_posted");
    await setValue(container.querySelector('[data-testid="filter-invoice-payment-status"]'), "BELUM DIBAYAR");

    const last = api.listSalesInvoices.mock.calls.at(-1)[0];
    expect(last.q).toBe("Sentosa");
    expect(last.party).toBe("Halaman Jauh");
    expect(last.po_number).toBe("PO-JAUH");
    expect(last.date_from).toBe("2026-01-01");
    expect(last.date_to).toBe("2026-01-31");
    expect(last.total_min).toBe(0);
    expect(last.total_max).toBe(42);
    expect(last.outstanding_min).toBe(0);
    expect(last.outstanding_max).toBe(0);
    expect(last.financial_status).toBe("draft");
    expect(last.accounting_status).toBe("not_posted");
    expect(last.payment_status).toBe("BELUM DIBAYAR");
    expect(last.page).toBe(1);
    expect(last.scope).toBe("active");
    expect(container.querySelector('[data-testid="invoice-row-far-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="invoice-list-count"]').textContent).toContain("hasil filter");
    expect(container.querySelector('[data-testid="invoice-list-range"]').textContent).toContain("hasil filter");
    expect(container.querySelector('[data-testid="tab-count-active"]').textContent).toBe("4");

    await act(async () => {
      container.querySelector('[data-testid="reset-invoice-filters"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="filter-invoice-party"]').value).toBe("");
    expect(container.querySelector('[data-testid="search-invoice-input"]').value).toBe("");
    expect(api.listSalesInvoices.mock.calls.at(-1)[0].party).toBeUndefined();
  });

  test("invalid amount range shows a message and does not request that range", async () => {
    await renderPage();
    const callsBefore = api.listSalesInvoices.mock.calls.length;
    await setValue(container.querySelector('[data-testid="filter-invoice-total-min"]'), "20");
    await setValue(container.querySelector('[data-testid="filter-invoice-total-max"]'), "0");
    expect(container.querySelector('[data-testid="invoice-list-error"]').textContent).toContain("Rentang Total Faktur tidak valid");
    const later = api.listSalesInvoices.mock.calls.slice(callsBefore).map((call) => call[0]);
    expect(later.some((params) => params.total_min === 20 && params.total_max === 0)).toBe(false);
  });

  test("a late response does not replace the newest tab", async () => {
    let releaseFirst;
    let calls = 0;
    api.listSalesInvoices.mockImplementation((params) => {
      calls += 1;
      if (calls === 1) {
        return new Promise((resolve) => {
          releaseFirst = () => resolve(listPayload([DRAFT, FINAL], { total: 2, tab_total: 2, active_total: 2, cancelled_total: 1 }));
        });
      }
      return Promise.resolve(listPayload([CANCELLED], {
        total: 1,
        tab_total: 1,
        active_total: 2,
        cancelled_total: 1,
        page: params.page,
      }));
    });
    await renderPage();
    await act(async () => {
      container.querySelector('[data-testid="tab-faktur-dibatalkan"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="invoice-row-cancel-1"]')).toBeTruthy();
    await act(async () => {
      releaseFirst();
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="invoice-row-cancel-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="invoice-row-draft-1"]')).toBeFalsy();
  });

  test("only deletable drafts on the page can be selected, and a rejected delete disables confirm", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="invoice-checkbox-draft-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="invoice-checkbox-draft-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="invoice-checkbox-final-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="invoice-checkbox-paid-draft"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="btn-delete-invoice-final-1"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="select-all-invoices"]').click();
    });
    expect(container.querySelector('[data-testid="invoice-checkbox-draft-1"]').checked).toBe(true);
    expect(container.querySelector('[data-testid="invoice-checkbox-draft-2"]').checked).toBe(true);
    expect(container.querySelector('[data-testid="btn-hapus-terpilih"]').textContent).toContain("(2)");
    await act(async () => {
      container.querySelector('[data-testid="select-all-invoices"]').click();
    });
    expect(container.querySelector('[data-testid="invoice-checkbox-draft-1"]').checked).toBe(false);

    await act(async () => {
      container.querySelector('[data-testid="invoice-checkbox-draft-1"]').click();
      container.querySelector('[data-testid="invoice-checkbox-draft-2"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="tab-faktur-dibatalkan"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      container.querySelector('[data-testid="tab-faktur-aktif"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="invoice-checkbox-draft-1"]').checked).toBe(false);
    expect(container.querySelector('[data-testid="invoice-checkbox-draft-2"]').checked).toBe(false);

    await act(async () => {
      container.querySelector('[data-testid="invoice-checkbox-draft-1"]').click();
      container.querySelector('[data-testid="invoice-checkbox-draft-2"]').click();
    });
    api.bulkDeleteSalesInvoices.mockRejectedValue({
      response: {
        data: {
          detail: {
            message: "Tidak dapat menghapus.",
            blockers: [{ id: "draft-2", number: "INV-2", reason: "Hapus Penerimaan Penjualan terlebih dahulu." }],
          },
        },
      },
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-hapus-terpilih"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-confirm-delete"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="btn-confirm-delete"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="delete-error"]').textContent).toContain("INV-2");
    await act(async () => {
      container.querySelector('[data-testid="delete-remove-draft-2"]').click();
    });
    expect(container.querySelector('[data-testid="btn-confirm-delete"]').disabled).toBe(false);
  });

  test("view, print, edit, and delete follow permissions", async () => {
    mockAuth.user = { id: "u2", name: "Lihat", role: "crm" };
    mockAuth.allow = new Set(["sales.invoice.view"]);
    await renderPage();
    expect(container.querySelector('[data-testid="view-invoice-btn-draft-1"]').textContent).toBe("Lihat");
    expect(container.querySelector('[data-testid="btn-print-pdf-draft-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="btn-delete-invoice-draft-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="select-all-invoices"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="btn-create-invoice"]')).toBeFalsy();

    mockAuth.allow = new Set(["sales.invoice.create"]);
    await act(async () => {
      root.render(<FakturPenjualan />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="btn-print-pdf-draft-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="view-invoice-btn-draft-1"]').textContent).toBe("Lihat / Edit");
    expect(container.querySelector('[data-testid="invoice-checkbox-draft-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="btn-create-invoice"]')).toBeTruthy();
  });
});
