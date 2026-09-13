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

  test("Detail PO hides upload controls while RoleWorkPanel keeps them", () => {
    expect(detail).toMatch(/allowUpload=\{false\}/);
    expect(role).not.toMatch(/allowUpload=\{false\}/);
    expect(src).toMatch(/allowUpload = true/);
    expect(src).toMatch(/Pilih File/);
    expect(src).toMatch(/Upload Surat Jalan/);
    expect(src).toMatch(/Lihat Surat Jalan/);
    expect(src).toMatch(/Download/);
    expect(src).toMatch(/Hapus File Surat Jalan/);
  });

  test("generated SJ documents are labeled without removing manual upload", () => {
    expect(src).toMatch(/Dibuat Sistem/);
    expect(src).toMatch(/SYSTEM_GENERATED/);
    expect(src).toMatch(/surat-jalan-upload-button/);
    expect(src).toMatch(/Upload Surat Jalan/);
  });

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

  test("Hapus File is permission-gated with confirmation", () => {
    expect(src).toMatch(/SJ_DELETE_FILE_PERM|delivery_note.delete_file/);
    expect(src).toMatch(/Hapus File Surat Jalan/);
    expect(src).toMatch(/Hapus file Surat Jalan ini\?/);
    expect(src).toMatch(/deleteSuratJalanFile/);
    expect(src).toMatch(/sj-hapus-/);
  });

  test("FILE TERSIMPAN badge uses live sjHistory files, not shipment ghosts", () => {
    expect(src).toMatch(/const files = sjHistory\(order\)/);
    expect(src).toMatch(/const created = files\.length > 0/);
    expect(src).toMatch(/File tersimpan/);
    expect(src).not.toMatch(/const created = !!order\.surat_jalan_created/);
  });

  test("Loading view uses file-view permission and does not grant upload", () => {
    const viewBtn = fs.readFileSync(path.join(__dirname, "SuratJalanViewButton.jsx"), "utf8");
    expect(viewBtn).toMatch(/SJ_VIEW_FILE_PERM/);
    expect(viewBtn).not.toMatch(/delivery_note.manage/);
    expect(loading).toMatch(/SuratJalanViewButton/);
    expect(loading).toMatch(/Lihat Surat Jalan/);
    expect(loading).not.toMatch(/Upload Surat Jalan/);
    expect(loading).not.toMatch(/addAttachment/);
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
