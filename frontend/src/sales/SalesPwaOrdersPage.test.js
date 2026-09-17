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
    tanggal_kirim: "2026-09-20",
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
    expect(container.textContent).toContain("TANGGAL KIRIM");
    expect(container.querySelector('[data-testid="sales-pwa-nav"]')).toBeNull();
    act(() => root.unmount());
    container.remove();
  });
});
