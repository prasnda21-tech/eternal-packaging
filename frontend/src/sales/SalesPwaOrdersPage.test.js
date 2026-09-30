import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaOrdersPage from "./SalesPwaOrdersPage";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesPwaOrders: jest.fn(),
    salesPwaDocuments: jest.fn(),
  },
  publicApiUrl: (p) => p,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const sample = {
  orders: Array.from({ length: 8 }, (_, i) => ({
    id: `po-${i + 1}`,
    po_number: `PO-A${String(i + 1).padStart(3, "0")}`,
    customer: `Pelanggan ${i + 1}`,
    status: i === 1 ? "HOLD" : "Packing",
    hold: i === 1,
    order_date: "2026-09-18",
    buffer_stock: i === 0,
    tanggal_kirim_overdue: false,
    progress_percent: 40,
    workflow: { packing: "active", order: "completed" },
    documents: { total: i % 3, po: 1, sj: 0, invoice: 0, bukti_kirim: 0 },
  })),
  counts: { semua: 8, berjalan: 7, hold: 1, parsial: 0, selesai: 0 },
};

describe("SalesPwaOrdersPage", () => {
  test("renders dense PO table without sending sales_user_id", async () => {
    api.salesPwaOrders.mockResolvedValue(sample);
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<SalesPwaOrdersPage />);
    });
    await flush();
    expect(api.salesPwaOrders).toHaveBeenCalled();
    const params = api.salesPwaOrders.mock.calls[0][0] || {};
    expect(params.sales_user_id).toBeUndefined();
    expect(container.querySelector('[data-testid="sales-po-table"]')).toBeTruthy();
    expect(container.querySelectorAll("tbody tr").length).toBe(8);
    expect(container.textContent).toContain("PELANGGAN / PO");
    expect(container.textContent).toContain("TAHAP");
    expect(container.textContent).toContain("TANGGAL PO");
    expect(container.textContent).toContain("BUFFER");
    expect(container.querySelector('[aria-label="Filter pelanggan atau PO"]')).toBeTruthy();
    expect(container.querySelector('[aria-label="Filter tahap"]')).toBeTruthy();
    expect(container.querySelector('[aria-label="Filter buffer stock"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sales-pwa-nav"]')).toBeNull();
    expect(container.querySelector('[data-testid="sales-po-remainder"]')).toBeNull();
    act(() => root.unmount());
    container.remove();
  });

  test("clears the previous list while a new filter loads and reports rows left out by the limit", async () => {
    let resolveNext;
    api.salesPwaOrders.mockReset();
    api.salesPwaOrders
      .mockResolvedValueOnce({ ...sample, total: 8 })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve; }));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<SalesPwaOrdersPage />);
    });
    await flush();
    expect(container.textContent).toContain("Semua 8");

    const input = container.querySelector('[aria-label="Filter pelanggan atau PO"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    await act(async () => {
      setter.call(input, "PO-A001");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.textContent).not.toContain("Semua 8");
    expect(container.textContent).toContain("Memuat...");
    const params = api.salesPwaOrders.mock.calls.at(-1)[0];
    expect(params.q).toBe("PO-A001");
    expect(params.customer_q).toBeUndefined();
    expect(params.po_q).toBeUndefined();
    expect(params.limit).toBe(120);

    await act(async () => {
      resolveNext({
        ...sample,
        orders: sample.orders.slice(0, 1),
        total: 4,
        counts: { semua: 1, berjalan: 1, hold: 0, parsial: 0, selesai: 0 },
      });
    });
    expect(container.textContent).toContain("Semua 1");
    expect(container.querySelector('[data-testid="sales-po-remainder"]').textContent).toContain("Menampilkan 1 dari 4 PO");
    act(() => root.unmount());
    container.remove();
  });
});
