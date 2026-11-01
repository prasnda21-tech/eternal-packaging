import { Plus, Trash2 } from "lucide-react";
import {
  allocatedTotal,
  allocationVariance,
  allocationWarningMessage,
  emptyAllocationRow,
  isInventoryLineForAllocation,
  normalizeAllocationRows,
} from "../lib/stockAllocationForm";

/**
 * Compact multi-warehouse allocation editor.
 * Recording only — empty/unbalanced allocations never disable parent submit.
 */
export default function StockAllocationEditor({
  line,
  warehouses = [],
  onChange,
  disabled = false,
  testIdPrefix = "stock-alloc",
}) {
  if (!isInventoryLineForAllocation(line)) {
    return (
      <p className="so-line-hint" data-testid={`${testIdPrefix}-skipped`}>
        Baris jasa/non-inventory tidak memakai alokasi gudang.
      </p>
    );
  }

  const rows = normalizeAllocationRows(line?.stock_allocations);
  const ordered = Number(line?.quantity) || 0;
  const total = allocatedTotal(rows);
  const variance = allocationVariance(ordered, rows);
  const warning = allocationWarningMessage({ ...line, stock_allocations: rows });
  const unit = String(line?.unit || "pcs").trim() || "pcs";

  const updateRows = (next) => {
    if (typeof onChange === "function") onChange(next);
  };

  return (
    <div className="so-stock-alloc" data-testid={testIdPrefix}>
      <div className="so-stock-alloc-head">
        <strong>Alokasi gudang</strong>
        <span className="so-line-hint">Pencatatan saja — tidak memblokir simpan</span>
      </div>
      <div className="so-stock-alloc-rows">
        {rows.map((row, idx) => (
          <div className="so-stock-alloc-row" key={`alloc-${idx}`} data-testid={`${testIdPrefix}-row-${idx}`}>
            <select
              className="so-line-input"
              value={row.warehouse_id || ""}
              disabled={disabled}
              aria-label={`Gudang alokasi ${idx + 1}`}
              data-testid={`${testIdPrefix}-wh-${idx}`}
              onChange={(e) => {
                const next = rows.map((r, i) => (i === idx ? { ...r, warehouse_id: e.target.value } : r));
                updateRows(next);
              }}
            >
              <option value="">— Pilih gudang —</option>
              {warehouses.map((wh) => (
                <option key={wh.id} value={wh.id}>
                  {wh.code ? `${wh.code} · ${wh.name || ""}` : (wh.name || wh.id)}
                </option>
              ))}
            </select>
            <input
              type="number"
              min="0"
              step="any"
              className="so-line-input"
              value={row.qty ?? ""}
              disabled={disabled}
              placeholder="Qty"
              aria-label={`Qty alokasi ${idx + 1}`}
              data-testid={`${testIdPrefix}-qty-${idx}`}
              onChange={(e) => {
                const next = rows.map((r, i) => (i === idx ? { ...r, qty: e.target.value } : r));
                updateRows(next);
              }}
            />
            <button
              type="button"
              className="so-stock-alloc-remove"
              disabled={disabled || rows.length <= 1}
              aria-label="Hapus alokasi"
              data-testid={`${testIdPrefix}-remove-${idx}`}
              onClick={() => updateRows(rows.filter((_, i) => i !== idx))}
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
      <div className="so-stock-alloc-actions">
        <button
          type="button"
          className="so-stock-alloc-add"
          disabled={disabled}
          data-testid={`${testIdPrefix}-add`}
          onClick={() => updateRows([...rows, emptyAllocationRow()])}
        >
          <Plus size={14} aria-hidden="true" /> Tambah gudang
        </button>
        <span className="so-stock-alloc-summary" data-testid={`${testIdPrefix}-summary`}>
          Dialokasikan {total.toLocaleString("id-ID")} / {ordered.toLocaleString("id-ID")} {unit}
          {variance !== 0 ? ` · selisih ${variance > 0 ? "+" : ""}${variance.toLocaleString("id-ID")}` : ""}
        </span>
      </div>
      {warning ? (
        <p className="ux90-warn-box so-stock-alloc-warn" role="status" data-testid={`${testIdPrefix}-warn`}>
          {warning}
        </p>
      ) : null}
    </div>
  );
}
