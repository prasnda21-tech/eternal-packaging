import { buildSalesProcessNavLinks } from "./salesProcessNav";

function hasOf(...keys) {
  const set = new Set(keys);
  return (k) => set.has(k);
}

describe("buildSalesProcessNavLinks", () => {
  test("returns empty without hasPerm", () => {
    expect(buildSalesProcessNavLinks({})).toEqual([]);
  });

  test("omits modules without permission", () => {
    const links = buildSalesProcessNavLinks({ hasPerm: () => false });
    expect(links).toEqual([]);
  });

  test("builds module links for sales + schedule + status po", () => {
    const hasPerm = hasOf(
      "nav.sales",
      "nav.sales.sales_order",
      "nav.sales.delivery_order",
      "nav.sales.sales_invoice",
      "nav.sales.sales_receipt",
      "nav.reports",
      "nav.reports.status_po",
      "nav.production_schedule",
    );
    const links = buildSalesProcessNavLinks({ hasPerm, poNumber: "PO-1" });
    const ids = links.map((l) => l.id);
    expect(ids).toEqual([
      "pesanan",
      "produksi",
      "pengiriman",
      "faktur",
      "penerimaan",
      "status-po",
    ]);
    expect(links.find((l) => l.id === "pengiriman").to).toBe("/penjualan/pengiriman-pesanan");
    expect(links.find((l) => l.id === "pengiriman").to).not.toMatch(/po_id/);
    expect(links.find((l) => l.id === "faktur").to).toBe("/penjualan/faktur-penjualan");
    expect(links.find((l) => l.id === "status-po").to).toBe("/laporan/status-po?po=PO-1");
    expect(links.find((l) => l.id === "status-po").kind).toBe("related");
  });

  test("uses proven invoice_id for related faktur/penerimaan only", () => {
    const hasPerm = hasOf(
      "nav.sales",
      "nav.sales.sales_invoice",
      "nav.sales.sales_receipt",
    );
    const links = buildSalesProcessNavLinks({
      hasPerm,
      invoiceId: "inv-9",
    });
    expect(links.find((l) => l.id === "faktur")).toEqual({
      id: "faktur",
      to: "/penjualan/faktur-penjualan?invoice_id=inv-9",
      label: "Faktur terkait",
      kind: "related",
    });
    expect(links.find((l) => l.id === "penerimaan").to).toBe(
      "/penjualan/penerimaan-penjualan",
    );
  });

  test("never invents po_id create deep-links", () => {
    const hasPerm = hasOf("nav.sales", "nav.sales.delivery_order", "nav.sales.sales_invoice");
    const links = buildSalesProcessNavLinks({
      hasPerm,
      orderId: "ord-1",
      poNumber: "PO-99",
    });
    for (const link of links) {
      expect(link.to).not.toMatch(/action=create/);
      expect(link.to).not.toMatch(/po_id=/);
    }
  });
});
