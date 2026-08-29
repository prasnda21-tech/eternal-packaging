/** Shared helpers for stock / prediction import preview modals. */

export function selectedImportRows(rows) {
  return (rows || []).filter((r) => r.selectable && r.selected);
}

export function toggleRowSelected(rows, rowId, checked) {
  return (rows || []).map((r) => (
    r.row_id === rowId && r.selectable ? { ...r, selected: !!checked } : r
  ));
}

export function toggleSelectAll(rows, checked) {
  return (rows || []).map((r) => (
    r.selectable ? { ...r, selected: !!checked } : { ...r, selected: false }
  ));
}

export function importCounts(rows) {
  const list = rows || [];
  return {
    total: list.length,
    valid: list.filter((r) => r.status === "VALID").length,
    invalid: list.filter((r) => r.status === "INVALID" || r.status === "TIDAK DITEMUKAN" || r.status === "KODE AMBIGU" || r.status === "ID AMBIGU").length,
    duplicate: list.filter((r) => r.status === "DUPLIKAT").length,
    selected: selectedImportRows(list).length,
  };
}
