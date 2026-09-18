import fs from "fs";
import path from "path";

const read = (name) => fs.readFileSync(path.join(__dirname, name), "utf8");

describe("compact sales workspaces", () => {
  const so = read("PesananPenjualan.jsx");
  const sj = read("PengirimanPesanan.jsx");
  const inv = read("FakturPenjualan.jsx");
  const rec = read("PenerimaanPenjualan.jsx");

  test("all four menus use the compact workspace shell", () => {
    expect(so).toMatch(/sales-compact-workspace/);
    expect(sj).toMatch(/sales-compact-workspace/);
    expect(inv).toMatch(/sales-compact-workspace/);
    expect(rec).toMatch(/sales-compact-workspace/);
    expect(so).toMatch(/salesWorkspacePages\.css/);
  });

  test("client pagination exists and resets on search/filter", () => {
    for (const src of [so, sj, inv, rec]) {
      expect(src).toMatch(/paginateRows/);
      expect(src).toMatch(/setListPage\(1\)/);
      expect(src).toMatch(/SalesPaginationBar/);
    }
  });

  test("row click opens drawer/detail without checkbox or menu", () => {
    expect(so).toMatch(/openOrderDrawer/);
    expect(so).toMatch(/isInteractiveTarget/);
    expect(so).toMatch(/SalesWorkspaceDrawer/);
    expect(sj).toMatch(/delivery-detail-modal/);
    expect(sj).toMatch(/isInteractiveTarget/);
    expect(sj).toMatch(/SalesRowMenu/);
    expect(so).toMatch(/btn-close-drawer|onClose=\{closeOrderDrawer\}/);
  });

  test("pesanan create form keeps draft across steps", () => {
    expect(so).toMatch(/createStep/);
    expect(so).toMatch(/Informasi Pesanan/);
    expect(so).toMatch(/Barang dan Desain/);
    expect(so).toMatch(/Review dan Simpan/);
    expect(so).toMatch(/setCreateStep\(\(s\) => s \+ 1\)/);
    expect(so).toMatch(/formData/);
  });

  test("pengiriman form keeps qty allocation across steps", () => {
    expect(sj).toMatch(/createStep/);
    expect(sj).toMatch(/Alokasi Qty dan Review/);
    expect(sj).toMatch(/line_qtys/);
    expect(sj).toMatch(/setCreateStep\(2\)/);
  });

  test("invoice totals still use createCalculations", () => {
    expect(inv).toMatch(/createCalculations/);
    expect(inv).toMatch(/grandTotal/);
    expect(inv).toMatch(/invoice-discount-input/);
    expect(inv).toMatch(/invoice-ppn-input/);
  });

  test("receipt validation strings and proof upload stay intact", () => {
    expect(rec).toMatch(/melebihi sisa tagihan/);
    expect(rec).toMatch(/validateReceiptProofFile/);
    expect(rec).toMatch(/buildReceiptProofPayload/);
    expect(rec).toMatch(/overflowY:\s*"auto"/);
    expect(rec).toMatch(/minHeight:\s*0/);
    expect(rec).toMatch(/flex:\s*"0 0 auto"/);
  });

  test("RBAC gates remain on all actions", () => {
    expect(so).toMatch(/hasPerm\("sales_order.create"\)/);
    expect(so).toMatch(/hasPerm\("po.delete"\)/);
    expect(sj).toMatch(/hasPerm\("sales.delivery.create"\)/);
    expect(sj).toMatch(/hasPerm\("sales.delivery.print"\)/);
    expect(inv).toMatch(/hasPerm\("sales.invoice.create"\)/);
    expect(inv).toMatch(/hasPerm\("sales.invoice.delete"\)/);
    expect(rec).toMatch(/hasPerm\("sales.receipt.create"\)/);
    expect(rec).toMatch(/hasPerm\("sales.receipt.delete"\)/);
  });
});
