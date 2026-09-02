export const AUTOMATION_LABELS = {
  ACTIVE: "Aktif",
  PAUSED_MIGRATION: "Paused — migrasi",
  PAUSED_MANUAL: "Paused — manual",
};

export const KIND_LABELS = {
  PROSPECT: "Prospek",
  CUSTOMER: "Customer aktif",
  DORMANT: "Dormant",
};

export function automationPausedMigration(status) {
  return status === "PAUSED_MIGRATION";
}

export function canManageOwnership(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("sales.ownership.manage");
}

export function canManagePool(hasPerm) {
  return typeof hasPerm === "function" && (hasPerm("sales.pool.manage") || hasPerm("sales.ownership.manage"));
}

export function canImportSalesActivity(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("sales.activity.import");
}

export function canActivateMigration(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("sales.activity.activate_migration");
}

export function canCreateOutlet(hasPerm) {
  return typeof hasPerm === "function" && (hasPerm("sales.outlet.create") || hasPerm("sales.ownership.manage"));
}

export function canCreateActivity(hasPerm) {
  return typeof hasPerm === "function" && (hasPerm("sales.activity.create") || hasPerm("sales.ownership.manage"));
}

export function validateSettingsDraft(draft) {
  const errors = [];
  const rule = draft?.default_rule || {};
  if (Number(rule.ownership_duration_days) < 1) errors.push("Durasi ownership harus lebih dari 0");
  if (Number(rule.inactivity_expiration_days) < 1) errors.push("Inactivity expiration harus lebih dari 0");
  if (Number(rule.warning_before_expiration_days) >= Number(rule.ownership_duration_days)) {
    errors.push("Warning expiration harus lebih kecil dari durasi ownership");
  }
  if (Number(rule.inactivity_warning_days) >= Number(rule.inactivity_expiration_days)) {
    errors.push("Warning inactivity harus lebih kecil dari inactivity expiration");
  }
  const seq = draft?.reassignment?.sales_sequence || [];
  if (new Set(seq).size !== seq.length) errors.push("Urutan sales tidak boleh duplikat");
  if (Number(draft?.dormancy?.threshold_days) < 1) errors.push("Ambang dormant harus lebih dari 0");
  if ((draft?.dormancy?.destination || "POOL") !== "POOL") errors.push("Destinasi dormant harus Pool");
  return errors;
}

export function importDoesNotActivate(text) {
  return String(text || "").includes("Import tidak menjalankan automation");
}

export const GEO_UNAVAILABLE_MESSAGE = "Lokasi tidak dapat diambil. Izinkan akses lokasi pada browser.";
export const GEO_TIMEOUT_MESSAGE = "Lokasi timeout. Coba Ambil Lokasi lagi.";
export const GEO_UNSUPPORTED_MESSAGE = "Browser ini tidak mendukung lokasi. Gunakan Chrome/Edge di localhost.";
export const VISIT_PHOTO_UPLOAD_PATH = "/sales-activity/visit-photos";

export function selectedVisitPhotoFiles(fileList) {
  return Array.from(fileList || []).filter((file) => file && file.size >= 0 && file.name != null);
}

export function visitPhotoUploadError(err) {
  const status = err?.response?.status;
  const detail = err?.response?.data?.detail;
  if (status === 404 || String(detail || "") === "Not Found") {
    return "Gagal unggah foto visit. Endpoint /sales-activity/visit-photos tidak tersedia.";
  }
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail?.message) return detail.message;
  if (err?.message && !/status code 404/i.test(err.message)) return err.message;
  return "Gagal unggah foto visit.";
}

export function isVisitActivityType(typeId) {
  return String(typeId || "").trim().toLowerCase() === "visit";
}

export function visitPhotoRequiredForType(typeCfg) {
  if (!typeCfg || !isVisitActivityType(typeCfg.id || typeCfg.activity_type)) return false;
  return Boolean(typeCfg.visit_photo_required || typeCfg.evidence_required);
}

export function isValidVisitCoordinate(lat, lng) {
  const latitude = Number(lat);
  const longitude = Number(lng);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return false;
  if (Math.abs(latitude) < 1e-6 && Math.abs(longitude) < 1e-6) return false;
  return latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180;
}

export function visitMapsUrl(lat, lng) {
  if (!isValidVisitCoordinate(lat, lng)) return "";
  return `https://www.google.com/maps?q=${latitudeParam(lat)},${latitudeParam(lng)}`;
}

function latitudeParam(value) {
  return Number(value);
}

export function compactVisitIndicator(activity) {
  const photos = activity?.visit_photos || [];
  const count = Number(activity?.visit_photo_count ?? photos.length ?? 0);
  const hasLocation = Boolean(activity?.has_visit_location || photos.some((p) => p.location_status === "OK"));
  return {
    photoLabel: `Foto: ${count}`,
    locationLabel: hasLocation ? "Lokasi: Tersedia" : "Lokasi: -",
  };
}

export function hasHistoricalEvidence(activity) {
  return Boolean(String(activity?.evidence_type || "").trim() || String(activity?.evidence_link || "").trim());
}

export function captureVisitGeolocation(getCurrentPosition = defaultGetCurrentPosition) {
  return new Promise((resolve) => {
    if (typeof getCurrentPosition !== "function") {
      resolve({ status: "UNSUPPORTED", message: GEO_UNAVAILABLE_MESSAGE });
      return;
    }
    try {
      getCurrentPosition(
        (pos) => {
          const latitude = pos?.coords?.latitude;
          const longitude = pos?.coords?.longitude;
          const accuracy = pos?.coords?.accuracy;
          if (!isValidVisitCoordinate(latitude, longitude)) {
            resolve({ status: "UNAVAILABLE", message: GEO_UNAVAILABLE_MESSAGE });
            return;
          }
          resolve({ status: "OK", latitude, longitude, accuracy });
        },
        (err) => {
          const code = Number(err?.code);
          if (code === 1) {
            resolve({ status: "DENIED", message: GEO_UNAVAILABLE_MESSAGE });
            return;
          }
          if (code === 3) {
            resolve({ status: "UNAVAILABLE", message: GEO_TIMEOUT_MESSAGE });
            return;
          }
          if (code === 2) {
            resolve({ status: "UNAVAILABLE", message: GEO_UNSUPPORTED_MESSAGE });
            return;
          }
          resolve({
            status: "UNAVAILABLE",
            message: GEO_UNAVAILABLE_MESSAGE,
          });
        },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
      );
    } catch {
      resolve({ status: "UNSUPPORTED", message: GEO_UNAVAILABLE_MESSAGE });
    }
  });
}

function defaultGetCurrentPosition(success, error, options) {
  if (typeof navigator === "undefined" || !navigator.geolocation?.getCurrentPosition) {
    error?.({ code: 2, message: GEO_UNSUPPORTED_MESSAGE });
    return;
  }
  navigator.geolocation.getCurrentPosition(success, error, options);
}
