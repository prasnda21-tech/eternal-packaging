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

  const openFilter = async (key) => {
    await act(async () => {
      container.querySelector(`[data-testid="filter-invoice-open-${key}"]`).click();
    });
  };

  const applyFilter = async () => {
    await act(async () => {
      document.querySelector('[data-testid="filter-invoice-apply"]').click();
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
    const pageSize = 20;
    const tabTotal = pageSize + 1;
    api.listSalesInvoices.mockImplementation(async (params = {}) => {
      if (params.scope === "cancelled") {
        return listPayload([CANCELLED], { total: 1, tab_total: 1, active_total: tabTotal, cancelled_total: 1, page_size: params.page_size });
      }
      if (params.party) {
        return listPayload([FAR], {
          total: 1,
          tab_total: tabTotal,
          active_total: tabTotal,
          cancelled_total: 1,
          page: params.page,
          page_size: params.page_size,
        });
      }
      return listPayload([DRAFT, SECOND, PAID_DRAFT, FINAL], {
        total: tabTotal,
        tab_total: tabTotal,
        active_total: tabTotal,
        cancelled_total: 1,
        page: params.page,
        page_size: params.page_size,
      });
    });
    await renderPage();
    const opened = api.listSalesInvoices.mock.calls.at(-1)[0];
    expect(opened.page).toBe(1);
    expect(opened.page_size).toBe(pageSize);
    expect(tabTotal).toBeGreaterThan(opened.page_size);
    expect(container.querySelector('[data-testid="invoice-row-far-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="invoice-list-range"]').textContent).toContain("dari 21");
    await setValue(container.querySelector('[data-testid="search-invoice-input"]'), "Sentosa");
    await openFilter("party");
    await setValue(document.querySelector('[data-testid="filter-invoice-party"]'), "Halaman Jauh");
    await applyFilter();
    await openFilter("po");
    await setValue(document.querySelector('[data-testid="filter-invoice-po"]'), "PO-JAUH");
    await applyFilter();
    await openFilter("date");
    await setValue(document.querySelector('[data-testid="filter-invoice-date-from"]'), "2026-01-01");
    await setValue(document.querySelector('[data-testid="filter-invoice-date-to"]'), "2026-01-31");
    await applyFilter();
    await openFilter("total");
    await setValue(document.querySelector('[data-testid="filter-invoice-total-min"]'), "0");
    await setValue(document.querySelector('[data-testid="filter-invoice-total-max"]'), "42");
    await applyFilter();
    await openFilter("outstanding");
    await setValue(document.querySelector('[data-testid="filter-invoice-outstanding-min"]'), "0");
    await setValue(document.querySelector('[data-testid="filter-invoice-outstanding-max"]'), "0");
    await applyFilter();
    await openFilter("status");
    await act(async () => {
      document.querySelector('[data-testid="filter-invoice-financial-draft"]').click();
      document.querySelector('[data-testid="filter-invoice-accounting-not_posted"]').click();
    });
    await applyFilter();
    await openFilter("payment");
    await act(async () => {
      document.querySelector('[data-testid="filter-invoice-payment-BELUM-DIBAYAR"]').click();
    });
    await applyFilter();
    expect(container.querySelector('[data-testid="filter-invoice-active-party"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="filter-invoice-active-payment"]')).toBeTruthy();

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
    expect(container.querySelector('[data-testid="tab-count-active"]').textContent).toBe(String(tabTotal));

    await openFilter("party");
    await act(async () => {
      document.querySelector('[data-testid="filter-invoice-column-reset"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    const afterColumnReset = api.listSalesInvoices.mock.calls.at(-1)[0];
    expect(afterColumnReset.party).toBeUndefined();
    expect(afterColumnReset.po_number).toBe("PO-JAUH");
    expect(container.querySelector('[data-testid="filter-invoice-active-party"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="filter-invoice-active-po"]')).toBeTruthy();

    await act(async () => {
      container.querySelector('[data-testid="reset-invoice-filters"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(document.querySelector('[data-testid="filter-invoice-party"]')).toBeNull();
    expect(container.querySelector('[data-testid="search-invoice-input"]').value).toBe("");
    expect(container.querySelector('[data-testid="filter-invoice-active-po"]')).toBeFalsy();
    expect(api.listSalesInvoices.mock.calls.at(-1)[0].party).toBeUndefined();
    expect(api.listSalesInvoices.mock.calls.at(-1)[0].po_number).toBeUndefined();
  });

  test("invalid date range shows a message and does not request that range", async () => {
    await renderPage();
    const callsBefore = api.listSalesInvoices.mock.calls.length;
    await openFilter("date");
    await setValue(document.querySelector('[data-testid="filter-invoice-date-from"]'), "2026-05-02");
    await setValue(document.querySelector('[data-testid="filter-invoice-date-to"]'), "2026-05-01");
    expect(api.listSalesInvoices.mock.calls.length).toBe(callsBefore);
    await applyFilter();
    expect(document.querySelector('[data-testid="invoice-filter-panel-error"]').textContent).toContain("Rentang tanggal tidak valid");
    expect(container.querySelector('[data-testid="invoice-list-error"]').textContent).toContain("Rentang tanggal tidak valid");
    const later = api.listSalesInvoices.mock.calls.slice(callsBefore).map((call) => call[0]);
    expect(later.some((params) => params.date_from === "2026-05-02" && params.date_to === "2026-05-01")).toBe(false);
  });

  test("a pending valid response is ignored after the range becomes invalid", async () => {
    let releaseValid;
    api.listSalesInvoices.mockImplementation(() => new Promise((resolve) => {
      releaseValid = () => resolve(listPayload([DRAFT, FINAL], {
        total: 2,
        tab_total: 2,
        active_total: 2,
        cancelled_total: 1,
      }));
    }));
    await renderPage();
    expect(container.querySelector('[data-testid="invoice-row-draft-1"]')).toBeFalsy();

    await openFilter("total");
    await act(async () => {
      const min = document.querySelector('[data-testid="filter-invoice-total-min"]');
      const max = document.querySelector('[data-testid="filter-invoice-total-max"]');
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(min, "20");
      min.dispatchEvent(new Event("input", { bubbles: true }));
      setter.call(max, "0");
      max.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      document.querySelector('[data-testid="filter-invoice-apply"]').click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(document.querySelector('[data-testid="invoice-filter-panel-error"]').textContent).toContain("Rentang Total Faktur tidak valid");
    expect(container.querySelector('[data-testid="invoice-list-error"]').textContent).toContain("Rentang Total Faktur tidak valid");
    expect(api.listSalesInvoices.mock.calls.some((call) => call[0].total_min === 20 && call[0].total_max === 0)).toBe(false);

    await act(async () => {
      releaseValid();
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="invoice-list-error"]').textContent).toContain("Rentang Total Faktur tidak valid");
    expect(container.querySelector('[data-testid="invoice-row-draft-1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="invoice-row-final-1"]')).toBeFalsy();
    expect(container.textContent).not.toContain("Memuat daftar Faktur Penjualan");
    expect(container.querySelector('[data-testid="invoice-list-count"]').textContent).toContain("0");
  });

  test("invalid amount range shows a message and does not request that range", async () => {
    await renderPage();
    const callsBefore = api.listSalesInvoices.mock.calls.length;
    await openFilter("total");
    await setValue(document.querySelector('[data-testid="filter-invoice-total-min"]'), "20");
    await setValue(document.querySelector('[data-testid="filter-invoice-total-max"]'), "0");
    await applyFilter();
    expect(document.querySelector('[data-testid="invoice-filter-panel-error"]').textContent).toContain("Rentang Total Faktur tidak valid");
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

  test("escape and an outside click discard a draft without requesting it", async () => {
    await renderPage();
    const callsBefore = api.listSalesInvoices.mock.calls.length;
    await openFilter("party");
    expect(container.querySelector('[data-testid="filter-invoice-open-party"]').getAttribute("aria-expanded")).toBe("true");
    await setValue(document.querySelector('[data-testid="filter-invoice-party"]'), "Belum diterapkan");
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(document.querySelector('[data-testid="invoice-filter-panel"]')).toBeNull();
    expect(container.querySelector('[data-testid="filter-invoice-open-party"]').getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(container.querySelector('[data-testid="filter-invoice-open-party"]'));

    await openFilter("po");
    await setValue(document.querySelector('[data-testid="filter-invoice-po"]'), "PO-X");
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(document.querySelector('[data-testid="invoice-filter-panel"]')).toBeNull();
    expect(api.listSalesInvoices.mock.calls.slice(callsBefore).some((call) => call[0].party || call[0].po_number)).toBe(false);
    expect(container.querySelector('[data-testid="filter-invoice-active-party"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="filter-invoice-active-po"]')).toBeFalsy();
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
