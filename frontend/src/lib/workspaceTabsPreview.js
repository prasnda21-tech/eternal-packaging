/**
 * Dev-only helper to seed multiple desktop workspace tabs for visual preview.
 * Never used in production builds.
 */

export const WORKSPACE_TAB_PREVIEW_FLAG = "previewWorkspaceTabs";

const PREVIEW_PATHS = [
  "/produksi/jadwal-pengiriman",
  "/produksi",
  "/laporan/status-po",
  "/laporan/analitik-output",
];

export function mergeWorkspaceTabsPreview(tabs, search) {
  if (process.env.NODE_ENV !== "development") return tabs || [];
  const params = new URLSearchParams(search || "");
  if (params.get(WORKSPACE_TAB_PREVIEW_FLAG) !== "1") return tabs || [];

  // eslint-disable-next-line global-require
  const { GLOBAL_WORKSPACE_TABS } = require("./globalWorkspace");
  const byPath = Object.fromEntries((GLOBAL_WORKSPACE_TABS || []).map((t) => [t.path, t]));
  const seeded = PREVIEW_PATHS.map((p) => byPath[p]).filter(Boolean);

  const merged = new Map();
  [...seeded, ...(tabs || [])].forEach((t) => {
    if (t?.id) merged.set(t.id, t);
  });
  return Array.from(merged.values());
}
