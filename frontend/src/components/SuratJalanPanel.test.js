import fs from "fs";
import path from "path";

describe("Surat Jalan card upload + preview", () => {
  const src = fs.readFileSync(path.join(__dirname, "SuratJalanPanel.jsx"), "utf8");
  const preview = fs.readFileSync(path.join(__dirname, "SuratJalanPreviewModal.jsx"), "utf8");
  const loading = fs.readFileSync(path.join(__dirname, "LoadingPanel.jsx"), "utf8");
  const settings = fs.readFileSync(path.join(__dirname, "../pages/Settings.jsx"), "utf8");
  const schedule = fs.readFileSync(path.join(__dirname, "../pages/DeliverySchedulePage.jsx"), "utf8");
  const role = fs.readFileSync(path.join(__dirname, "RoleWorkPanel.jsx"), "utf8");
  const detail = fs.readFileSync(path.join(__dirname, "../pages/PODetail.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

  test("card has file upload only, no manual item and qty", () => {
    expect(src).toMatch(/Pilih File|Upload Surat Jalan/);
    expect(src).toMatch(/surat-jalan-upload-button/);
    expect(src).toMatch(/kind: "surat_jalan"/);
    expect(src).toMatch(/surat-jalan-delivery-date/);
    expect(src).toMatch(/Kosong = pengiriman hari ini/);
    expect(src).toMatch(/delivery_date/);
    expect(src).not.toMatch(/Tanggal pengiriman wajib diisi/);
    expect(src).not.toMatch(/sj-qty-input/);
    expect(src).not.toMatch(/sj-item-select/);
    expect(src).not.toMatch(/ITEM SURAT JALAN/);
    expect(src).not.toMatch(/surat-jalan-number-input/);
    expect(src).not.toMatch(/Menunggu Packing/);
  });

  test("preview modal and download for authorized users", () => {
    expect(src).toMatch(/Lihat Surat Jalan/);
    expect(src).toMatch(/delivery_note.view_file|SJ_VIEW_FILE_PERM/);
    expect(preview).toMatch(/SURAT JALAN/);
    expect(preview).toMatch(/sj-preview-download/);
    expect(preview).toMatch(/File Surat Jalan tidak dapat ditampilkan/);
    expect(preview).toMatch(/Tutup/);
    expect(preview).toMatch(/application\/pdf/);
  });

  test("permission is configurable via existing role catalog UI", () => {
    expect(settings).toMatch(/permissionCatalog/);
    expect(settings).toMatch(/Role \& Hak Akses|settings-tab-roles/);
  });

  test("file preview on schedule, loading, driver, and PO detail", () => {
    expect(schedule).toMatch(/SuratJalanViewButton/);
    expect(loading).toMatch(/loading-sj-lihat/);
    expect(role).toMatch(/terkirim-sj-lihat/);
    expect(detail).toMatch(/terkirim-sj-lihat/);
    expect(detail).toMatch(/canViewSjFile|delivery_note.view_file/);
  });

  test("does not reintroduce Route Optimization or Rute Saya", () => {
    expect(src).not.toMatch(/Optimize Rute|Rute Saya/);
    expect(nav).toMatch(/nav.tasks.loading/);
    expect(app).not.toMatch(/MyRoutePage/);
    expect(app).toMatch(/path="\/produksi\/rute-saya"/);
  });
});
