import fs from "fs";
import path from "path";

const read = (rel) => fs.readFileSync(path.join(__dirname, rel), "utf8");

describe("Sales document clickable rows + form open", () => {
  const helper = read("../lib/clickableListRow.js");
  const pesanan = read("PesananPenjualan.jsx");
  const faktur = read("FakturPenjualan.jsx");
  const penerimaan = read("PenerimaanPenjualan.jsx");
  const pengiriman = read("PengirimanPesanan.jsx");
  const densityCss = read("salesSubmenuDensity.css");
  const pesananCss = read("pesananPenjualan.css");

  test("shared helper blocks nested interactive navigation", () => {
    expect(helper).toMatch(/ROW_INTERACTIVE_SELECTOR/);
    expect(helper).toMatch(/isRowInteractiveTarget/);
    expect(helper).toMatch(/clickableDocumentRowProps/);
    expect(helper).toMatch(/data-no-row-nav/);
    expect(helper).toMatch(/role:\s*"link"/);
    expect(helper).toMatch(/tabIndex:\s*0/);
    expect(helper).toMatch(/Enter/);
  });

  test("Pesanan Penjualan row opens PO detail once; checkbox/actions excluded", () => {
    expect(pesanan).toMatch(/clickableDocumentRowProps/);
    expect(pesanan).toMatch(/openOrder\(o\)/);
    expect(pesanan).toMatch(/navigate\(`\/po\/\$\{order\.id\}`/);
    expect(pesanan).toMatch(/so-list-check[\s\S]{0,80}stopPropagation/);
    expect(pesanan).toMatch(/onClick=\{\(e\) => e\.stopPropagation\(\)\}/);
    expect(pesanan).toMatch(/btn-view-po-/);
    expect(pesanan).toMatch(/order-checkbox-/);
    expect(pesananCss).toMatch(/so-list-row:focus-visible/);
  });

  test("Faktur Penjualan row opens existing detail form; PO link and actions excluded", () => {
    expect(faktur).toMatch(/clickableDocumentRowProps/);
    expect(faktur).toMatch(/handleOpenDetail\(inv\.id\)/);
    expect(faktur).toMatch(/api\.getSalesInvoice/);
    expect(faktur).toMatch(/setViewMode\("detail"\)/);
    expect(faktur).toMatch(/data-no-row-nav/);
    expect(faktur).toMatch(/invoice-checkbox-/);
    expect(faktur).toMatch(/view-invoice-btn-/);
    expect(faktur).toMatch(/btn-delete-invoice-/);
    expect(faktur).toMatch(/Link to=\{`\/po\/\$\{inv\.po_id\}`\}/);
    expect(faktur).toMatch(/canEditFinancial/);
    expect(faktur).toMatch(/Simpan Perubahan/);
    expect(faktur).toMatch(/financialLocked/);
  });

  test("Penerimaan Penjualan row opens populated detail; Package C Edit is capability-gated", () => {
    expect(penerimaan).toMatch(/clickableDocumentRowProps/);
    expect(penerimaan).toMatch(/openReceiptDetail/);
    expect(penerimaan).toMatch(/api\.getSalesReceipt/);
    expect(penerimaan).toMatch(/setDetailReceipt/);
    expect(penerimaan).toMatch(/receipt-checkbox-/);
    expect(penerimaan).toMatch(/receipt-number-/);
    expect(penerimaan).toMatch(/btn-post-accounting-/);
    expect(penerimaan).toMatch(/btn-delete-receipt-/);
    expect(penerimaan).toMatch(/updateSalesReceipt\(/);
    expect(penerimaan).toMatch(/edit_capability/);
    expect(penerimaan).toMatch(/canBusinessEditReceipt/);
    expect(penerimaan).not.toMatch(/Simpan Perubahan Penerimaan/);
  });

  test("Pengiriman Pesanan row opens existing delivery detail; PO link and actions excluded", () => {
    expect(pengiriman).toMatch(/clickableDocumentRowProps/);
    expect(pengiriman).toMatch(/openDeliveryDetail/);
    expect(pengiriman).toMatch(/loadDeliveryDetail/);
    expect(pengiriman).toMatch(/api\.getSalesDelivery/);
    expect(pengiriman).toMatch(/delivery-detail-modal/);
    expect(pengiriman).toMatch(/delivery-checkbox-/);
    expect(pengiriman).toMatch(/btn-view-delivery-/);
    expect(pengiriman).toMatch(/btn-delete-delivery-/);
    expect(pengiriman).toMatch(/po-link-/);
    expect(pengiriman).not.toMatch(/updateSalesDelivery\(/);
  });

  test("compact density and focus styles preserved", () => {
    expect(densityCss).toMatch(/sd-clickable-row/);
    expect(densityCss).toMatch(/focus-visible/);
    expect(densityCss).toMatch(/height:60px/);
    expect(pesananCss).toMatch(/height: 60px/);
  });
});
