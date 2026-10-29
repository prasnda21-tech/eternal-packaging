/** Helpers for PO Terlambat Kirim (PO-grain) widget + tracking page. */

export const PO_LATE_TRACKING_PATH = "/produksi/tracking-po-terlambat";

export const PO_LATE_VIEW_PERMS = [
  "dashboard.production.view",
  "delivery.schedule.view",
  "schedule.view",
  "nav.delivery_schedule",
];

export const CATEGORY_OPTIONS = [
  { value: "", label: "Semua kategori" },
  { value: "lewat_1_hari", label: "Lewat 1 Hari" },
  { value: "terlambat", label: "Terlambat" },
  { value: "kritis", label: "Kritis" },
];

export function poLateTrackingPath({
  late = true,
  category = "",
  branchId = "",
  q = "",
  visualFixture = false,
} = {}) {
  const params = new URLSearchParams();
  if (late) params.set("late", "1");
  if (category) params.set("category", category);
  if (branchId) params.set("branch_id", branchId);
  if (q) params.set("q", q);
  if (visualFixture) params.set("visualFixture", "1");
  const qs = params.toString();
  return qs ? `${PO_LATE_TRACKING_PATH}?${qs}` : PO_LATE_TRACKING_PATH;
}

export function poLateDrill({ visualFixture = false } = {}) {
  return {
    path: poLateTrackingPath({ late: true, visualFixture }),
    any_of: PO_LATE_VIEW_PERMS,
  };
}

export function mapPoLateSummary(body) {
  if (!body || typeof body !== "object") return { status: "unavailable" };
  // Never activate visualFixture drill in production builds, even if payload claims it.
  const visualFixture = process.env.NODE_ENV !== "production" && Boolean(body.visual_fixture);
  if (body.status === "empty") {
    return {
      status: "empty",
      overdue: 0,
      counts: { lewat_1_hari: 0, terlambat: 0, kritis: 0 },
      note: body.note || "PO aktif melewati tanggal pengiriman.",
      drill: poLateDrill({ visualFixture }),
    };
  }
  const counts = body.counts || {};
  return {
    status: "complete",
    overdue: Number(body.total) || 0,
    counts: {
      lewat_1_hari: Number(counts.lewat_1_hari) || 0,
      terlambat: Number(counts.terlambat) || 0,
      kritis: Number(counts.kritis) || 0,
    },
    note: body.note || "PO aktif melewati tanggal pengiriman.",
    drill: poLateDrill({ visualFixture }),
  };
}

export function categoryClass(category) {
  if (category === "kritis") return "is-critical";
  if (category === "terlambat") return "is-late";
  if (category === "lewat_1_hari") return "is-warn";
  return "";
}

export function formatIdDate(value) {
  const key = String(value || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return "—";
  const [y, m, d] = key.split("-");
  return `${d}/${m}/${y}`;
}
