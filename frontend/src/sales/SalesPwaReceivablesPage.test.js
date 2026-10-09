import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaReceivablesPage from "./SalesPwaReceivablesPage";

jest.mock("react-router-dom", () => ({
  Link: ({ children, to, ...props }) => <a href={typeof to === "string" ? to : ""} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { salesPwaReceivables: jest.fn() },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const row = {
  id: "inv-9",
  invoice_number: "INV-OLD",
  customer_name: "Toko Lama",
  invoice_date: "2026-08-15",
  due_date: "2026-09-14",
  grand_total: 400000,
  paid_amount: 0,
  outstanding_amount: 400000,
};

async function renderPage() {
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaReceivablesPage />); });
  return { node, root };
}

async function flush() {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

beforeEach(() => {
  api.salesPwaReceivables.mockReset();
});

test("renders outstanding invoices", async () => {
  api.salesPwaReceivables.mockResolvedValue({
    receivables: [
      row,
      {
        id: "inv-3",
        invoice_number: "INV-PART",
        customer_name: "Kopi Ayub",
        invoice_date: "2026-10-05",
        due_date: "2026-11-04",
        grand_total: 1000000,
        paid_amount: 250000,
        outstanding_amount: 750000,
      },
    ],
    summary: { outstanding_total: 1150000, invoice_count: 2 },
  });
  const { node, root } = await renderPage();
  await flush();
  expect(api.salesPwaReceivables).toHaveBeenCalledWith();
  const card = node.querySelector("[data-testid='sales-receivable-inv-9']").textContent;
  const at = (label) => card.indexOf(label);
  expect(at("Toko Lama")).toBeLessThan(at("Rp400.000"));
  expect(at("Rp400.000")).toBeLessThan(at("14/09/2026"));
  expect(at("14/09/2026")).toBeLessThan(at("INV-OLD"));
  expect(at("INV-OLD")).toBeLessThan(at("15/08/2026"));
  expect(at("15/08/2026")).toBeLessThan(at("Rp0"));
  expect(node.querySelector("[data-testid='sales-receivable-inv-9']").getAttribute("href")).toBe("/sales/invoices/inv-9?month=2026-08&from=receivables");
  expect(node.innerHTML).not.toContain("/buku-besar/piutang-usaha");
  expect(node.innerHTML).not.toContain("/penjualan/faktur-penjualan");
  act(() => root.unmount());
});

test("shows an empty receivable list", async () => {
  api.salesPwaReceivables.mockResolvedValue({ receivables: [], summary: { outstanding_total: 0, invoice_count: 0 } });
  const { node, root } = await renderPage();
  await flush();
  expect(node.textContent).toContain("Tidak ada invoice yang masih memiliki sisa piutang.");
  act(() => root.unmount());
});

test("shows the receivable loading state", async () => {
  api.salesPwaReceivables.mockReturnValue(new Promise(() => {}));
  const { node, root } = await renderPage();
  expect(node.textContent).toContain("Memuat piutang...");
  expect(node.textContent).not.toContain("Tidak ada invoice");
  act(() => root.unmount());
});

test("shows the receivable error state", async () => {
  api.salesPwaReceivables.mockRejectedValue({ response: { data: { detail: "Piutang gagal dimuat" } } });
  const { node, root } = await renderPage();
  await flush();
  expect(node.textContent).toContain("Piutang gagal dimuat");
  expect(node.textContent).not.toContain("Tidak ada invoice");
  act(() => root.unmount());
});
