import { useRef, useState } from "react";
import { Upload, X } from "lucide-react";
import { importCounts, selectedImportRows, toggleRowSelected, toggleSelectAll } from "../lib/importPreview";
import { formatApiError } from "../lib/errors";

/**
 * Generic import preview modal: file pick → preview table with checkboxes → confirm apply.
 */
export default function ImportPreviewModal({
  open,
  title,
  testid = "import-preview-modal",
  columns,
  onClose,
  onPreviewFile,
  onApply,
  applyLabel = "Terapkan terpilih",
  confirmText = "Terapkan baris terpilih?",
}) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [summary, setSummary] = useState(null);

  if (!open) return null;

  const rows = preview?.rows || [];
  const counts = importCounts(rows);
  const allSelectable = rows.filter((r) => r.selectable);
  const allSelected = allSelectable.length > 0 && allSelectable.every((r) => r.selected);

  const reset = () => {
    setBusy(false);
    setErr("");
    setPreview(null);
    setConfirming(false);
    setSummary(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const close = () => {
    if (busy) return;
    reset();
    onClose?.();
  };

  const pickFile = async (file) => {
    if (!file || busy) return;
    setBusy(true);
    setErr("");
    setSummary(null);
    setConfirming(false);
    try {
      const data = await onPreviewFile(file);
      setPreview(data);
    } catch (e) {
      let msg = formatApiError(e, "Gagal mem-preview file");
      if (e?.code === "ECONNABORTED") {
        msg = "Preview timeout — file terlalu besar atau server lambat.";
      } else if (!e?.response) {
        msg = "Tidak terhubung ke server. Cek backend lokal lalu coba lagi.";
      }
      setErr(msg);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (busy || !preview) return;
    const selected = selectedImportRows(rows);
    if (!selected.length) {
      setErr("Pilih minimal satu baris valid.");
      return;
    }
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const result = await onApply(selected, preview);
      setSummary(result);
      setConfirming(false);
    } catch (e) {
      setErr(formatApiError(e, "Gagal menerapkan import"));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="drawer" role="dialog" aria-modal="true" data-testid={testid} onClick={close}>
      <div className="drawer-panel import-preview-panel" style={{ maxWidth: 960 }} onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <p className="eyebrow">Impor</p>
            <h3>{title}</h3>
          </div>
          <button type="button" className="icon-button" onClick={close} aria-label="Tutup" disabled={busy} data-testid={`${testid}-close`}>
            <X size={18} />
          </button>
        </header>
        <div className="drawer-body" style={{ display: "grid", gap: 12 }}>
          <div
            className="import-drop"
            data-testid={`${testid}-drop`}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = e.dataTransfer?.files?.[0];
              if (f) pickFile(f);
            }}
          >
            <Upload size={16} />
            <span>Seret file Excel/CSV atau pilih file</span>
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx,.csv,.txt"
              disabled={busy}
              onChange={(e) => pickFile(e.target.files?.[0])}
              data-testid={`${testid}-file`}
            />
          </div>

          {preview && (
            <div className="muted small" data-testid={`${testid}-counts`}>
              Total {counts.total} · Valid {counts.valid} · Invalid {counts.invalid}
              {counts.duplicate ? ` · Duplikat ${counts.duplicate}` : ""} · Terpilih {counts.selected}
            </div>
          )}

          {preview && (
            <div className="prod-table-wrap" style={{ maxHeight: 360, overflow: "auto" }}>
              <table className="prod-table" data-testid={`${testid}-table`}>
                <thead>
                  <tr>
                    <th className="col-fit">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={(e) => setPreview((p) => ({ ...p, rows: toggleSelectAll(p.rows, e.target.checked) }))}
                        data-testid={`${testid}-select-all`}
                        aria-label="Pilih semua"
                      />
                    </th>
                    {columns.map((c) => (
                      <th key={c.key} className={c.num ? "num" : undefined}>{c.label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.row_id} data-testid={`${testid}-row-${r.row_id}`} className={!r.selectable ? "is-disabled" : ""}>
                      <td>
                        <input
                          type="checkbox"
                          checked={!!r.selected && r.selectable}
                          disabled={!r.selectable || busy}
                          onChange={(e) => setPreview((p) => ({
                            ...p,
                            rows: toggleRowSelected(p.rows, r.row_id, e.target.checked),
                          }))}
                          data-testid={`${testid}-check-${r.row_id}`}
                        />
                      </td>
                      {columns.map((c) => (
                        <td key={c.key} className={c.num ? "num mono" : undefined}>
                          {c.render ? c.render(r) : (r[c.key] ?? "—")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {err && <div className="error" data-testid={`${testid}-error`}>{err}</div>}
          {summary && (
            <div className="signal-strip" data-testid={`${testid}-summary`}>
              Berhasil: {summary.applied_count ?? summary.imported_count ?? 0} baris
              {(summary.rejected_count > 0) ? ` · Ditolak: ${summary.rejected_count}` : ""}
            </div>
          )}
          {confirming && (
            <p className="muted small" data-testid={`${testid}-confirm-note`}>{confirmText}</p>
          )}

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="ghost" onClick={close} disabled={busy}>Tutup</button>
            <button
              type="button"
              className="primary"
              disabled={busy || !preview || counts.selected === 0}
              onClick={apply}
              data-testid={`${testid}-apply`}
            >
              {busy ? "Memproses…" : confirming ? "Ya, terapkan" : applyLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
