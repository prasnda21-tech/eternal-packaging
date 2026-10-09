/**
 * Status PO initial load + filters + selection (LOCAL / TEST HARNESS ONLY).
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";
import StatusPO from "./StatusPO";

const mockSetSearchParams = jest.fn();
let mockSearch = "";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : to?.pathname || "#"} {...props}>{children}</a>,
  useSearchParams: () => [new URLSearchParams(mockSearch), mockSetSearchParams],
  useLocation: () => ({ pathname: "/laporan/status-po", search: mockSearch, state: null }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u-1", role: "admin", name: "Admin", email: "a@x.com" },
    hasPerm: () => true,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    statusPo: jest.fn(),
    bulkCancel: jest.fn(),
    bulkDelete: jest.fn(),
    listOrders: jest.fn(),
    reminders: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const src = fs.readFileSync(path.join(__dirname, "StatusPO.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const flushDebounce = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 350));
  });
};

function setField(el, value) {
  const proto = el.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
  el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  if (el.tagName !== "SELECT") el.dispatchEvent(new Event("change", { bubbles: true }));
}

const rows = [
  {
    id: "a",
    po_number: "PO-001",
    order_date: "2026-08-10",
    customer: "ABC Pack",
    status: "Operator",
    percent_delivered: 0,
  },
  {
    id: "b",
    po_number: "PO-002",
    order_date: "2026-08-20",
    customer: "XYZ",
    status: "Packing",
    percent_delivered: 25,
  },
  {
    id: "c",
    po_number: "PO-DONE",
    order_date: "2026-07-01",
    customer: "Done Cust",
    status: "Terkirim",
    percent_delivered: 100,
  },
];

function mockPage(list, extra = {}) {
  return {
    rows: list,
    total: list.length,
    total_count: list.length,
    page: 1,
    page_size: 50,
    total_pages: list.length ? 1 : 0,
    has_next: false,
    has_prev: false,
    status_options: [...new Set(list.map((r) => r.status).filter(Boolean))],
    ...extra,
  };
}

describe("Status PO load (source)", () => {
  test("uses dedicated lean status-po endpoint once per load", () => {
    expect(apiSrc).toMatch(/statusPo:\s*\(params = \{\}\) => client\.get\(`\/dashboard\/status-po`/);
    expect(src).toMatch(/api\.statusPo\(/);
    expect(src).toMatch(/loadSeq\.current/);
    expect(src).not.toMatch(/api\.listOrders/);
    expect(src).not.toMatch(/api\.reminders/);
    expect(src).not.toMatch(/dashboard\/production/);
    expect(src).not.toMatch(/view:\s*"summary"/);
  });
});

describe("Status PO load (runtime)", () => {
  let container;
  let root;

  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    mockSearch = "";
    mockSetSearchParams.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.statusPo.mockReset();
    api.bulkCancel.mockReset();
    api.bulkDelete.mockReset();
    api.listOrders.mockReset();
    api.reminders.mockReset();
    api.statusPo.mockImplementation(async (params = {}) => {
      let list = rows;
      if (params.po) list = list.filter((r) => r.po_number.includes(params.po));
      if (params.customer) list = list.filter((r) => r.customer.toLowerCase().includes(String(params.customer).toLowerCase()));
      if (params.status) list = list.filter((r) => r.status === params.status);
      if (params.date_from || params.date_to) {
        list = list.filter((r) => {
          const key = r.order_date;
          if (params.date_from && key < params.date_from) return false;
          if (params.date_to && key > params.date_to) return false;
          return true;
        });
      }
      if (params.view === "active") list = list.filter((r) => r.id !== "c");
      if (params.view === "completed") list = list.filter((r) => r.id === "c");
      if (params.view === "cancelled") list = [];
      return mockPage(list, {
        page: params.page || 1,
        page_size: params.page_size || 50,
        has_next: false,
        has_prev: (params.page || 1) > 1,
      });
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("initial mount fires statusPo once with page defaults and no unrelated endpoints", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    expect(api.statusPo).toHaveBeenCalledTimes(1);
    expect(api.statusPo).toHaveBeenCalledWith({ page: 1, page_size: 50 });
    expect(api.listOrders).not.toHaveBeenCalled();
    expect(api.reminders).not.toHaveBeenCalled();
  });

  test("rows keep PO number, customer, date, status, and completion percent", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    expect(container.querySelector('[data-testid="status-po-row-PO-001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-link-PO-001"]').textContent).toBe("PO-001");
    expect(container.querySelector('[data-testid="status-po-status-PO-001"]').textContent).toContain("Operator");
    expect(container.querySelector('[data-testid="status-po-pct-PO-001"]').textContent).toContain("0%");
    expect(container.querySelector('[data-testid="status-po-status-PO-002"]').textContent).toContain("Packing");
    expect(container.querySelector('[data-testid="status-po-status-PO-DONE"]').textContent).toContain("Terkirim");
    expect(container.textContent).toContain("ABC Pack");
    expect(container.textContent).toContain("10/08/2026");
  });

  test("four tabs render; Aktif tab updates URL view", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    expect(container.querySelector('[data-testid="status-po-tab-all"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-tab-active"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-tab-completed"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-tab-cancelled"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="status-po-tab-active"]').click();
    });
    expect(mockSetSearchParams).toHaveBeenCalled();
    const next = mockSetSearchParams.mock.calls[0][0];
    expect(next.get("view")).toBe("active");
  });

  test("PO, customer, status, and date filters are sent to backend after debounce", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    api.statusPo.mockClear();
    const po = container.querySelector('[data-testid="status-po-filter-po"]');
    await act(async () => {
      setField(po, "PO-001");
    });
    await flushDebounce();
    expect(api.statusPo).toHaveBeenCalled();
    const last = api.statusPo.mock.calls[api.statusPo.mock.calls.length - 1][0];
    expect(last.po).toBe("PO-001");
    expect(last.page).toBe(1);
    expect(container.querySelector('[data-testid="status-po-row-PO-001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-row-PO-002"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="status-po-filter-reset"]').click();
    });
    await flushDebounce();
    expect(container.querySelector('[data-testid="status-po-row-PO-002"]')).toBeTruthy();
  });

  test("refresh recalls status-po with current pagination params", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="status-po-refresh"]').click();
    });
    await flush();
    expect(api.statusPo).toHaveBeenCalledTimes(2);
    expect(api.statusPo.mock.calls.every((c) => c[0].page === 1 && c[0].page_size === 50)).toBe(true);
  });

  test("pagination controls reflect backend totals", async () => {
    api.statusPo.mockResolvedValue(mockPage(rows.slice(0, 2), {
      total_count: 105,
      total: 105,
      page: 1,
      total_pages: 3,
      has_next: true,
      has_prev: false,
    }));
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    expect(container.querySelector('[data-testid="status-po-count"]').textContent).toContain("dari 105 PO");
    expect(container.querySelector('[data-testid="status-po-page-label"]').textContent).toContain("Halaman 1 dari 3");
    expect(container.querySelector('[data-testid="status-po-page-prev"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="status-po-page-next"]').disabled).toBe(false);
  });

  test("status dropdown uses server status_options not only current page rows", async () => {
    api.statusPo.mockResolvedValue(mockPage(rows.slice(0, 1), {
      status_options: ["Operator", "Packing", "Terkirim", "HiddenFromPage"],
      total_count: 90,
      total: 90,
      total_pages: 2,
      has_next: true,
    }));
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    const status = container.querySelector('[data-testid="status-po-filter-status"]');
    const labels = [...status.querySelectorAll("option")].map((o) => o.value);
    expect(labels).toContain("HiddenFromPage");
    expect(labels).toContain("Terkirim");
  });

  test("stale filter response does not overwrite newer request", async () => {
    act(() => root.unmount());
    root = createRoot(container);
    let releaseFirst;
    const firstGate = new Promise((r) => { releaseFirst = r; });
    let call = 0;
    api.statusPo.mockImplementation(async (params = {}) => {
      call += 1;
      if (call === 1) {
        await firstGate;
        return mockPage([rows[0]], { total_count: 1, status_options: ["Operator"] });
      }
      return mockPage([rows[1]], { total_count: 1, status_options: ["Packing"] });
    });
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    await act(async () => {
      setField(container.querySelector('[data-testid="status-po-filter-customer"]'), "XYZ");
    });
    await flushDebounce();
    expect(api.statusPo.mock.calls.length).toBeGreaterThanOrEqual(2);
    await act(async () => {
      releaseFirst();
      await Promise.resolve();
    });
    await flush();
    expect(container.querySelector('[data-testid="status-po-row-PO-002"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-row-PO-001"]')).toBeFalsy();
  });

  test("select-all and row checkbox stay scoped to visible rows", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    expect(container.querySelectorAll("tbody tr[data-testid^='status-po-row-']")).toHaveLength(3);
    await act(async () => {
      container.querySelector('[data-testid="status-po-check-PO-001"]').click();
      container.querySelector('[data-testid="status-po-check-PO-002"]').click();
      container.querySelector('[data-testid="status-po-check-PO-DONE"]').click();
    });
    expect(container.querySelector('[data-testid="status-po-selected-count"]').textContent).toContain("3");
    await act(async () => {
      container.querySelector('[data-testid="status-po-select-all"]').click();
    });
    expect(container.querySelector('[data-testid="status-po-bulk-bar"]')).toBeFalsy();
  });
});
