import fs from "fs";
import path from "path";

describe("PO Detail delivery cards", () => {
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const proof = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
  const qtyUi = fs.readFileSync(path.join(__dirname, "../components/qtyUi.jsx"), "utf8");
  const wage = fs.readFileSync(path.join(__dirname, "OperatorWageSettings.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

  test("Bukti Kirim appears before Terkirim without duplicate cards", () => {
    const slot = detail.slice(detail.indexOf("workflow-slot-14"));
    const bukti = slot.indexOf("DeliveryProofPanel");
    const terkirim = slot.indexOf("terkirim-panel");
    expect(bukti).toBeGreaterThan(-1);
    expect(terkirim).toBeGreaterThan(-1);
    expect(bukti).toBeLessThan(terkirim);
    expect(slot.split("DeliveryProofPanel").length - 1).toBe(1);
    expect(slot.split("terkirim-panel").length - 1).toBe(1);
  });

  test("Atur Lokasi is available on PO detail without create-schedule", () => {
    expect(detail).toMatch(/Atur Lokasi|Edit Lokasi/);
    expect(detail).toMatch(/po-atur-lokasi/);
    expect(detail).toMatch(/OutletLocationModal/);
    expect(detail).toMatch(/delivery.location.manage/);
  });

  test("Terkirim summary has total delivered remaining qty", () => {
    expect(detail).toMatch(/terkirim-total-po/);
    expect(detail).toMatch(/terkirim-delivered-qty/);
    expect(detail).toMatch(/terkirim-remaining-qty/);
    expect(detail).toMatch(/terkirimQtySummary/);
  });

  test("Bukti Kirim delete waits on the request and does not duplicate", () => {
    expect(proof).toMatch(/deleteDeliveryProof/);
    expect(proof).toMatch(/deletingId/);
    expect(proof).toMatch(/if \(!p\?\.id \|\| deletingId \|\| uploading\) return;/);
    expect(proof).toMatch(/disabled=\{\!\!deletingId\}/);
    expect(proof).not.toMatch(/current_stage\s*=\s*13/);
    expect(proof).not.toMatch(/setStage\(/);
  });

  test("Bukti Kirim history shows driver item qty time plate photos", () => {
    expect(proof).toMatch(/<h3>Bukti Kirim<\/h3>/);
    expect(proof).toMatch(/delivery-history/);
    expect(proof).toMatch(/Nama Driver/);
    expect(proof).toMatch(/Barang \/ Item/);
    expect(proof).toMatch(/Plat Kendaraan/);
    expect(proof).toMatch(/delivery-history-photo/);
    expect(proof).toMatch(/confirm-delivered-button/);
    expect(proof).toMatch(/Upload foto tidak menandai Terkirim/);
  });

  test("Driver/Terkirim actions use delivery.update not role name", () => {
    expect(proof).toMatch(/hasPerm\("delivery\.update"\)/);
    expect(proof).not.toMatch(/role\s*===\s*["']driver["']/);
    expect(proof).not.toMatch(/role\s*!==\s*["']driver["']/);
    expect(proof).not.toMatch(/isDriver/);
    expect(proof).not.toMatch(/Aksi ini tidak tersedia untuk role Anda/);
    expect(detail).toMatch(/hasPerm\("delivery\.update"\)/);
  });

  test("manual qty input uses dedicated contrast class not global input", () => {
    expect(qtyUi).toMatch(/manual-qty-input/);
    expect(qtyUi).toMatch(/is-manual/);
    expect(qtyUi).toMatch(/is-disabled/);
    expect(css).toMatch(/manual-qty-input/);
  });

  test("wage settings tables scroll inside wrapper not page", () => {
    expect(wage).toMatch(/wage-table-wrap/);
    expect(wage).toMatch(/wage-table/);
    expect(wage).not.toMatch(/prod-table/);
    expect(css).toMatch(/\.wage-settings\{[^}]*max-width:100%/);
    expect(css).toMatch(/\.wage-table-wrap\{[^}]*overflow-x:auto/);
  });

  test("shows readable chevron stageflow without generic title", () => {
    const bar = fs.readFileSync(path.join(__dirname, "../components/StageBar.jsx"), "utf8");
    expect(detail).toMatch(/po-stage-bar-block/);
    expect(detail).toMatch(/showLabels/);
    expect(detail).not.toMatch(/Alur Workflow/);
    expect(bar).toMatch(/stage-bar-step-num/);
    expect(bar).toMatch(/stage-bar-label/);
    expect(bar).toMatch(/resolveStageState/);
    expect(css).toMatch(/\.stage-bar-label/);
    expect(css).toMatch(/clip-path:polygon/);
    expect(css).toMatch(/\.po-stage-bar-block\{[^}]*overflow-x:auto/);
    expect(css).toMatch(/po-meta-aksi-row/);
  });

  test("PROSES KE PENGIRIMAN PESANAN is gated by remain+perm only, not Packing/Loading", () => {
    expect(detail).toMatch(/canProcessSalesDelivery/);
    expect(detail).toMatch(/aksiPoSalesDeliveryState/);
    expect(detail).toMatch(/salesDeliveryTotalRemaining/);
    expect(detail).toMatch(/btn-proses-pengiriman-pesanan/);
    expect(detail).toMatch(/badge-pengiriman-selesai|Seluruh Qty Selesai Dibuatkan SJ/);
    // No packing/loading prerequisite on the Aksi PO create entry
    const gateBlock = detail.slice(
      detail.indexOf("aksiPoSalesDeliveryState"),
      detail.indexOf("aksiPoSalesDeliveryState") + 200,
    );
    expect(gateBlock).not.toMatch(/packing|loading|qty_packed|qty_loaded/i);
    expect(detail).toMatch(/DeliveryProofPanel/);
  });

  test("StageBar uses resolveStageState so early SJ does not skip Loading without eligible", () => {
    const bar = fs.readFileSync(path.join(__dirname, "../components/StageBar.jsx"), "utf8");
    const stageState = fs.readFileSync(path.join(__dirname, "../lib/stageState.js"), "utf8");
    expect(bar).toMatch(/resolveStageState/);
    expect(stageState).toMatch(/eligible_delivery_qty/);
    expect(stageState).toMatch(/Menunggu konfirmasi Loading/);
    // Pengiriman current only when eligible > 0, not merely because SJ/shipment exists
    expect(stageState).toMatch(/if \(eligible > 0\)/);
    expect(stageState).toMatch(/Menunggu bukti pengiriman/);
  });

  test("Aksi PO sits above Line Item and shipping summary is gone", () => {
    expect(detail.indexOf("detail-info")).toBeLessThan(detail.indexOf("po-actions-panel"));
    expect(detail.indexOf("po-actions-panel")).toBeLessThan(detail.indexOf("line-items-panel"));
    expect(detail.split("data-testid=\"po-actions-panel\"").length - 1).toBe(1);
    expect(detail).not.toMatch(/sales-delivery-summary-panel/);
    expect(detail).not.toMatch(/Pengiriman Pesanan \(Surat Jalan Penjualan\)/);
    expect(detail).not.toMatch(/kpi-delivery-total-order/);
    expect(detail).not.toMatch(/po-sj-docs/);
    expect(detail.indexOf("workflow-slot-11")).toBeLessThan(detail.indexOf("workflow-slot-12"));
    expect(detail.indexOf("workflow-slot-12")).toBeLessThan(detail.indexOf("workflow-slot-13"));
    expect(detail.indexOf("workflow-slot-13")).toBeLessThan(detail.indexOf("workflow-slot-14"));
    expect(detail).toMatch(/copy-tracking-link/);
    expect(detail).toMatch(/btn-proses-pengiriman-pesanan/);
    expect(detail).toMatch(/btn-buat-faktur-penjualan/);
  });

  test("hero puts customer above No PO", () => {
    expect(detail).toMatch(/detail-customer-name/);
    expect(detail).toMatch(/po-hero-customer/);
    expect(detail.indexOf("detail-customer-name")).toBeLessThan(detail.indexOf("detail-po-number"));
  });

  test("imports formatDisplayDate for remaining receipt dates", () => {
    expect(detail).toMatch(/import\s*\{[^}]*formatDisplayDate[^}]*\}\s*from\s*["']\.\.\/lib\/constants["']/);
    expect(detail).toMatch(/formatDisplayDate\(r\.receipt_date \|\| r\.created_at\)/);
  });

  test("pairs Approval Design with Customer Approval in one outer card", () => {
    expect(detail.split('data-testid="approval-design-panel"').length - 1).toBe(1);
    expect(detail).toMatch(/po-pair-card po-phase-design/);
    expect(detail).toMatch(/approve-design-button/);
    expect(detail).toMatch(/bypass-design-button/);
    expect(detail).toMatch(/<CustomerApprovalPanel/);
    const ca = fs.readFileSync(path.join(__dirname, "../components/CustomerApprovalPanel.jsx"), "utf8");
    expect(ca).toMatch(/Buat Link Approval/);
    expect(ca).toMatch(/ca-generate-button/);
    const slot2 = detail.slice(detail.indexOf("workflow-slot-2"), detail.indexOf("payment-approval-pair"));
    expect(slot2).toMatch(/CustomerApprovalPanel/);
    expect(slot2.split('data-testid="approval-design-panel"').length - 1).toBe(1);
    expect(slot2).not.toMatch(/data-testid="customer-approval-standalone"/);
  });

  test("pairs payment evidence with Approval Payment in one outer card", () => {
    expect(detail).toMatch(/payment-approval-pair/);
    expect(detail).toMatch(/Payment &amp; Approval/);
    expect(detail).toMatch(/upload-payment-label/);
    expect(detail).toMatch(/Tambah Bukti/);
    expect(detail).toMatch(/approve-payment-button/);
    expect(detail).toMatch(/bypass-payment-button/);
    expect(detail.split('data-testid="approval-payment-panel"').length - 1).toBe(1);
    expect(detail.split('data-testid="payment-panel"').length - 1).toBe(1);
    expect(detail.indexOf("payment-approval-pair")).toBeLessThan(detail.indexOf("approval-payment-panel"));
  });

  test("pairs Penjadwalan with Pembagian Jadwal without separate Deadline panel", () => {
    // Group B is a single header; next stage slot (6) bounds Penjadwalan content.
    const slot5 = detail.slice(detail.indexOf("workflow-slot-5"), detail.indexOf("workflow-slot-6"));
    expect(slot5.split("<section").length - 1).toBe(1);
    expect(slot5).toMatch(/penjadwalan-panel/);
    expect(slot5).toMatch(/po-pair-grid-schedule/);
    expect(slot5).toMatch(/mode="schedule"/);
    expect(slot5).not.toMatch(/DeliveryDeadlinePanel/);
    expect(detail).toMatch(/Tambah Operator|operator-split-panel|mode="schedule"/);
    expect(detail).toMatch(/detail-tanggal-pengiriman/);
  });

  test("pairs Cetak Sample with Approval Cetak Sample and keeps Qty Selesai separate", () => {
    expect(detail).toMatch(/cetak-sample-pair/);
    expect(detail).toMatch(/section="sample"/);
    expect(detail).toMatch(/approve-sample-button/);
    expect(detail).toMatch(/approval-sample-panel/);
    expect(detail.indexOf("cetak-sample-pair")).toBeLessThan(detail.indexOf("workflow-slot-10"));
    expect(detail.indexOf("workflow-slot-8")).toBeLessThan(detail.indexOf("workflow-slot-9"));
    expect(detail.indexOf("workflow-slot-9")).toBeLessThan(detail.indexOf("workflow-slot-10"));
    expect(detail).toMatch(/mode="progress"/);
  });

  test("Detail PO headers pad enough to avoid clipped titles", () => {
    expect(css).toMatch(/\.po-detail \.panel-head/);
    expect(css).not.toMatch(/padding:8px 2px 7px/);
    expect(css).toMatch(/white-space:normal/);
    expect(css).toMatch(/po-phase-design/);
    expect(css).toMatch(/po-phase-payment/);
    expect(css).toMatch(/po-phase-scheduling/);
    expect(css).toMatch(/po-phase-sample/);
    expect(css).toMatch(/po-pair-grid/);
  });

  test("Detail PO card headers are solid without gradients", () => {
    const start = css.indexOf(".po-detail .panel-head,");
    const end = css.indexOf(".po-detail .qty-ops-title{");
    const headBlock = css.slice(start, end);
    expect(headBlock).toMatch(/background:var\(--mod/);
    expect(headBlock).toMatch(/background-image:none/);
    expect(headBlock).not.toMatch(/linear-gradient|radial-gradient|conic-gradient/);
    expect(headBlock).toMatch(/color:var\(--mod-head-fg/);
  });

  test("Detail PO keeps --mod-ink dark so ghost button labels stay readable", () => {
    expect(css).toMatch(/\.po-detail\{[^}]*--mod-head-fg:#0f172a/);
    expect(css).not.toMatch(/\.po-detail\{[^}]*--mod-ink:#fff/);
    expect(css).not.toMatch(/--mod-ink:#fff;--mod-tint/);
    // Industrial chrome uses light header bands + dark ink (not white-on-teal saturation)
    expect(css).toMatch(/po-detail-industrial[\s\S]*panel-head[\s\S]*background:#f1f5f7/);
  });
});
