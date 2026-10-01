import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaCustomersPage from "./SalesPwaCustomersPage";

jest.mock("react-router-dom", () => ({
  Link: ({ children, to, ...props }) => <a href={typeof to === "string" ? to : ""} {...props}>{children}</a>,
}), { virtual: true });
jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { salesPwaCustomers: jest.fn() },
}));
global.IS_REACT_ACT_ENVIRONMENT = true;

test("shows visit history only when the customer is linked by id", async () => {
  api.salesPwaCustomers.mockResolvedValue({
    customers: [
      {
        customer_key: "CUST-AMAN",
        prediction_key: "id:CUST-AMAN",
        customer: "Pelanggan Aman",
        outlet: "Outlet Beda Nama",
        po_count: 1,
        last_order_date: "2026-09-01",
        visit_link: "linked",
        visit_history: [{
          id: "v1",
          outlet: "Outlet Beda Nama",
          activity_at: "2026-09-10T09:00:00+07:00",
          result: "Hasil aman",
          next_action: "Kirim harga",
          notes: "Catatan aman",
          next_follow_up_at: "2026-09-19T17:30:00+00:00",
        }],
      },
      {
        customer_key: "name:pelanggan nama",
        prediction_key: "name:pelanggan nama",
        customer: "Pelanggan Nama",
        po_count: 1,
        last_order_date: "2026-09-02",
        visit_link: "unlinked",
        visit_history: [],
      },
    ],
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaCustomersPage />); await Promise.resolve(); });
  const linked = node.querySelector('[data-testid="sales-customer-visits-CUST-AMAN"]');
  expect(linked.textContent).toContain("Hasil aman");
  expect(linked.textContent).toContain("Kirim harga");
  expect(linked.textContent).toContain("Catatan aman");
  expect(linked.textContent).toContain("Follow-up: 20/09/26 00:30");
  expect(linked.textContent).not.toContain("19/09/26");
  expect(node.querySelectorAll("[data-testid^='sales-customer-visits-']")).toHaveLength(1);
  expect(node.textContent).not.toContain("Jangan ikut nama");
  const links = Array.from(node.querySelectorAll("a"));
  expect(links.find((link) => link.textContent.includes("Pelanggan Aman")).getAttribute("href")).toBe("/sales/prediksi/id%3ACUST-AMAN");
  expect(links.find((link) => link.textContent.includes("Pelanggan Nama")).getAttribute("href")).toBe(`/sales/prediksi/${encodeURIComponent("name:pelanggan nama")}`);
  act(() => root.unmount());
});

test("does not invent a prediction link when the backend has no prediction_key", async () => {
  api.salesPwaCustomers.mockResolvedValue({
    customers: [
      {
        customer_key: "name:tanpa kunci",
        prediction_key: "",
        customer: "Tanpa Kunci",
        po_count: 1,
        last_order_date: "2026-09-03",
        visit_link: "unlinked",
      },
      {
        customer_key: "name:toko kembar",
        prediction_key: "ambiguous:toko kembar",
        customer: "Toko Kembar",
        po_count: 1,
        last_order_date: "2026-09-04",
        visit_link: "unlinked",
      },
    ],
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaCustomersPage />); await Promise.resolve(); });
  const links = Array.from(node.querySelectorAll("a"));
  expect(links.find((link) => link.textContent.includes("Tanpa Kunci"))).toBeUndefined();
  expect(node.querySelector('[data-testid="sales-customer-name:tanpa kunci"]').tagName).toBe("DIV");
  expect(links.find((link) => link.textContent.includes("Toko Kembar")).getAttribute("href")).toBe(`/sales/prediksi/${encodeURIComponent("ambiguous:toko kembar")}`);
  act(() => root.unmount());
});
