import {
  GEO_TIMEOUT_MESSAGE,
  GEO_UNAVAILABLE_MESSAGE,
  VISIT_PHOTO_UPLOAD_PATH,
  automationPausedMigration,
  canActivateMigration,
  canCreateActivity,
  canImportSalesActivity,
  canManageOwnership,
  canManagePool,
  captureVisitGeolocation,
  compactVisitIndicator,
  hasHistoricalEvidence,
  importDoesNotActivate,
  isValidVisitCoordinate,
  isVisitActivityType,
  selectedVisitPhotoFiles,
  validateSettingsDraft,
  visitMapsUrl,
  visitPhotoRequiredForType,
  visitPhotoUploadError,
} from "./salesActivity";

const has = (...keys) => (k) => keys.includes(k);

describe("salesActivity helpers", () => {
  test("imported automation paused indication", () => {
    expect(automationPausedMigration("PAUSED_MIGRATION")).toBe(true);
    expect(automationPausedMigration("ACTIVE")).toBe(false);
    expect(importDoesNotActivate("Import tidak menjalankan automation.")).toBe(true);
  });

  test("own-sales vs supervisor controls", () => {
    expect(canCreateActivity(has("sales.activity.create"))).toBe(true);
    expect(canManageOwnership(has("sales.activity.create"))).toBe(false);
    expect(canManagePool(has("sales.activity.create"))).toBe(false);
    expect(canActivateMigration(has("sales.activity.create"))).toBe(false);
    expect(canImportSalesActivity(has("sales.activity.import"))).toBe(true);
    expect(canManageOwnership(has("sales.ownership.manage"))).toBe(true);
    expect(canManagePool(has("sales.pool.manage"))).toBe(true);
  });

  test("settings validation rejects invalid timing and duplicate sequence", () => {
    const bad = validateSettingsDraft({
      default_rule: { ownership_duration_days: 0, inactivity_expiration_days: 10, warning_before_expiration_days: 1, inactivity_warning_days: 1 },
      reassignment: { sales_sequence: ["a", "a"] },
      dormancy: { threshold_days: 90, destination: "POOL" },
    });
    expect(bad.length).toBeGreaterThan(0);
    const ok = validateSettingsDraft({
      default_rule: { ownership_duration_days: 30, inactivity_expiration_days: 30, warning_before_expiration_days: 14, inactivity_warning_days: 14 },
      reassignment: { sales_sequence: ["a", "b"] },
      dormancy: { threshold_days: 90, destination: "POOL" },
    });
    expect(ok).toEqual([]);
  });

  test("visit photo helpers and maps url", () => {
    expect(isVisitActivityType("visit")).toBe(true);
    expect(isVisitActivityType("chat")).toBe(false);
    expect(visitPhotoRequiredForType({ id: "visit", evidence_required: true })).toBe(true);
    expect(visitPhotoRequiredForType({ id: "chat", evidence_required: true })).toBe(false);
    expect(isValidVisitCoordinate(0, 0)).toBe(false);
    expect(isValidVisitCoordinate(-6.2, 106.8)).toBe(true);
    expect(visitMapsUrl(-6.2, 106.8)).toBe("https://www.google.com/maps?q=-6.2,106.8");
    expect(compactVisitIndicator({ visit_photo_count: 2, has_visit_location: true })).toEqual({
      photoLabel: "Foto: 2",
      locationLabel: "Lokasi: Tersedia",
    });
    expect(hasHistoricalEvidence({ evidence_type: "foto_wa", evidence_link: "" })).toBe(true);
  });

  test("geolocation success stores valid coordinates and accuracy", async () => {
    const ok = await captureVisitGeolocation((success) =>
      success({ coords: { latitude: -6.2, longitude: 106.8, accuracy: 9 } }),
    );
    expect(ok.status).toBe("OK");
    expect(ok.latitude).toBe(-6.2);
    expect(ok.longitude).toBe(106.8);
    expect(ok.accuracy).toBe(9);
    expect(ok.message).toBeUndefined();
  });

  test("geolocation denied is explicit and never faked", async () => {
    const denied = await captureVisitGeolocation((_ok, err) => err({ code: 1 }));
    expect(denied.status).toBe("DENIED");
    expect(denied.message).toBe(GEO_UNAVAILABLE_MESSAGE);
    expect(denied.latitude).toBeUndefined();
    expect(denied.longitude).toBeUndefined();
    expect(denied.message).not.toBe("Not Found");
    expect(isValidVisitCoordinate(denied.latitude, denied.longitude)).toBe(false);
  });

  test("geolocation timeout and zero coordinates stay unavailable", async () => {
    const timeout = await captureVisitGeolocation((_ok, err) => err({ code: 3 }));
    expect(timeout.status).toBe("UNAVAILABLE");
    expect(timeout.message).toBe(GEO_TIMEOUT_MESSAGE);
    expect(timeout.latitude).toBeUndefined();
    const zero = await captureVisitGeolocation((success) =>
      success({ coords: { latitude: 0, longitude: 0, accuracy: 1 } }),
    );
    expect(zero.status).toBe("UNAVAILABLE");
    expect(zero.latitude).toBeUndefined();
  });

  test("empty photo selection does not imply an upload", () => {
    expect(selectedVisitPhotoFiles(undefined)).toEqual([]);
    expect(selectedVisitPhotoFiles([])).toEqual([]);
    expect(selectedVisitPhotoFiles({ length: 0 })).toEqual([]);
    const file = { name: "visit.jpg", size: 12 };
    expect(selectedVisitPhotoFiles([file])).toEqual([file]);
  });

  test("upload 404 is a specific upload error not Not Found", () => {
    expect(VISIT_PHOTO_UPLOAD_PATH).toBe("/sales-activity/visit-photos");
    const msg = visitPhotoUploadError({
      response: { status: 404, data: { detail: "Not Found" } },
      message: "Request failed with status code 404",
    });
    expect(msg).not.toBe("Not Found");
    expect(msg).toMatch(/Gagal unggah foto visit/);
    expect(visitPhotoUploadError({ response: { status: 400, data: { detail: "File bukan gambar" } } })).toBe(
      "File bukan gambar",
    );
  });

  test("maps url uses stored coordinates and non-visit does not require photo", () => {
    expect(visitMapsUrl(-6.175, 106.827)).toBe("https://www.google.com/maps?q=-6.175,106.827");
    expect(visitMapsUrl(0, 0)).toBe("");
    expect(visitPhotoRequiredForType({ id: "chat", evidence_required: true })).toBe(false);
    expect(visitPhotoRequiredForType({ id: "visit", evidence_required: false })).toBe(false);
  });
});
