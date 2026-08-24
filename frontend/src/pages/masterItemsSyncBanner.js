export const SYNC_FAIL_MSG =
  "Gagal sinkronisasi Master Barang/Jasa dari Accurate. Data existing tidak diubah.";

/** Sync banner state. Failures are never restored on reload. */
export function nextMasterSyncBanner(action, payload) {
  switch (action) {
    case "start":
      return { error: "", result: null };
    case "success":
      return { error: "", result: payload ?? null };
    case "fail":
      return { error: SYNC_FAIL_MSG, result: null };
    case "reload":
      return { error: "", result: null };
    default:
      return { error: "", result: null };
  }
}
