/** Helpers for Stock Saat Ini → Import Excel Saldo Awal UX. */

export const STATUS_SIAP = "Siap";

export function isReadyRow(row) {
  return String(row?.status || "") === STATUS_SIAP;
}

export function rowSelectKey(row) {
  return String(row?.row_number ?? "");
}

/** Default: every Siap row selected. */
export function defaultSelectedKeys(rows = []) {
  return new Set((rows || []).filter(isReadyRow).map(rowSelectKey).filter(Boolean));
}

export function summarizeImportRows(rows = [], selectedKeys = new Set()) {
  const list = rows || [];
  const readyRows = list.filter(isReadyRow);
  const rejectedRows = list.filter((row) => !isReadyRow(row));
  const selectedRows = readyRows.filter((row) => selectedKeys.has(rowSelectKey(row)));
  return {
    readyCount: readyRows.length,
    rejectedCount: rejectedRows.length,
    selectedCount: selectedRows.length,
    selectedRows,
  };
}

export function buildConfirmLines(selectedRows = []) {
  return (selectedRows || []).map((row) => ({
    master_item_id: row.master_item_id,
    branch_id: row.branch_id,
    warehouse_id: row.warehouse_id,
    physical_qty: row.physical_qty,
    item_code: row.item_code,
  }));
}

/**
 * Interpret confirm response without inventing PROCESSING.
 * Returns: "confirmed" | "processing" | "unknown"
 */
export function classifyImportConfirmResponse(body) {
  const status = String(body?.import?.status || "").trim().toUpperCase();
  if (status === "CONFIRMED") return "confirmed";
  if (status === "PROCESSING") return "processing";
  // Legacy/partial payloads (tests, older clients): 200 with no status ⇒ treat as done.
  if (body && (body.idempotent === true || body.idempotent === false) && !status) {
    return "confirmed";
  }
  if (body && body.import && !status) return "unknown";
  return "unknown";
}

export const IMPORT_RESUME_SAFE_MESSAGE =
  "Impor belum selesai atau statusnya tidak pasti. Jangan unggah file baru dan jangan ubah pilihan baris. Klik Lanjutkan untuk mencoba lagi dengan idempotency key yang sama.";
