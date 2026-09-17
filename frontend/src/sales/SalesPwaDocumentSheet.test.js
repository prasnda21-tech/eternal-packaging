import { act } from "react";
import { createRoot } from "react-dom/client";
import SalesPwaDocumentSheet from "./SalesPwaDocumentSheet";

jest.mock("react-router-dom", () => ({\n  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,\n}), { virtual: true });\n\nglobal.IS_REACT_ACT_ENVIRONMENT = true;\n
describe("SalesPwaDocumentSheet", () => {
  test("uses internal /sales viewer routes and never opens raw API in a new tab", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const data = {
      po_number: "PO-001",
      customer: "Customer A",
      counts: { total: 4 },
      po: { available: true, filename: "po.pdf" },
      sj: [{ id: "sj-1", number: "SJ-001", available: true, date: "2026-09-17" }],
      invoices: [{ id: "inv-1", number: "INV-001", available: true }],
      bukti_kirim: [{ id: "proof-1", storage_path: "proof.jpg" }],
    };

    act(() => {
      root.render(
        <MemoryRouter>
          <SalesPwaDocumentSheet orderId="order-1" data={data} onClose={() => {}} />
        </MemoryRouter>
      );
    });

    const links = Array.from(container.querySelectorAll("a"));
    expect(links).toHaveLength(4);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/sales/po/order-1/dokumen/po/po",
      "/sales/po/order-1/dokumen/sj/sj-1",
      "/sales/po/order-1/dokumen/invoice/inv-1",
      "/sales/po/order-1/dokumen/proof/proof-1",
    ]);
    links.forEach((link) => {
      expect(link.getAttribute("target")).toBeNull();
      expect(link.getAttribute("href")).not.toContain("/api/");
    });

    act(() => root.unmount());
    container.remove();
  });
});
