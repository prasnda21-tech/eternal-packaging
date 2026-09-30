import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { SALES_PWA_PO_FILTER_KEY, SALES_PWA_PO_SCROLL_KEY } from "../lib/salesPwaPoListState";
import SalesPwaOrderDetailPage from "./SalesPwaOrderDetailPage";

const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  Link: ({ to, state, children, ...props }) => (
    <a href={to} data-route-state={state ? JSON.stringify(state) : ""} {...props}>{children}</a>
  ),
  useParams: () => ({ id: "order-1" }),
  useNavigate: () => mockNavigate,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesPwaOrder: jest.fn(),
    salesPwaDocuments: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const order = {
  id: "order-1",
  customer: "Tala Bumi Coffee",
  po_number: "PO/2026/09/23/000023",
  order_date: "2026-09-23",
  status: "Packing",
  progress_percent: 75,
  active_stage: "packing",
  buffer_stock: true,
  tanggal_kirim: "2026-10-02",
  tanggal_kirim_overdue: true,
  address: "Jl. Seturan",
  notes: "Kirim pagi",
  recipient: "Budi",
  documents: { po: 1, sj: 0, invoice: 1, bukti_kirim: 0, total: 2 },
  line_items: [
    { idx: 0, name: "Cup 16oz", quantity: 1000, unit: "pcs", color: "Putih", production_process: "print" },
    { idx: 1, name: "Sedotan", quantity: 500, unit: "pcs", color: "", production_process: "" },
  ],
};

const documents = {
  po_number: order.po_number,
  customer: order.customer,
  counts: { total: 2, po: 1, sj: 0, invoice: 1, bukti_kirim: 0 },
  po: { available: true, filename: "po.pdf" },
  sj: [],
  invoices: [{ id: "inv-1", number: "INV-1", available: true }],
  bukti_kirim: [],
};

async function renderDetail() {
  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => { root.render(<SalesPwaOrderDetailPage />); });
  await act(async () => { await Promise.resolve(); });
  return { container, root };
}

beforeEach(() => {
  sessionStorage.clear();
  mockNavigate.mockReset();
  api.salesPwaOrder.mockReset();
  api.salesPwaDocuments.mockReset();
  api.salesPwaOrder.mockResolvedValue({ order });
  api.salesPwaDocuments.mockResolvedValue(documents);
});

test("shows the sales workspace from existing detail fields", async () => {
  const { container, root } = await renderDetail();
  const header = container.querySelector(".spwa-top").textContent;
  expect(header).toContain("Tala Bumi Coffee");
  expect(header.indexOf("Tala Bumi Coffee")).toBeLessThan(header.indexOf("PO/2026/09/23/000023"));
  expect(header).toContain("23/09/26");
  expect(container.querySelector('[data-testid="sales-detail-back"]').getAttribute("href")).toBe("/sales/po");
  const status = container.querySelector('[data-testid="sales-detail-status"]');
  expect(status.textContent).toContain("Packing");
  expect(status.textContent).toContain("Progress 75%");
  expect(status.textContent).toContain("STATUS PO");
  expect(status.textContent).toContain("Tahap aktif");
  expect(status.textContent).toContain("Pengiriman");
  expect(status.textContent).toContain("02/10/26");
  expect(status.textContent).not.toContain("Jl. Seturan");
  expect(status.textContent).toContain("Kirim terlambat");
  expect(status.textContent).toContain("Buffer Stock");
  expect(container.querySelector('[data-testid="sales-detail-customer"]').textContent).toContain("Jl. Seturan");
  expect(container.querySelector(".spwa-progress")).toBeNull();
  const items = container.querySelector('[data-testid="sales-detail-items"]').textContent;
  expect(items).toContain("Cup 16oz");
  expect(items).toContain("1000 pcs");
  expect(items).toContain("Warna: Putih");
  expect(items).toContain("Cetak");
  expect(items).toContain("Sedotan");
  const cards = container.querySelectorAll(".spwa-item-card");
  expect(cards[1].textContent).not.toContain("Warna");
  expect(cards[1].textContent).not.toContain("Cetak");
  const notes = container.querySelector('[data-testid="sales-detail-notes"]').textContent;
  expect(notes).toContain("Penerima: Budi");
  expect(notes).toContain("Kirim pagi");
  expect(container.innerHTML).not.toContain("customer-approval");
  expect(container.innerHTML).not.toContain("token");
  act(() => root.unmount());
});

test("opens an existing document viewer and leaves list session state intact", async () => {
  sessionStorage.setItem(SALES_PWA_PO_FILTER_KEY, JSON.stringify({ status: "hold", stage: "Packing" }));
  sessionStorage.setItem(SALES_PWA_PO_SCROLL_KEY, "740");
  const { container, root } = await renderDetail();
  const invoice = container.querySelector('[data-testid="sales-doc-open-invoice"]');
  expect(invoice.getAttribute("href")).toBe("/sales/po/order-1/dokumen/invoice/inv-1");
  expect(JSON.parse(invoice.dataset.routeState)).toEqual({ salesDocumentFrom: "/sales/po/order-1" });
  expect(invoice.textContent).toBe("Buka Dokumen");
  expect(container.textContent).not.toContain("Belum ada dokumen tersedia");
  expect(container.querySelector('[data-testid="sales-doc-row-invoice"]').textContent).toContain("Tersedia");
  expect(container.querySelector('[data-testid="sales-doc-row-sj"]').textContent).toContain("Belum ada");
  expect(container.querySelector('[data-testid="sales-doc-open-sj"]')).toBeNull();
  expect(container.querySelector('[data-testid="sales-doc-row-proof"]').textContent).toContain("Belum ada");
  expect(container.querySelector('[data-testid="sales-doc-open-po"]').getAttribute("href")).toBe("/sales/po/order-1/dokumen/po/po");
  expect(container.textContent).not.toContain("Approval Design");
  act(() => root.unmount());
  expect(sessionStorage.getItem(SALES_PWA_PO_FILTER_KEY)).toContain("Packing");
  expect(sessionStorage.getItem(SALES_PWA_PO_SCROLL_KEY)).toBe("740");
});

test("opens the existing document sheet when one kind has several files", async () => {
  api.salesPwaDocuments.mockResolvedValue({
    ...documents,
    sj: [
      { id: "sj-1", number: "SJ-1", available: true },
      { id: "sj-2", number: "SJ-2", available: true },
    ],
  });
  const { container, root } = await renderDetail();
  await act(async () => {
    container.querySelector('[data-testid="sales-doc-open-sj"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  const links = Array.from(container.querySelectorAll('[data-testid="sales-doc-sheet"] a')).map((link) => link.getAttribute("href"));
  expect(links).toEqual(expect.arrayContaining([
    "/sales/po/order-1/dokumen/sj/sj-1",
    "/sales/po/order-1/dokumen/sj/sj-2",
  ]));
  act(() => root.unmount());
});

test("hides empty notes, ship date, buffer, and color", async () => {
  api.salesPwaDocuments.mockResolvedValue({
    po: { available: false },
    sj: [],
    invoices: [],
    bukti_kirim: [],
  });
  api.salesPwaOrder.mockResolvedValue({
    order: {
      ...order,
      notes: "  ",
      recipient: "",
      address: "",
      buffer_stock: false,
      tanggal_kirim: null,
      tanggal_kirim_overdue: false,
      documents: { po: 0, sj: 0, invoice: 0, bukti_kirim: 0, total: 0 },
      line_items: [{ idx: 0, name: "Cup", quantity: 2, unit: "pcs" }],
    },
  });
  const { container, root } = await renderDetail();
  expect(container.querySelector('[data-testid="sales-detail-notes"]')).toBeNull();
  expect(container.querySelector('[data-testid="sales-detail-customer"]')).toBeNull();
  const status = container.querySelector('[data-testid="sales-detail-status"]').textContent;
  expect(status).not.toContain("Kirim:");
  expect(status).not.toContain("Buffer Stock");
  expect(status).not.toContain("Kirim terlambat");
  expect(container.querySelector('[data-testid="sales-detail-items"]').textContent).not.toContain("Warna");
  expect(container.querySelector('[data-testid="sales-detail-docs"]').textContent).toContain("Belum ada dokumen tersedia");
  expect(container.querySelector('[data-testid="sales-doc-open-po"]')).toBeNull();
  act(() => root.unmount());
});
