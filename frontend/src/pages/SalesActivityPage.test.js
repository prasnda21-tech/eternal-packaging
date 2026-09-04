import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "SalesActivityPage.jsx"), "utf8");
const detail = fs.readFileSync(path.join(__dirname, "SalesActivityOutletDetail.jsx"), "utf8");
const settings = fs.readFileSync(path.join(__dirname, "..", "components", "SalesActivitySettings.jsx"), "utf8");
const photos = fs.readFileSync(path.join(__dirname, "..", "components", "SalesActivityVisitPhotos.jsx"), "utf8");

describe("SalesActivityPage contracts", () => {
  test("has activity create form and own-sales fields", () => {
    expect(src).toMatch(/data-testid="sa-activity-form"/);
    expect(src).toMatch(/api\.createSalesActivity/);
    expect(src).toMatch(/data-testid="sa-outlet-create-form"/);
  });

  test("outlet prospek owner is selectable PIC dropdown", () => {
    expect(src).toMatch(/data-testid="sa-outlet-owner-select"/);
    expect(src).toMatch(/api\.salesActivityProspectPicUsers/);
    expect(src).toMatch(/Owner \/ PIC Sales/);
    expect(src).toMatch(/Belum ada PIC Sales Prospek yang diatur/);
    expect(src).toMatch(/data-testid="sa-outlet-pic-empty"/);
    expect(src).toMatch(/submitDisabled=\{!canSubmit\}/);
    expect(src).not.toMatch(/Owner mengikuti akun yang sedang login/);
    expect(src).toMatch(/owner_id: form\.owner_id/);
  });

  test("activity tab remains separate from outlet owner select", () => {
    expect(src).toMatch(/data-testid="sa-tab-activity"/);
    expect(src).toMatch(/function ActivityTab/);
    expect(src.indexOf("sa-outlet-owner-select")).toBeLessThan(src.indexOf("function ActivityTab"));
  });

  test("import preview is separate from activation", () => {
    expect(src).toMatch(/Import tidak menjalankan automation/);
    expect(src).toMatch(/data-testid="sa-import-paused-banner"/);
    expect(src).toMatch(/data-testid="sa-import-confirm"/);
    expect(src).toMatch(/data-testid="sa-activate-migration"/);
    expect(src).toMatch(/api\.salesActivityImportPreview/);
    expect(src).toMatch(/Aktifkan automation data migrasi/);
  });

  test("shows conflict state and pool assignment controls", () => {
    expect(src).toMatch(/data-testid="sa-import-conflicts"/);
    expect(src).toMatch(/data-testid="sa-pool-table"/);
    expect(src).toMatch(/api\.salesActivityAssignFromPool/);
    expect(src).toMatch(/Alasan wajib/);
  });

  test("imported paused indication on outlet list", () => {
    expect(src).toMatch(/data-testid="sa-import-paused"/);
    expect(src).toMatch(/PAUSED_MIGRATION/);
  });
});

describe("SalesActivityOutletDetail contracts", () => {
  test("supervisor-only controls and immutable history", () => {
    expect(detail).toMatch(/data-testid="sa-supervisor-controls"/);
    expect(detail).toMatch(/canManageOwnership/);
    expect(detail).toMatch(/data-testid="sa-history-table"/);
    expect(detail).toMatch(/data-testid="sa-detail-paused"/);
  });

  test("prospect reassign uses PIC allowlist options", () => {
    expect(detail).toMatch(/salesActivityProspectPicUsers/);
    expect(detail).toMatch(/data-testid="sa-detail-owner-select"/);
    expect(detail).toMatch(/isProspect/);
    expect(detail).toMatch(/\(saat ini\)/);
  });

  test("detail shows visit photos and historical evidence", () => {
    expect(detail).toMatch(/VisitPhotoList/);
    expect(detail).toMatch(/HistoricalEvidence/);
  });
});

describe("SalesActivity visit photo UI", () => {
  test("visit form shows foto lokasi and keeps top-right primary action", () => {
    expect(src).toMatch(/Foto & Lokasi Visit/);
    expect(src).toMatch(/VisitPhotoComposer/);
    expect(photos).toMatch(/data-testid="sa-visit-photo-section"/);
    expect(src).not.toMatch(/Evidence Type/);
    expect(src).not.toMatch(/Evidence Link/);
    expect(src).toMatch(/Tambah Activity/);
    expect(src).toMatch(/SaTabHead/);
    expect(src).toMatch(/isVisitActivityType\(form\.activity_type\)/);
  });

  test("photo preview date location maps and denied geo", () => {
    expect(photos).toMatch(/data-testid="sa-visit-photo-preview"/);
    expect(photos).toMatch(/formatDisplayDateTime\(photo\.visit_at\)/);
    expect(photos).toMatch(/visitMapsUrl\(photo\.latitude, photo\.longitude\)/);
    expect(photos).toMatch(/data-testid="sa-visit-maps-link"/);
    expect(photos).toMatch(/data-testid="sa-visit-geo-denied"/);
    expect(photos).toMatch(/GEO_UNAVAILABLE_MESSAGE/);
  });

  test("ambil foto and upload do not call backend until a file is selected", () => {
    expect(photos).toMatch(/onClick=\{\(\) => cameraRef\.current\?\.click\(\)\}/);
    expect(photos).toMatch(/onClick=\{\(\) => fileRef\.current\?\.click\(\)\}/);
    expect(photos).toMatch(/const files = selectedVisitPhotoFiles\(fileList\);/);
    expect(photos).toMatch(/if \(!files\.length\) return;/);
    expect(photos).toMatch(/api\.uploadSalesActivityVisitPhoto\(form\)/);
    expect(photos).toMatch(/visitPhotoUploadError/);
  });

  test("ambil lokasi only requests geolocation", () => {
    expect(photos).toMatch(/const onCaptureLocation = async \(\) => \{\s*await captureGeo\(\);\s*\}/s);
    expect(photos).toMatch(/onClick=\{onCaptureLocation\}/);
    expect(photos).not.toMatch(/onCaptureLocation[\s\S]{0,80}uploadSalesActivityVisitPhoto/);
  });

  test("activity detail renders persisted photo and location metadata", () => {
    expect(detail).toMatch(/VisitPhotoList/);
    expect(detail).toMatch(/a\.visit_photos/);
    expect(src).toMatch(/\{isVisitActivityType\(form\.activity_type\) && \(/);
  });

  test("frontend calls the visit-photos upload endpoint", () => {
    const apiSrc = fs.readFileSync(path.join(__dirname, "..", "lib", "api.js"), "utf8");
    expect(apiSrc).toMatch(/uploadSalesActivityVisitPhoto: \(form\) => client\.post\(`\/sales-activity\/visit-photos`/);
    expect(apiSrc).toMatch(/salesActivityVisitPhotoUrl: \(photoId\) => `\$\{API\}\/sales-activity\/visit-photos\/\$\{encodeURIComponent\(photoId\)\}\/file`/);
    expect(apiSrc).toMatch(/salesActivityDetail: \(id\) => client\.get\(`\/sales-activity\/activities\/\$\{encodeURIComponent\(id\)\}`/);
  });
});

describe("SalesActivitySettings contracts", () => {
  test("validates and locks dormant destination to pool", () => {
    expect(settings).toMatch(/validateSettingsDraft/);
    expect(settings).toMatch(/data-testid="sa-dormant-destination"/);
    expect(settings).toMatch(/Destinasi dormant: Pool/);
    expect(settings).toMatch(/data-testid="sa-settings-save"/);
  });

  test("PIC Sales Prospek allowlist multi-select", () => {
    expect(settings).toMatch(/PIC Sales Prospek/);
    expect(settings).toMatch(/data-testid="sa-settings-prospect-pic"/);
    expect(settings).toMatch(/prospect_pic_user_ids/);
    expect(settings).toMatch(/toggleProspectPic/);
    expect(settings).toMatch(/status \|\| "active"/);
  });

  test("visit setting uses foto visit wajib label", () => {
    expect(settings).toMatch(/Foto Visit wajib/);
  });
});
