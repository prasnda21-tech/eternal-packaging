/**
 * Status PO initial load + filters + selection (LOCAL / TEST HARNESS ONLY).
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";
import StatusPO from "./StatusPO";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : to?.pathname || "#"} {...props}>{children}</a>,
  useSearchParams: () => [new URLSearchParams(""), jest.fn()],
  useLocation: () => ({ pathname: "/laporan/status-po", search: "", state: null }),
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

describe("Status PO load (source)", () => {
  test("uses dedicated lean status-po endpoint once per load", () => {
    expect(apiSrc).toMatch(/statusPo:\s*\(params = \{\}\) => client\.get\(`\/dashboard\/status-po`/);
    expect(src).toMatch(/api\.statusPo\(view \? \{ view \} : \{\}\)/);
    expect(src).toMatch(/loadInFlight\.current/);
    expect(src).toMatch(/if \(loadInFlight\.current\) return;/);
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
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.statusPo.mockReset();
    api.bulkCancel.mockReset();
    api.bulkDelete.mockReset();
    api.listOrders.mockReset();
    api.reminders.mockReset();
    api.statusPo.mockResolvedValue({ rows, total: rows.length });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("initial mount fires statusPo once with no view and no unrelated endpoints", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    expect(api.statusPo).toHaveBeenCalledTimes(1);
    expect(api.statusPo).toHaveBeenCalledWith({});
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
    expect(container.querySelector('[data-testid="status-po-status-PO-001"]').textContent).toBe("Operator");
    expect(container.querySelector('[data-testid="status-po-delivered-PO-001"]').textContent).toBe("0%");
    expect(container.querySelector('[data-testid="status-po-status-PO-002"]').textContent).toBe("Packing");
    expect(container.querySelector('[data-testid="status-po-delivered-PO-002"]').textContent).toBe("25%");
    expect(container.querySelector('[data-testid="status-po-status-PO-DONE"]').textContent).toBe("Terkirim");
    expect(container.querySelector('[data-testid="status-po-delivered-PO-DONE"]').textContent).toBe("100%");
    expect(container.textContent).toContain("ABC Pack");
    expect(container.textContent).toContain("10/08/26");
  });

  test("PO, customer, status, and date filters plus reset still work", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    const po = container.querySelector('[data-testid="status-po-filter-po"]');
    await act(async () => {
      setField(po, "PO-001");
    });
    expect(container.querySelector('[data-testid="status-po-row-PO-001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-row-PO-002"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="status-po-filter-reset"]').click();
    });
    expect(container.querySelector('[data-testid="status-po-row-PO-002"]')).toBeTruthy();

    const customer = container.querySelector('[data-testid="status-po-filter-customer"]');
    await act(async () => {
      setField(customer, "XYZ");
    });
    expect(container.querySelector('[data-testid="status-po-row-PO-002"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-row-PO-001"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="status-po-filter-reset"]').click();
    });
    const status = container.querySelector('[data-testid="status-po-filter-status"]');
    await act(async () => {
      setField(status, "Terkirim");
    });
    expect(container.querySelector('[data-testid="status-po-row-PO-DONE"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-row-PO-001"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="status-po-filter-reset"]').click();
    });
    const from = container.querySelector('[data-testid="status-po-filter-date-from"]');
    const to = container.querySelector('[data-testid="status-po-filter-date-to"]');
    await act(async () => {
      setField(from, "2026-08-01");
      setField(to, "2026-08-31");
    });
    expect(container.querySelector('[data-testid="status-po-row-PO-001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-row-PO-002"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-row-PO-DONE"]')).toBeFalsy();
  });

  test("refresh recalls the same status-po helper", async () => {
    await act(async () => {
      root.render(<StatusPO />);
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="status-po-refresh"]').click();
    });
    await flush();
    expect(api.statusPo).toHaveBeenCalledTimes(2);
    expect(api.statusPo.mock.calls.every((c) => JSON.stringify(c[0]) === "{}")).toBe(true);
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
    await act(async () => {
      container.querySelector('[data-testid="status-po-check-PO-001"]').click();
      container.querySelector('[data-testid="status-po-check-PO-002"]').click();
    });
    expect(container.querySelector('[data-testid="status-po-selected-count"]').textContent).toContain("2");
    const customer = container.querySelector('[data-testid="status-po-filter-customer"]');
    await act(async () => {
      setField(customer, "XYZ");
    });
    expect(container.querySelector('[data-testid="status-po-selected-count"]').textContent).toContain("1");
  });
});
