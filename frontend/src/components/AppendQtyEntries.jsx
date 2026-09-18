import { formatDisplayDateTime } from "../lib/constants";
import { sumEntryQty, visibleEntries } from "../lib/workflowEntries";
import { fmtQty, QuantityInput } from "./qtyUi";

export default function AppendQtyEntries({
  entries,
  snapshotQty,
  snapshotAt,
  snapshotName,
  draft,
  onDraftChange,
  onConfirmClick,
  canEdit,
  busy,
  locked,
  testid,
  remaining,
  unit = "pcs",
  inputTestid,
  confirmTestid,
}) {
  const rows = visibleEntries(entries, snapshotQty, snapshotAt, snapshotName);
  const total = rows.length ? sumEntryQty(rows) : Number(snapshotQty || 0);
  const draftQty = Number(draft || 0);
  const remainingQty = remaining == null ? null : Math.max(0, Number(remaining || 0));
  const exceedsRemaining = remainingQty != null && draftQty > remainingQty;
  return (
    <div className="append-entries" data-testid={testid || "append-entries"}>
      {rows.length > 0 && (
        <table className="append-entries-table">
          <thead>
            <tr>
              <th>Qty</th>
              <th>Waktu</th>
              <th>Nama</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e, i) => (
              <tr key={e.id || i} data-testid={`${testid || "append"}-row-${i}`} className="is-locked">
                <td className="mono">{fmtQty(e.qty)}</td>
                <td className="mono small">{e.created_at ? formatDisplayDateTime(e.created_at) : "—"}</td>
                <td>{e.created_by_name || e.created_by || "—"}</td>
                <td className="muted small">LOCKED</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="append-entries-total muted small">TOTAL: <strong className="mono">{fmtQty(total)}</strong> {unit}</div>
      {canEdit && !locked && (remaining == null || Number(remaining) > 0) && (
        <div className="append-entries-new">
          <QuantityInput
            label="Qty tambahan"
            min="1"
            max={remaining != null ? remaining : undefined}
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            disabled={busy}
            testid={inputTestid || `${testid || "append"}-input`}
            className={`is-compact${exceedsRemaining ? " is-invalid" : ""}`}
            title={exceedsRemaining ? `Qty tambahan melebihi sisa tahap sebelumnya (${remainingQty}).` : undefined}
          />
          <button
            type="button"
            className="primary"
            onClick={onConfirmClick}
            disabled={busy || draftQty <= 0 || exceedsRemaining}
            data-testid={confirmTestid || `${testid || "append"}-confirm`}
          >
            Konfirmasi
          </button>
          {exceedsRemaining && (
            <span className="error small" role="alert" data-testid={`${testid || "append"}-limit-error`}>
              Maksimal tambahan {fmtQty(remainingQty)} {unit}.
            </span>
          )}
        </div>
      )}
    </div>
  );
}
