/** Presentation-only helpers for Penjualan submenu lists. */

/**
 * A document count is shown only for a finished, unfiltered load.
 * A search query makes the loaded length a filter result, not the module total.
 * Zero after a successful unfiltered load is a valid count.
 */
export function showLoadedDocumentCount({ loading, error, query } = {}) {
  if (loading) return false;
  if (error) return false;
  if (String(query || "").trim()) return false;
  return true;
}

export function loadedRangeLabel(count, noun) {
  const n = Number(count) || 0;
  const label = noun || "dokumen";
  if (n <= 0) return `Menampilkan 0 ${label}`;
  return `Menampilkan 1–${n.toLocaleString("id-ID")} dari ${n.toLocaleString("id-ID")} ${label}`;
}
