import fs from "fs";
import path from "path";

describe("Faktur Penjualan UI & Navigation Suite", () => {
  const pageSrc = fs.readFileSync(path.join(__dirname, "FakturPenjualan.jsx"), "utf8");
  const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const shellSrc = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const poDetailSrc = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");

  test("FakturPenjualan page contains list, search, table and create elements", () => {
    expect(pageSrc).toMatch(/faktur-penjualan-page/);
    expect(pageSrc).toMatch(/search-invoice-input/);
    expect(pageSrc).toMatch(/refresh-invoice-btn/);
    expect(pageSrc).toMatch(/invoices-table/);
    expect(pageSrc).toMatch(/btn-create-invoice/);
  });

  test("FakturPenjualan create form contains PO snapshot preview, partial qty and financial inputs", () => {
    expect(pageSrc).toMatch(/po-selector/);
    expect(pageSrc).toMatch(/po-snapshot-card/);
    expect(pageSrc).toMatch(/snapshot-po-number/);
    expect(pageSrc).toMatch(/snapshot-customer/);
    expect(pageSrc).toMatch(/invoice-date-input/);
    expect(pageSrc).toMatch(/invoice-term-input/);
    expect(pageSrc).toMatch(/invoice-discount-input/);
    expect(pageSrc).toMatch(/invoice-ppn-input/);
    expect(pageSrc).toMatch(/qty-input/);
    expect(pageSrc).toMatch(/unit-price-input/);
    expect(pageSrc).toMatch(/price_from_order/);
    expect(pageSrc).toMatch(/Harga kosong tidak dijadikan Rp0/);
    expect(pageSrc).not.toMatch(/initialPrices\[l\.line_idx\] = l\.unit_price \|\| ""/);
    expect(pageSrc).toMatch(/save-invoice-btn/);
    expect(pageSrc).toMatch(/invoice-actions/);
    expect(pageSrc).toMatch(/ux90-fp-create/);
    expect(pageSrc).toMatch(/fp-po-gate/);
    expect((pageSrc.match(/invoice-actions/g) || []).length).toBeGreaterThanOrEqual(2);
  });

  test("PODetail -> Faktur Penjualan passes exact po_id; create keeps PO switcher after load", () => {
    expect(poDetailSrc).toMatch(/btn-buat-faktur-penjualan/);
    expect(poDetailSrc).toMatch(/\/penjualan\/faktur-penjualan\?po_id=\$\{order\.id\}&action=create/);
    expect(pageSrc).toMatch(/queryPoId/);
    expect(pageSrc).toMatch(/fp-po-switch/);
    expect(pageSrc).toMatch(/loadPoPreview\(selectedPoId\)/);
    expect(pageSrc).toMatch(/invoiceBelumDifakturMeta/);
    expect(pageSrc).not.toMatch(/delivery_summary\?\.total_remaining \?\? p\?\.total_quantity/);
  });

  test("Faktur PO lookup searches list_tab=all via server q and disables fully-invoiced/cancelled", () => {
    expect(pageSrc).toMatch(/list_tab:\s*"all"/);
    expect(pageSrc).toMatch(/fetchInvoicePoOptions/);
    expect(pageSrc).toMatch(/fetchOptions=\{fetchInvoicePoOptions\}/);
    expect(pageSrc).toMatch(/isOptionDisabled=\{isInvoicePoDisabled\}/);
    expect(pageSrc).toMatch(/PO sudah difaktur sepenuhnya/);
    expect(pageSrc).toMatch(/PO dibatalkan/);
    expect(pageSrc).toMatch(/PO tidak ditemukan/);
    expect(pageSrc).not.toMatch(/listSalesOrders\(\{\s*status:\s*"aktif"\s*\}\)/);
    expect(pageSrc).not.toMatch(/status:\s*"gabungan"/);
  });

  test("FakturPenjualan computes line total, discount, PPN, grand total, and remaining qty", () => {
    expect(pageSrc).toMatch(/createCalculations/);
    expect(pageSrc).toMatch(/detailCalculations/);
    expect(pageSrc).toMatch(/subtotal/);
    expect(pageSrc).toMatch(/discount/);
    expect(pageSrc).toMatch(/afterDiscount/);
    expect(pageSrc).toMatch(/ppn/);
    expect(pageSrc).toMatch(/grandTotal/);
    expect(pageSrc).toMatch(/totalRemaining/);
    expect(pageSrc).toMatch(/fully-invoiced-banner/);
  });

  test("invoice-breakdown restores DPP and PPN rows from existing createCalculations", () => {
    const breakdown = pageSrc.split('data-testid="invoice-breakdown"')[1] || "";
    expect(breakdown).toMatch(/>\s*Subtotal\s*</);
    expect(breakdown).toMatch(/>\s*Diskon\s*</);
    expect(breakdown).toMatch(/>\s*DPP\s*</);
    expect(breakdown).toMatch(/createCalculations\.afterDiscount/);
    expect(breakdown).toMatch(/PPN \(\{createCalculations\.ppnPercent\}%\)/);
    expect(breakdown).toMatch(/createCalculations\.ppn/);
    expect(breakdown).toMatch(/data-testid="invoice-dpp"/);
    expect(breakdown).toMatch(/data-testid="invoice-ppn"/);
    expect(breakdown).toMatch(/>\s*Biaya Lainnya\s*</);
    expect(breakdown).toMatch(/>\s*Total Faktur\s*</);
    expect(breakdown).toMatch(/data-testid="invoice-grand-total"/);
    // Display-only restore — formula site must stay afterDiscount + ppn + charges.
    expect(pageSrc).toMatch(/afterDiscount\s*\+\s*ppn\s*\+\s*chargeSummary\.total/);
  });

  test("navAccess includes Faktur Penjualan under Penjualan parent", () => {
    expect(navSrc).toMatch(/salesInvoice:\s*"nav\.sales\.sales_invoice"/);
    expect(navSrc).toMatch(/Faktur Penjualan/);
    expect(navSrc).toMatch(/\/penjualan\/faktur-penjualan/);
    expect(navSrc).toMatch(/top-nav-sales-invoice/);
  });

  test("AppShell includes FileText tile icon for salesInvoice", () => {
    expect(shellSrc).toMatch(/\[NAV_KEYS\.salesInvoice\]:\s*FileText/);
  });

  test("App.js wires up route for /penjualan/faktur-penjualan with PermGate", () => {
    expect(appSrc).toMatch(/const FakturPenjualan = lazy\(\(\) => import\("\.\/pages\/FakturPenjualan"\)\)/);
    expect(appSrc).toMatch(/path="\/penjualan\/faktur-penjualan"/);
    expect(appSrc).toMatch(/anyOf=\{\["sales\.invoice\.view"\]\}/);
  });

  test("Biaya Lainnya rows stay in draft state and post through the invoice API", () => {
    expect(pageSrc).toMatch(/Biaya Lainnya/);
    expect(pageSrc).toMatch(/idPrefix="create"/);
    expect(pageSrc).toMatch(/idPrefix="detail"/);
    expect(pageSrc).toMatch(/\$\{idPrefix\}-add-other-charge/);
    expect(pageSrc).toMatch(/\$\{idPrefix\}-remove-other-charge-/);
    expect(pageSrc).toMatch(/salesInvoiceChargeAccounts/);
    expect(pageSrc).toMatch(/other_charges: otherChargePayload\(otherCharges\)/);
    expect(pageSrc).toMatch(/other_charges: otherChargePayload\(detailCharges\)/);
    expect(pageSrc).toMatch(/setDetailCharges\(\(inv\.other_charges \|\| \[\]\)\.map\(otherChargeFromStored\)\)/);
    expect(pageSrc).toMatch(/chargesIncomplete/);
    expect(pageSrc).not.toMatch(/700031/);
  });

  test("api.js exposes complete sales invoice methods", () => {
    expect(apiSrc).toMatch(/listSalesInvoices:/);
    expect(apiSrc).toMatch(/getSalesInvoice:/);
    expect(apiSrc).toMatch(/getPoInvoicePreview:/);
    expect(apiSrc).toMatch(/salesInvoiceChargeAccounts:/);
    expect(apiSrc).toMatch(/createSalesInvoice:/);
    expect(apiSrc).toMatch(/updateSalesInvoice:/);
    expect(apiSrc).toMatch(/finalizeSalesInvoice:/);
    expect(apiSrc).toMatch(/cancelSalesInvoice:/);
    expect(apiSrc).toMatch(/businessCancelSalesInvoice:/);
    expect(apiSrc).toMatch(/recoverAutoSalesInvoiceAccounting:/);
    expect(apiSrc).toMatch(/postSalesInvoiceAccounting:/);
    expect(apiSrc).toMatch(/reverseSalesInvoiceAccounting:/);
    expect(apiSrc).toMatch(/deleteSalesInvoice:/);
    expect(apiSrc).toMatch(/bulkDeleteSalesInvoices:/);
  });

  test("Faktur list has checkbox, visible-only select all, Hapus Terpilih, and business-number confirm", () => {
    expect(pageSrc).toMatch(/select-all-invoices/);
    expect(pageSrc).toMatch(/invoice-checkbox-/);
    expect(pageSrc).toMatch(/btn-hapus-terpilih/);
    expect(pageSrc).toMatch(/Hapus Terpilih/);
    expect(pageSrc).toMatch(/delete-confirm-modal/);
    expect(pageSrc).toMatch(/delete-confirm-numbers/);
    expect(pageSrc).toMatch(/invoice_number/);
    expect(pageSrc).toMatch(/pageDeletableIds\.every\(\(id\) => selectedIds\.has\(id\)\)/);
    expect(pageSrc).toMatch(/tab-faktur-aktif/);
    expect(pageSrc).toMatch(/tab-faktur-dibatalkan/);
    expect(pageSrc).toMatch(/reset-invoice-filters/);
    expect(pageSrc).toMatch(/invoiceDeletable/);
  });

  test("Faktur lifecycle UI one-click save finalizes; no separate Finalisasi button", () => {
    expect(pageSrc).not.toMatch(/btn-finalize-invoice/);
    expect(pageSrc).not.toMatch(/finalize-confirm-modal/);
    expect(pageSrc).not.toMatch(/Finalisasi Invoice/);
    expect(pageSrc).not.toMatch(/finalizeSalesInvoice\(/);
    expect(pageSrc).toMatch(/finalize_on_save:\s*true/);
    expect(pageSrc).toMatch(/createSubmitLockRef/);
    expect(pageSrc).toMatch(/if \(createSubmitLockRef\.current \|\| formBusy\) return/);
    expect(pageSrc).toMatch(/Simpan & Finalisasi/);
    expect(pageSrc).toMatch(/berhasil disimpan dan difinalisasi/);
    expect(pageSrc).toMatch(/Gagal finalisasi setelah menyimpan/);
    expect(pageSrc).toMatch(/FINALIZE_AFTER_CREATE_FAILED/);
    expect(pageSrc).toMatch(/detail-payment-status/);
    expect(pageSrc).toMatch(/invoice-financial-badge-/);
    expect(pageSrc).toMatch(/btn-cancel-invoice/);
    expect(pageSrc).toMatch(/btn-post-accounting/);
    expect(pageSrc).toMatch(/Post ke Accounting/);
    expect(pageSrc).toMatch(/Posted/);
    expect(pageSrc).toMatch(/Reversed/);
    expect(pageSrc).toMatch(/Belum Diposting/);
    expect(pageSrc).toMatch(/btn-reverse-accounting/);
    expect(pageSrc).toMatch(/reverse-accounting-confirm-modal/);
    expect(pageSrc).toMatch(/reverse-reason-input/);
    expect(pageSrc).toMatch(/sales_accounting\.reverse/);
    expect(pageSrc).toMatch(/Accounting belum dikonfigurasi/);
    expect(pageSrc).toMatch(/post-accounting-confirm-modal/);
    expect(pageSrc).toMatch(/Qty Faktur akan kembali tersedia/);
  });

  test("FakturPenjualan create mode uses createCalculations safely without undefined invoiceLines", () => {
    expect(pageSrc).not.toMatch(/\binvoiceLines\b/);
    expect(pageSrc).toMatch(/createCalculations\?\.lines/);
    expect(pageSrc).toMatch(/FormProgress/);
    expect(pageSrc).toMatch(/Daftar Barang/);
    expect(pageSrc).toMatch(/Rincian Barang/);
    expect(pageSrc).toMatch(/Info Lainnya/);
    expect(pageSrc).toMatch(/Biaya Lainnya/);
    expect(pageSrc).toMatch(/ux90-fp-vtabs/);
    expect(pageSrc).toMatch(/invoice-breakdown/);
    expect(pageSrc).toMatch(/invoice-dpp/);
    expect(pageSrc).toMatch(/invoice-ppn/);
    expect(pageSrc).toMatch(/Belum lengkap/);
    expect(pageSrc).toMatch(/priceMissingAny/);
    expect(pageSrc).toMatch(/ux90-create-shell/);
    expect(pageSrc).toMatch(/Mengganti Nomor PO akan menghapus perubahan/);
  });

  test("draft invoice qty is an input and finalized or cancelled qty stays display-only", () => {
    expect(pageSrc).toMatch(/invoiceLineTestId\("detail-qty-input"/);
    expect(pageSrc).toMatch(/canEditFinancial \?/);
    expect(pageSrc).toMatch(/invoiceLineTestId\("detail-qty-text"/);
    expect(pageSrc).toMatch(/invoiceLineUiKey/);
    expect(pageSrc).toMatch(/toggle-multi-po-invoice/);
    expect(pageSrc).toMatch(/multi-po-invoice-picker/);
    expect(pageSrc).toMatch(/multi-po-save-invoice/);
    expect(pageSrc).toMatch(/financialLocked = Boolean\(detailData\) && detailStatus !== "draft"/);
    expect(pageSrc).toMatch(/min=\{1\}/);
    expect(pageSrc).toMatch(/max=\{li\.maxQty/);
  });

  test("draft qty cap is shown from backend metadata and save sends the edited qty", () => {
    expect(pageSrc).toMatch(/Qty PO:/);
    expect(pageSrc).toMatch(/Sudah difakturkan lainnya:/);
    expect(pageSrc).toMatch(/Maksimum Faktur ini:/);
    expect(pageSrc).toMatch(/li\.po_qty/);
    expect(pageSrc).toMatch(/li\.other_active_invoiced_qty/);
    expect(pageSrc).toMatch(/li\.maxQty/);
    expect(pageSrc).toMatch(/invoiceLineTestId\("detail-qty-cap"/);
    expect(pageSrc).toMatch(/resolveDetailLineCapacity/);
    expect(pageSrc).toMatch(/quantity: qty/);
    expect(pageSrc).toMatch(/setDetailQuantities/);
  });

  test("over-limit draft save and finalize show Eternal wording", () => {
    expect(pageSrc).toMatch(/Qty Faktur melebihi sisa Qty Pesanan Penjualan\./);
    expect(pageSrc).toMatch(/Qty Faktur melebihi sisa Qty Pesanan Penjualan\. Sesuaikan Qty Faktur sebelum Finalisasi\./);
    expect(pageSrc).toMatch(/FAKTUR_QTY_MELEBIHI_SISA_PO/);
    expect(pageSrc).toMatch(/DRAFT_QTY_OVER_MESSAGE/);
    expect(pageSrc).toMatch(/FINALIZE_QTY_OVER_MESSAGE/);
    expect(pageSrc).toMatch(/invoiceLineTestId\("detail-qty-over"/);
    expect(pageSrc).not.toMatch(/aggregate/);
    expect(pageSrc).not.toMatch(/non-cancelled/);
    expect(pageSrc).not.toMatch(/remaining capacity/);
  });

  test("draft financial fields, official PDF, and Proforma lifecycle stay in place", () => {
    expect(pageSrc).toMatch(/disabled=\{!canEditFinancial\}/);
    expect(pageSrc).toMatch(/btn-print-pdf-detail/);
    expect(pageSrc).toMatch(/Cetak PDF/);
    expect(pageSrc).toMatch(/Simpan & Finalisasi/);
    expect(pageSrc).not.toMatch(/btn-finalize-invoice/);
    expect(pageSrc).not.toMatch(/proforma/i);
    const proformaSrc = fs.readFileSync(path.join(__dirname, "../components/poDetail/ProformaInvoiceDocRow.jsx"), "utf8");
    expect(proformaSrc).toMatch(/Proforma/);
    expect(proformaSrc).not.toMatch(/detail-qty-input/);
  });

  test("detail shows live AR fields and PO/Penerimaan navigation links", () => {
    expect(pageSrc).toMatch(/detail-outstanding-amount/);
    expect(pageSrc).toMatch(/detail-paid-total/);
    expect(pageSrc).toMatch(/detail-payment-status/);
    expect(pageSrc).toMatch(/detail-po-link/);
    expect(pageSrc).toMatch(/detail-receipt-link-/);
    expect(pageSrc).toMatch(/queryInvoiceId/);
    expect(pageSrc).toMatch(/Sudah Diterima \(AR\)/);
    expect(pageSrc).toMatch(/Sisa Tagihan \(AR\)/);
  });

  test("list identity cell keeps stacked customer/invoice/outlet with titles and unclipped row height", () => {
    const densityCss = fs.readFileSync(path.join(__dirname, "salesSubmenuDensity.css"), "utf8");
    expect(pageSrc).toMatch(/className="sd-identity"/);
    expect(pageSrc).toMatch(/className="sd-identity-stack"/);
    expect(pageSrc).toMatch(/className="sd-customer"[\s\S]{0,40}title=\{inv\.customer/);
    expect(pageSrc).toMatch(/invoice-number-\$\{inv\.id\}[\s\S]{0,80}title=\{inv\.invoice_number/);
    expect(pageSrc).toMatch(/sd-support" title=\{inv\.outlet\}/);
    expect(densityCss).toMatch(/\.fp-invoice-page \.sd-list-table tbody td\{[\s\S]*?height:auto/);
    expect(densityCss).toMatch(/\.fp-invoice-page \.sd-list-table td\.sd-identity\{[\s\S]*?min-height:56px/);
    expect(densityCss).toMatch(/\.fp-invoice-page \.sd-list-table td\.sd-identity\{[\s\S]*?min-width:200px/);
    expect(densityCss).toMatch(/\.fp-invoice-page \.sd-identity-stack\{[\s\S]*?display:flex/);
    expect(pageSrc).toMatch(/fp-identity-col/);
    expect(pageSrc).toMatch(/width:\s*260/);
    expect(densityCss).toMatch(/\.fp-invoice-page \.sd-list-table col\.fp-identity-col\{[\s\S]*?width:260px/);
    expect(densityCss).toMatch(/\.fp-invoice-page \.sd-customer\{[\s\S]*?text-overflow:ellipsis/);
    expect(densityCss).toMatch(/\.fp-invoice-page \.sd-doc\.is-static\{[\s\S]*?text-overflow:ellipsis/);
    expect(densityCss).toMatch(/\.fp-invoice-page \.sd-identity \.sd-support\{[\s\S]*?text-overflow:ellipsis/);
  });
});
