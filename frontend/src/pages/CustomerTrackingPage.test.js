import fs from "fs";
import path from "path";

describe("Customer tracking public page", () => {
  const page = fs.readFileSync(path.join(__dirname, "CustomerTrackingPage.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const panel = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");

  test("public route is outside Protected AppShell and does not go to login", () => {
    expect(app).toMatch(/path="\/tracking\/:token"/);
    expect(app).toMatch(/CustomerTrackingPage/);
    const trackingIdx = app.indexOf('path="/tracking/:token"');
    const protectedIdx = app.indexOf("<Protected>");
    const catchAllIdx = app.indexOf('path="*"');
    expect(trackingIdx).toBeGreaterThan(-1);
    expect(trackingIdx).toBeLessThan(protectedIdx);
    expect(trackingIdx).toBeLessThan(catchAllIdx);
    const trackingLine = app.split("\n").find((l) => l.includes("/tracking/:token")) || "";
    expect(trackingLine).toMatch(/CustomerTrackingPage/);
    expect(trackingLine).not.toMatch(/login/);
  });

  test("page has no internal shell, login redirect, or sensitive fields", () => {
    expect(page).toMatch(/customer-tracking-page/);
    expect(page).toMatch(/Alur Kerja/);
    expect(page).toMatch(/trackingDisplayLabel/);
    expect(page).toMatch(/No PO/);
    expect(page).not.toMatch(/Navigate to="\/login"/);
    expect(page).not.toMatch(/AppShell/);
    expect(page).not.toMatch(/employee/);
    expect(page).not.toMatch(/accurate/i);
    expect(page).not.toMatch(/qty_/);
    expect(page).not.toMatch(/unit_price|harga/i);
    expect(page).not.toMatch(/audit/i);
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

  test("internal detail keeps document actions in Aksi PO not Pengiriman header", () => {
    expect(detail).toMatch(/Live Tracking/);
    expect(detail).toMatch(/copy-tracking-link/);
    expect(detail).toMatch(/orderTrackingLink/);
    expect(detail).not.toMatch(/live-tracking-workflow/);
    expect(detail).not.toMatch(/liveTrackingRows/);
    const actions = detail.slice(detail.indexOf("po-actions-panel"), detail.indexOf("line-items-panel"));
    expect(actions).toMatch(/copy-tracking-link/);
    expect(actions).toMatch(/btn-proses-pengiriman-pesanan/);
    expect(actions).toMatch(/btn-buat-faktur-penjualan/);
    expect(detail).not.toMatch(/sales-delivery-summary-panel/);
    expect(detail).not.toMatch(/Pengiriman Pesanan \(Surat Jalan Penjualan\)/);
  });

  test("public page shows customer name po and proofs", () => {
    expect(page).toMatch(/Nama Customer/);
    expect(page).toMatch(/tracking-customer-name/);
    expect(page).toMatch(/Bukti Pengiriman/);
    expect(page).toMatch(/slice\(0, 5\)/);
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
});
