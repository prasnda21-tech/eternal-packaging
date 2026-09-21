import fs from "fs";
import path from "path";

const pageSrc = fs.readFileSync(path.join(__dirname, "PenerimaanBarang.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

describe("Penerimaan Barang manual create", () => {
  test("Buat Penerimaan renders for purchase_receipts.receive only", () => {
    expect(pageSrc).toMatch(/const canCreate = hasPerm\("purchase_receipts\.receive"\)/);
    expect(pageSrc).toMatch(/view === "list" && canCreate &&/);
    expect(pageSrc).toMatch(/data-testid="btn-buat-penerimaan"/);
    expect(pageSrc).toMatch(/Penerimaan Langsung/);
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
    expect(pageSrc).toMatch(/data-testid="penerimaan-direct-stock-gap"/);
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
    // Manual create payload preserves line order; must not collapse by item_name.
    expect(pageSrc).not.toMatch(/reduce\(\([\s\S]{0,80}item_name/);
  });

  test("successful submit uses backend POST with loading guard", () => {
    expect(pageSrc).toMatch(/api\.createPurchaseReceipt/);
    expect(pageSrc).toMatch(/if \(busy \|\| !canCreate\) return;/);
    expect(pageSrc).toMatch(/disabled=\{busy\}/);
    expect(pageSrc).toMatch(/Menyimpan…/);
    expect(pageSrc).toMatch(/data-testid="save-penerimaan"/);
    expect(pageSrc).toMatch(/setOk\(`Penerimaan \$\{num\} tersimpan`\)/);
    expect(pageSrc).toMatch(/await loadHistory\(\)/);
  });

  test("error state and cancel", () => {
    expect(pageSrc).toMatch(/setFormErr\(errDetail\(ex\)\)/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-error"/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-form-cancel"/);
    expect(pageSrc).toMatch(/data-testid="btn-cancel-penerimaan"/);
    expect(pageSrc).toMatch(/Batal/);
    expect(pageSrc).toMatch(/Coba Lagi/);
  });

  test("existing sync, export, search, pagination, row detail unchanged", () => {
    expect(pageSrc).toMatch(/data-testid="penerimaan-sync-button"/);
    expect(pageSrc).toMatch(/Sinkronkan dari Accurate/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-export-button"/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-search"/);
    expect(pageSrc).toMatch(/useDebouncedLiveSearch/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-page-size"/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-pager"/);
    expect(pageSrc).toMatch(/purchaseRowInteractionProps/);
    expect(pageSrc).toMatch(/data-testid=\{`penerimaan-detail-\$\{it\.accurate_receipt_id\}`\}/);
    expect(pageSrc).toMatch(/po-linked-receipts/);
    expect(pageSrc).toMatch(/penerimaan-po-detail/);
    expect(pageSrc).toMatch(/save-po-receipt/);
    expect(pageSrc).not.toMatch(/PurchaseRowMenu/);
  });

  test("toolbar order places Penerimaan Manual before Accurate sync and export", () => {
    const buat = pageSrc.indexOf("btn-buat-penerimaan");
    const sync = pageSrc.indexOf("penerimaan-sync-button");
    const exp = pageSrc.indexOf("penerimaan-export-button");
    expect(buat).toBeGreaterThan(-1);
    expect(sync).toBeGreaterThan(buat);
    expect(exp).toBeGreaterThan(sync);
  });

  test("Accurate sync is secondary (Lainnya menu), receive PO is primary tab", () => {
    expect(pageSrc).toMatch(/penerimaan-more-button/);
    expect(pageSrc).toMatch(/tab-penerimaan-po/);
    expect(pageSrc).toMatch(/tab-riwayat-penerimaan/);
    expect(pageSrc).toMatch(/Simpan Penerimaan/);
  });

  test("manual rows do not fake Accurate last sync", () => {
    expect(pageSrc).toMatch(/isManual \? "Sumber: Manual" : "Sumber: Accurate"/);
    expect(pageSrc).toMatch(/Sumber: Manual/);
  });

  test("api exposes createPurchaseReceipt POST /purchase-receipts", () => {
    expect(apiSrc).toMatch(/createPurchaseReceipt: \(payload\) => client\.post\(`\/purchase-receipts`/);
    expect(apiSrc).toMatch(/purchaseReceiptsSync: \(\) => client\.post\(`\/purchase-receipts\/sync`/);
    expect(apiSrc).toMatch(/purchaseReceiptsExport:/);
  });

  test("history table headers match 8 body columns and full-width layout", () => {
    expect(pageSrc).toMatch(/purchase-table-history/);
    expect(pageSrc).toMatch(/>No Penerimaan</);
    expect(pageSrc).toMatch(/>Supplier \/ PO</);
    expect(pageSrc).toMatch(/>Total Qty</);
    expect(pageSrc).not.toMatch(/>Jumlah Item</);
    expect(pageSrc).toMatch(/colSpan=\{6\}/);
    expect(pageSrc).toMatch(/is-toolbar/);
    expect(pageSrc).not.toMatch(/po-receipts-table/);
  });

  test("PO relation and direct receipt stock gap messaging", () => {
    expect(pageSrc).toMatch(/data-testid="tab-penerimaan-po"/);
    expect(pageSrc).toMatch(/purchase_order_line_id/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-manual-stock-hint"/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-direct-stock-gap"/);
    expect(pageSrc).toMatch(/Catat penerimaan barang tanpa mengacu pada PO/);
    expect(pageSrc).toMatch(/remaining_receivable_qty/);
    expect(pageSrc).toMatch(/cumulative_received_qty/);
    expect(pageSrc).toMatch(/Terima Sekarang/);
    expect(pageSrc).not.toMatch(/Qty Diterima Sekarang/);
  });

  test("safe cancel does not require a reason and uses reversal messaging", () => {
    expect(pageSrc).toMatch(/btn-batalkan-receipt-/);
    expect(pageSrc).toMatch(/>\s*Batalkan\s*</);
    expect(pageSrc).not.toMatch(/Alasan pembatalan wajib diisi/);
    expect(pageSrc).not.toMatch(/requireReason/);
    expect(pageSrc).toMatch(/api\.cancelPurchaseGoodsReceipt\(cancelTarget\.id, ""\)/);
    expect(pageSrc).toMatch(/menangani reversal secara otomatis/);
    expect(pageSrc).not.toMatch(/purchase-cancel-reason/);
    expect(apiSrc).toMatch(/cancelPurchaseGoodsReceipt: \(id, reason = ""\)/);
  });

  test("PO detail shows cancel on the receipt, not the parent PO", () => {
    expect(pageSrc).toMatch(/include_cancelled: true/);
    expect(pageSrc).toMatch(/data-testid="penerimaan-po-receipts"/);
    expect(pageSrc).toMatch(/btn-batalkan-po-receipt-/);
    expect(pageSrc).toMatch(/Batalkan Penerimaan/);
    expect(pageSrc).toMatch(/openCancelReceipt\(receipt\)/);
    expect(pageSrc).toMatch(/cancelled \? "DIBATALKAN" : "ACTIVE"/);
    expect(pageSrc).toMatch(/canReceivePo && !cancelled/);
    expect(pageSrc).not.toMatch(/cancelPurchaseOrder\(poDetail/);
  });

  test("PO detail refreshes receipt cancel eligibility before rendering action", () => {
    expect(pageSrc).toMatch(/api\.listPurchaseGoodsReceipts\(\{\s*purchase_order_id: poId,\s*include_cancelled: true/);
    expect(pageSrc).toMatch(/setPoReceipts\(\(current\)/);
    expect(pageSrc).toMatch(/disabled=\{receipt\.can_cancel === false \|\| receipt\.accounting_status === "posted"\}/);
  });

  test("history exposes cancel for ERP manual receipts but never Accurate history", () => {
    expect(pageSrc).toMatch(/canCancel=\{canReceivePo && row\.kind === "manual"\}/);
    expect(pageSrc).toMatch(/btn-batalkan-manual-receipt-/);
    expect(pageSrc).toMatch(/api\.cancelManualPurchaseReceipt/);
    expect(pageSrc).toMatch(/isManual && canCancel && !cancelled/);
    expect(pageSrc).toMatch(/cancelled \? "DIBATALKAN"/);
    expect(apiSrc).toMatch(/cancelManualPurchaseReceipt: \(id, reason\)/);
  });
});
