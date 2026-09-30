import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaInvoiceDetailPage from "./SalesPwaInvoiceDetailPage";
import SalesPwaInvoicesPage from "./SalesPwaInvoicesPage";

const mockRouter = { search: "", params: { id: "inv-1" }, query: "month=2026-10" };

jest.mock("react-router-dom", () => ({
  useLocation: () => ({ pathname: "/sales/invoices", search: mockRouter.search, hash: "", key: "test", state: null }),
  useParams: () => mockRouter.params,
  useSearchParams: () => [new URLSearchParams(mockRouter.query)],
  Link: ({ children, to, ...props }) => <a href={typeof to === "string" ? to : ""} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesPwaInvoices: jest.fn(),
    salesPwaReceivables: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const invoice = {
  id: "inv-1",
  invoice_number: "INV-001",
  customer_name: "Kopi Ayub",
  invoice_date: "2026-10-05",
  grand_total: 1000000,
  payment_status: "DP",
};

async function renderPage(element) {
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(element); });
  return { node, root };
}

async function flush() {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

beforeEach(() => {
  mockRouter.search = "";
  mockRouter.params = { id: "inv-1" };
  mockRouter.query = "month=2026-10";
  api.salesPwaInvoices.mockReset();
  api.salesPwaReceivables.mockReset();
});

test("renders finalized invoices for the selected month", async () => {
  mockRouter.search = "?month=2026-10";
  api.salesPwaInvoices.mockResolvedValue({
    month: "2026-10",
    invoices: [
      invoice,
      {
        id: "inv-2",
        invoice_number: "INV-002",
        customer_name: "Kedai Tuman",
        invoice_date: "2026-10-20",
        grand_total: 120000,
        payment_status: "LUNAS",
      },
    ],
    summary: { total_amount: 1120000, invoice_count: 2 },
  });
  api.salesPwaReceivables.mockResolvedValue({
    receivables: [{ id: "inv-1", outstanding_amount: 750000, paid_amount: 250000, due_date: "2026-11-04" }],
    summary: { outstanding_total: 750000, invoice_count: 1 },
  });
  const { node, root } = await renderPage(<SalesPwaInvoicesPage />);
  await flush();
  expect(api.salesPwaInvoices).toHaveBeenCalledWith({ month: "2026-10" });
  expect(node.textContent).toContain("INV-001");
  expect(node.textContent).toContain("Kopi Ayub");
  expect(node.textContent).toContain("05/10/26");
  expect(node.textContent).toContain("Rp1.000.000");
  expect(node.textContent).toContain("DP");
  expect(node.textContent).toContain("Rp750.000");
  expect(node.querySelector("[data-testid='sales-invoice-inv-1']").getAttribute("href")).toBe("/sales/invoices/inv-1?month=2026-10");
  expect(node.innerHTML).not.toContain("/penjualan/faktur-penjualan");
  expect(node.innerHTML).not.toContain("/buku-besar/piutang-usaha");
  act(() => root.unmount());
});

test("shows an empty invoice month", async () => {
  api.salesPwaInvoices.mockResolvedValue({ invoices: [], summary: { total_amount: 0, invoice_count: 0 } });
  api.salesPwaReceivables.mockResolvedValue({ receivables: [], summary: { outstanding_total: 0, invoice_count: 0 } });
  const { node, root } = await renderPage(<SalesPwaInvoicesPage />);
  await flush();
  expect(node.textContent).toContain("Tidak ada invoice final pada bulan ini.");
  act(() => root.unmount());
});

test("shows the invoice loading state", async () => {
  api.salesPwaInvoices.mockReturnValue(new Promise(() => {}));
  api.salesPwaReceivables.mockReturnValue(new Promise(() => {}));
  const { node, root } = await renderPage(<SalesPwaInvoicesPage />);
  expect(node.textContent).toContain("Memuat invoice...");
  expect(node.textContent).not.toContain("Tidak ada invoice final");
  act(() => root.unmount());
});

test("shows the invoice error state", async () => {
  api.salesPwaInvoices.mockRejectedValue({ response: { data: { detail: "Invoice gagal dimuat" } } });
  api.salesPwaReceivables.mockResolvedValue({ receivables: [] });
  const { node, root } = await renderPage(<SalesPwaInvoicesPage />);
  await flush();
  expect(node.textContent).toContain("Invoice gagal dimuat");
  expect(node.textContent).not.toContain("Tidak ada invoice final");
  act(() => root.unmount());
});

test("renders invoice detail from the sales invoice and receivable payloads", async () => {
  api.salesPwaInvoices.mockResolvedValue({ invoices: [invoice], summary: { total_amount: 1000000, invoice_count: 1 } });
  api.salesPwaReceivables.mockResolvedValue({
    receivables: [{
      id: "inv-1",
      invoice_number: "INV-001",
      customer_name: "Kopi Ayub",
      invoice_date: "2026-10-05",
      due_date: "2026-11-04",
      grand_total: 1000000,
      paid_amount: 250000,
      outstanding_amount: 750000,
    }],
  });
  const { node, root } = await renderPage(<SalesPwaInvoiceDetailPage />);
  await flush();
  expect(node.textContent).toContain("Kopi Ayub");
  expect(node.textContent).toContain("INV-001");
  expect(node.textContent).toContain("04/11/26");
  expect(node.textContent).toContain("Rp250.000");
  expect(node.textContent).toContain("Rp750.000");
  expect(node.querySelector("[data-testid='sales-invoice-back']").getAttribute("href")).toBe("/sales/invoices?month=2026-10");
  act(() => root.unmount());
});
