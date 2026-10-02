import fs from "fs";
import path from "path";

describe("Customer tracking public page", () => {
  const page = fs.readFileSync(path.join(__dirname, "CustomerTrackingPage.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const panel = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const cssMobileLayout = () => {
    expect(css).toMatch(/tracking-timeline/);
    expect(css).toMatch(/max-width:600px/);
    expect(css).toMatch(/--track-green:var\(--ep-dark\)/);
    expect(css).toMatch(/\.tracking-page\{[^}]*width:100%[^}]*max-width:100%/);
    expect(css).toMatch(/align-items:stretch/);
    expect(css).toMatch(/\.tracking-hero\{[^}]*flex-wrap:wrap/);
    expect(css).toMatch(/\.tracking-shell\{[^}]*margin-inline:auto/);
    expect(css).toMatch(/100dvh/);
    expect(css).toMatch(/safe-area-inset-top/);
    expect(css).toMatch(/safe-area-inset-bottom/);
    expect(css).toMatch(/tracking-map/);
    expect(css).toMatch(/tracking-doc-grid/);
    expect(css).not.toMatch(/\.tracking-page\{[^}]*overflow-y:\s*hidden/);
    expect(css).not.toMatch(/\.tracking-page[^{]*\{[^}]*gradient/);
    return true;
  };

  test("public route is outside Protected AppShell and does not go to login", () => {
    expect(app).toMatch(/path="\/tracking\/:token"/);
    expect(app).toMatch(/path="\/t\/:customerSlug\/:poSlug\/:code"/);
    expect(app).toMatch(/CustomerTrackingPage/);
    const trackingIdx = app.indexOf('path="/tracking/:token"');
    const prettyIdx = app.indexOf('path="/t/:customerSlug/:poSlug/:code"');
    const protectedIdx = app.indexOf("<Protected>");
    const catchAllIdx = app.indexOf('path="*"');
    expect(trackingIdx).toBeGreaterThan(-1);
    expect(prettyIdx).toBeGreaterThan(-1);
    expect(trackingIdx).toBeLessThan(protectedIdx);
    expect(prettyIdx).toBeLessThan(protectedIdx);
    expect(trackingIdx).toBeLessThan(catchAllIdx);
    const trackingLine = app.split("\n").find((l) => l.includes("/tracking/:token")) || "";
    expect(trackingLine).toMatch(/CustomerTrackingPage/);
    expect(trackingLine).not.toMatch(/login/);
  });

  test("page has no internal shell, login redirect, or sensitive fields", () => {
    expect(page).toMatch(/params\.code \|\| params\.token/);
    expect(page).toMatch(/customer_name/);
    expect(page).not.toMatch(/params\.customerSlug/);
    expect(page).not.toMatch(/params\.poSlug/);
    expect(page).toMatch(/customer-tracking-page/);
    expect(page).toMatch(/Progress Pesanan/);
    expect(page).toMatch(/tracking-timeline/);
    expect(page).toMatch(/trackingDisplayLabel/);
    expect(page).toMatch(/No\. PO/);
    expect(page).not.toMatch(/Navigate to="\/login"/);
    expect(page).not.toMatch(/AppShell/);
    expect(page).not.toMatch(/employee/);
    expect(page).not.toMatch(/accurate/i);
    expect(page).not.toMatch(/qty_/);
    expect(page).not.toMatch(/unit_price|harga/i);
    expect(page).not.toMatch(/audit/i);
    expect(page).not.toMatch(/line_id|workflow_ready|token_hash/);
    expect(page).not.toMatch(/hpp|margin|biznet/i);
  });

  test("public fetch does not send credentials", () => {
    expect(api).toMatch(/publicTracking:\s*async/);
    expect(api).toMatch(/\/public\/tracking\//);
    const start = api.indexOf("publicTracking:");
    const fn = api.slice(start, start + 400);
    expect(fn).toMatch(/fetch\(/);
    expect(fn).not.toMatch(/withCredentials:\s*true/);
    expect(fn).not.toMatch(/credentials:\s*['"]include['"]/);
  });

  test("internal detail keeps Live Tracking in Aksi PO and Buat Faktur in Dokumen", () => {
    expect(detail).toMatch(/Live Tracking/);
    expect(detail).toMatch(/copy-tracking-link/);
    expect(detail).toMatch(/tracking-share-whatsapp/);
    expect(detail).toMatch(/tracking-share-qr/);
    expect(detail).toMatch(/trackingShareFromLink/);
    expect(detail).toMatch(/orderTrackingLink/);
    expect(detail).not.toMatch(/live-tracking-workflow/);
    expect(detail).not.toMatch(/liveTrackingRows/);
    const docs = detail.slice(
      detail.indexOf('data-testid="po-doc-panel-dokumen"'),
      detail.indexOf('data-testid="po-doc-panel-riwayat"'),
    );
    expect(docs).toMatch(/copy-tracking-link/);
    expect(docs).toMatch(/btn-proses-pengiriman-pesanan/);
    expect(docs).toMatch(/btn-buat-faktur-penjualan/);
    const info = detail.slice(detail.indexOf('data-testid="detail-info"'), detail.indexOf('data-testid="line-items-panel"'));
    expect(info).not.toMatch(/btn-buat-faktur-penjualan/);
    expect(info).not.toMatch(/btn-proses-pengiriman-pesanan/);
    expect(detail).not.toMatch(/sales-delivery-summary-panel/);
    expect(detail).not.toMatch(/Pengiriman Pesanan \(Surat Jalan Penjualan\)/);
  });

  test("public page shows customer name po and proofs", () => {
    expect(page).toMatch(/Customer/);
    expect(page).toMatch(/tracking-customer-name/);
    expect(page).toMatch(/Bukti Pengiriman/);
    expect(page).toMatch(/slice\(0, 5\)/);
  });

  test("mobile portal structure and approval CTA wiring", () => {
    expect(page).toMatch(/tracking-hero/);
    expect(page).toMatch(/Live Tracking Pesanan/);
    expect(page).toMatch(/tracking-approval-cta/);
    expect(page).toMatch(/Lihat & Approval/);
    expect(page).toMatch(/hasApprovalCta/);
    expect(page).toMatch(/approvalActionPath/);
    expect(page).toMatch(/href=\{ctaPath\}/);
    expect(page).not.toMatch(/approval\.token/);
    expect(page).toMatch(/Design Disetujui/);
    expect(page).toMatch(/Dokumen Pesanan/);
    expect(page).toMatch(/tracking-detail/);
    expect(page).toMatch(/Butuh Bantuan/);
    expect(page).not.toMatch(/wa\.me/);
    expect(page).not.toMatch(/Hubungi via WhatsApp/);
    expect(cssMobileLayout());
  });

  test("surat jalan view opens in-app and keeps a separate download href", () => {
    expect(page).toMatch(/tracking-sj-\$\{d\.id\}-open/);
    expect(page).toMatch(/openSuratJalanDocument\(d\)/);
    expect(page).toMatch(/preview_url/);
    expect(page).toMatch(/tracking-sj-\$\{d\.id\}-download/);
    expect(page).toMatch(/download_url/);
    expect(page).not.toMatch(/target="_blank"/);
    expect(page).not.toMatch(/surat-jalan", \{ method/);
    expect(page).not.toMatch(/packed_qty/);
  });

  test("public page may show reschedule date only", () => {
    expect(page).toMatch(/tracking-reschedule/);
    expect(page).toMatch(/scheduled_date/);
    expect(page).not.toMatch(/requested_by/);
    expect(page).not.toMatch(/reason/);
  });

  test("public page shows customer-safe delivery history", () => {
    expect(page).toMatch(/tracking-delivery-history/);
    expect(page).toMatch(/Nama Driver/);
    expect(page).toMatch(/Plat Kendaraan/);
    expect(page).toMatch(/slice\(0, 5\)/);
    expect(page).not.toMatch(/driver_id/);
    expect(page).not.toMatch(/storage_path/);
  });

  test("design preview and surat jalan Lihat PDF use the in-app viewer", () => {
    expect(page).toMatch(/InAppDocumentViewer/);
    expect(page).toMatch(/tracking-design-viewer/);
    expect(page).toMatch(/tracking-sj-viewer/);
    expect(page).toMatch(/TrackingPaymentProofs/);
    const lihat = page.split("tracking-lihat-design")[1].slice(0, 400);
    expect(lihat).toMatch(/openDesignDocument/);
    expect(lihat).not.toMatch(/window\.open/);
    expect(page).toMatch(/openSuratJalanDocument/);
    expect(page).not.toMatch(/target="_blank"/);
    const sjOpen = page.split("tracking-sj-${d.id}-open")[1].slice(0, 120);
    expect(sjOpen).toMatch(/openSuratJalanDocument/);
    expect(sjOpen).not.toMatch(/href=/);
  });

  test("approved design and surat jalan open/download controls", () => {
    expect(page).toMatch(/tracking-design-approved/);
    expect(page).toMatch(/tracking-surat-jalan/);
    expect(page).toMatch(/-open/);
    expect(page).toMatch(/-download/);
    expect(page).not.toMatch(/biznet/i);
    expect(page).not.toMatch(/object.?key/i);
  });

  test("delivered evidence shows photos signature name time", () => {
    expect(page).toMatch(/tracking-shipments-evidence/);
    expect(page).toMatch(/Nama Penerima/);
    expect(page).toMatch(/Tanda Tangan Penerima/);
    expect(page).toMatch(/Waktu Ditandatangani/);
  });

  test("driver panel has signature pad and recipient name with 2-photo button rule", () => {
    expect(panel).toMatch(/SignaturePad/);
    expect(panel).toMatch(/recipient-name-input/);
    expect(panel).toMatch(/recipient-signature-block/);
    expect(panel).toMatch(/driver-signature-block/);
    expect(panel).toMatch(/TTD Driver \/ Pengirim/);
    expect(panel).toMatch(/canConfirmDelivered\(proofs\)/);
    expect(panel).toMatch(/canSubmitConfirmDelivered/);
    expect(panel).toMatch(/confirm-delivered-button/);
  });

  test("driver signature pad input stays square via CSS", () => {
    const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
    expect(css).toMatch(/\.driver-signature-block \.signature-pad\{[^}]*width:200px/);
    expect(css).toMatch(/\.driver-signature-block \.signature-pad > div:first-child\{[^}]*aspect-ratio:1 \/ 1/);
  });

  test("tracking evidence can show driver signature", () => {
    expect(page).toMatch(/tracking-evidence-driver-signature/);
    expect(page).toMatch(/Tanda tangan driver/);
  });

  test("one-screen compact dashboard keeps primary surfaces and sheets", () => {
    expect(page).toMatch(/is-onescreen/);
    expect(page).toMatch(/tracking-po-summary/);
    expect(page).toMatch(/tracking-workflow-rail/);
    expect(page).toMatch(/data-branched="0"/);
    expect(page).not.toMatch(/hasParallelPair|toFork|toHelper/);
    expect(page).toMatch(/tracking-open-timeline/);
    expect(page).toMatch(/Lihat Timeline/);
    expect(page).toMatch(/tracking-doc-grid/);
    expect(page).toMatch(/tracking-doc-tile/);
    expect(page).toMatch(/tracking-utils/);
    expect(page).toMatch(/setSheet\("detail"\)/);
    expect(page).toMatch(/setSheet\("timeline"\)/);
    expect(page).toMatch(/disabled=\{!tile\.available\}/);
    expect(page).toMatch(/tracking-sheet/);
    expect(page).not.toMatch(/method:\s*['"]POST['"]/);
  });
});
