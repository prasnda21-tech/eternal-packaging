import { act } from "react";
import { createRoot } from "react-dom/client";
import SalesPwaDocumentSheet from "./SalesPwaDocumentSheet";

jest.mock("react-router-dom", () => ({
  Link: ({ to, state, children, ...props }) => (
    <a href={to} data-route-state={JSON.stringify(state)} {...props}>{children}</a>
  ),
}), { virtual: true });

global.IS_REACT_ACT_ENVIRONMENT = true;

test("documents keep their internal route and exact PO return state", () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const data = {
    po_number: "PO-1",
    customer: "A",
    counts: { total: 4 },
    po: { available: true },
    sj: [{ id: "sj-1", available: true }],
    invoices: [{ id: "inv-1", available: true }],
    bukti_kirim: [{ id: "proof-1", storage_path: "x" }],
  };

  act(() => root.render(
    <SalesPwaDocumentSheet orderId="order-1" data={data} onClose={() => {}} />
  ));

  const links = Array.from(container.querySelectorAll("a"));
  expect(links.map((link) => link.getAttribute("href"))).toEqual([
    "/sales/po/order-1/dokumen/po/po",
    "/sales/po/order-1/dokumen/sj/sj-1",
    "/sales/po/order-1/dokumen/invoice/inv-1",
    "/sales/po/order-1/dokumen/proof/proof-1",
  ]);
  links.forEach((link) => {
    expect(JSON.parse(link.dataset.routeState)).toEqual({
      salesDocumentFrom: "/sales/po/order-1",
    });
    expect(link.getAttribute("target")).toBeNull();
  });

  act(() => root.unmount());
});
