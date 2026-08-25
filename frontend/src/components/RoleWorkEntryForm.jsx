import { QuantityInput } from "./qtyUi";

export default function RoleWorkEntryForm({
  label = "Qty",
  unit = "pcs",
  draft,
  onDraftChange,
  onConfirmClick,
  canEdit,
  busy,
  remaining,
  helperText,
  testid = "role-work-entry",
}) {
  if (!canEdit) return null;
  if (remaining != null && Number(remaining) <= 0) {
    return <p className="muted small" data-testid={`${testid}-done`}>Target terpenuhi. Tidak ada pekerjaan baru.</p>;
  }
  return (
    <div className="role-work-entry" data-testid={testid}>
      <div className="role-work-entry-title">Pekerjaan Baru</div>
      <div className="role-work-entry-row">
        <QuantityInput
          label={label}
          min="1"
          max={remaining != null ? remaining : undefined}
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          disabled={busy}
          testid={`${testid}-input`}
          className="is-compact"
        />
        <button
          type="button"
          className="primary"
          onClick={onConfirmClick}
          disabled={busy || !Number(draft)}
          data-testid={`${testid}-confirm`}
        >
          Konfirmasi
        </button>
      </div>
      {helperText ? <p className="muted small">{helperText}</p> : null}
    </div>
  );
}
