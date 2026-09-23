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
  expect(container.textContent).not.toMatch(/Sudah ditandatangani/);

  act(() => root.unmount());
});

test("signed delivery note shows status once and hides storage paths", () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const secret = "eternal-packaging/surat-jalan/secret-signed.pdf";
  const data = {
    po_number: "PO-1",
    customer: "A",
    counts: { total: 3 },
    po: { available: false },
    sj: [
      {
        id: "sj-unsigned",
        number: "SJ-OPEN",
        available: true,
        has_signed: false,
        date: "2026-09-20",
        storage_path: "eternal-packaging/surat-jalan/secret-original.pdf",
        signed_storage_path: secret,
      },
      {
        id: "sj-signed",
        number: "SJ-SIGNED",
        available: true,
        has_signed: true,
        date: "2026-09-21",
        recipient_name: "Penerima",
        signed_at: "2026-09-22T10:00:00+00:00",
        storage_path: "eternal-packaging/surat-jalan/secret-original.pdf",
        signed_storage_path: secret,
      },
      {
        id: "sj-missing",
        number: "SJ-NONE",
        available: false,
        has_signed: false,
        date: "2026-09-19",
        storage_path: secret,
      },
    ],
    invoices: [],
    bukti_kirim: [],
  };

  act(() => root.render(
    <SalesPwaDocumentSheet orderId="order-1" data={data} onClose={() => {}} />
  ));

  const links = Array.from(container.querySelectorAll("a")).map((link) => link.getAttribute("href"));
  expect(links.filter((href) => href.includes("/dokumen/sj/"))).toEqual([
    "/sales/po/order-1/dokumen/sj/sj-unsigned",
    "/sales/po/order-1/dokumen/sj/sj-signed",
  ]);
  expect(container.textContent).toMatch(/Sudah ditandatangani/);
  expect(container.textContent).toMatch(/Penerima/);
  expect(container.textContent).toMatch(/22\/09\/26/);
  const signedRow = container.textContent.split("SJ-SIGNED")[1] || "";
  expect(signedRow).toMatch(/Sudah ditandatangani/);
  expect(container.textContent.split("Sudah ditandatangani")).toHaveLength(2);
  expect(container.textContent).toMatch(/SJ-NONE/);
  expect(container.textContent).toMatch(/Belum ada/);
  expect(container.textContent).not.toMatch(/eternal-packaging/);
  expect(container.textContent).not.toMatch(/storage_path/);
  expect(container.textContent).not.toMatch(/signed_storage_path/);

  act(() => root.unmount());
});
