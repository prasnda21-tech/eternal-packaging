import fs from "fs";
import path from "path";

const pageSrc = fs.readFileSync(path.join(__dirname, "PenerimaanBarang.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

describe("Penerimaan Barang manual create", () => {
  test("Buat Penerimaan renders for purchase_receipts.receive only", () => {
    expect(pageSrc).toMatch(/const canCreate = hasPerm\("purchase_receipts\.receive"\)/);
    expect(pageSrc).toMatch(/view === "list" && canCreate &&/);
    expect(pageSrc).toMatch(/data-testid="btn-buat-penerimaan"/);
    expect(pageSrc).toMatch(/Buat Penerimaan/);
    expect(pageSrc).not.toMatch(/const canCreate = hasPerm\("purchase_receipts\.receive"\) \|\| hasPerm\("purchase_receipts\.sync"\)/);
  });

  test("view-only and sync-only users do not get Buat Penerimaan", () => {
    expect(pageSrc).toMatch(/canCreate && \(/);
    expect(pageSrc).not.toMatch(/hasPerm\("purchase_receipts\.view"\) && \([\s\S]*btn-buat-penerimaan/);
    expect(pageSrc).toMatch(/if \(!canCreate\) return;/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-sync-button"/);
    expect(pageSrc).toMatch(/hasPerm\("purchase_receipts\.sync"\)/);
  });

  test("button opens in-page create form like Faktur Pembelian", () => {
    expect(pageSrc).toMatch(/setView\("create"\)/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-create-form"/);
    expect(pageSrc).toMatch(/view === "create" && canCreate/);
  });

  test("required field validation for supplier, item, qty", () => {
    expect(pageSrc).toMatch(/validateCreate/);
    expect(pageSrc).toMatch(/Supplier wajib diisi/);
    expect(pageSrc).toMatch(/Tanggal penerimaan wajib diisi/);
    expect(pageSrc).toMatch(/Minimal satu baris/);
    expect(pageSrc).toMatch(/harus lebih besar dari 0/);
    expect(pageSrc).toMatch(/Number\.isFinite/);
  });

  test("supplier selection uses existing purchase supplier field", () => {
    expect(pageSrc).toMatch(/data-testid="penerimaan-supplier"/);
    expect(pageSrc).toMatch(/readOnly=\{Boolean\(recvPoId\)\}/);
  });

  test("item add and qty inputs with stable line identity", () => {
    expect(pageSrc).toMatch(/MasterItemSearchSelect/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-add-line"/);
    expect(pageSrc).toMatch(/\+ Tambah Item/);
    expect(pageSrc).toMatch(/emptyLine\(/);
    expect(pageSrc).toMatch(/data-testid=\{`penerimaan-line-qty-\$\{i\}`\}/);
    expect(pageSrc).toMatch(/key=\{l\.key\}/);
  });

  test("multiple lines preserved and duplicate names not merged", () => {
    expect(pageSrc).toMatch(/setLines\(\(p\) => \[\.\.\.p, emptyLine\(\)\]\)/);
    expect(pageSrc).toMatch(/lines\.map\(\(l, idx\) =>/);
    expect(pageSrc).not.toMatch(/reduce\(\([\s\S]*item_name/);
  });

  test("successful submit uses backend POST with loading guard", () => {
    expect(pageSrc).toMatch(/api\.createPurchaseReceipt/);
    expect(pageSrc).toMatch(/if \(busy \|\| !canCreate\) return;/);
    expect(pageSrc).toMatch(/disabled=\{busy\}/);
    expect(pageSrc).toMatch(/Menyimpan…/);
    expect(pageSrc).toMatch(/data-testid="save-penerimaan"/);
    expect(pageSrc).toMatch(/setOk\(`Penerimaan \$\{num\} tersimpan`\)/);
    expect(pageSrc).toMatch(/await load\(\)/);
  });

  test("error state and cancel", () => {
    expect(pageSrc).toMatch(/setFormErr\(errDetail\(ex\)\)/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-error"/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-form-cancel"/);
    expect(pageSrc).toMatch(/data-testid="btn-cancel-penerimaan"/);
    expect(pageSrc).toMatch(/Batal/);
  });

  test("existing sync, export, search, pagination, row detail unchanged", () => {
    expect(pageSrc).toMatch(/data-testid="penerimaan-sync-button"/);
    expect(pageSrc).toMatch(/Sinkronkan dari Accurate/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-export-button"/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-search"/);
    expect(pageSrc).toMatch(/useDebouncedLiveSearch/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-page-size"/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-pager"/);
    expect(pageSrc).toMatch(/data-testid=\{`penerimaan-toggle-\$\{it\.accurate_receipt_id\}`\}/);
    expect(pageSrc).toMatch(/data-testid=\{`penerimaan-detail-\$\{it\.accurate_receipt_id\}`\}/);
    expect(pageSrc).toMatch(/po-linked-receipts/);
    expect(pageSrc).toMatch(/save-po-receipt/);
  });

  test("toolbar order places Buat Penerimaan before Accurate sync and export", () => {
    const buat = pageSrc.indexOf("btn-buat-penerimaan");
    const sync = pageSrc.indexOf("penerimaan-sync-button");
    const exp = pageSrc.indexOf("penerimaan-export-button");
    expect(buat).toBeGreaterThan(-1);
    expect(sync).toBeGreaterThan(buat);
    expect(exp).toBeGreaterThan(sync);
  });

  test("manual rows do not fake Accurate last sync", () => {
    expect(pageSrc).toMatch(/isManual \? "—" :/);
    expect(pageSrc).toMatch(/Sumber: Manual/);
  });

  test("api exposes createPurchaseReceipt POST /purchase-receipts", () => {
    expect(apiSrc).toMatch(/createPurchaseReceipt: \(payload\) => client\.post\(`\/purchase-receipts`/);
    expect(apiSrc).toMatch(/purchaseReceiptsSync: \(\) => client\.post\(`\/purchase-receipts\/sync`/);
    expect(apiSrc).toMatch(/purchaseReceiptsExport:/);
  });

  test("PO relation reuses existing preview line identity when selected", () => {
    expect(pageSrc).toMatch(/penerimaan-po-selector/);
    expect(pageSrc).toMatch(/purchase_order_line_id/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-manual-stock-hint"/);
    expect(pageSrc).toMatch(/tidak mengubah stok/);
    expect(pageSrc).toMatch(/remaining_receivable_qty/);
    expect(pageSrc).toMatch(/cumulative_received_qty/);
  });
});
