import { useEffect, useState } from "react";
import { MessageSquare, Pencil } from "lucide-react";
import { formatApiError } from "../../lib/errors";

/**
 * Single Catatan Produksi panel. Append and replace stay separate actions.
 */
export default function ProductionNotesPanel({
  notes,
  visible,
  canAppend,
  canEdit,
  busy,
  onAppend,
  onReplace,
}) {
  const [composerOpen, setComposerOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [saveErr, setSaveErr] = useState("");
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState("");
  const [editErr, setEditErr] = useState("");

  const saved = typeof notes === "string" ? notes : "";

  useEffect(() => {
    if (!draft.trim() && !editing) return undefined;
    const onLeave = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onLeave);
    return () => window.removeEventListener("beforeunload", onLeave);
  }, [draft, editing]);

  if (!visible) return null;

  const hasNotes = !!saved;
  const showEdit = !!canEdit && hasNotes && !editing;

  const toggleComposer = () => {
    if (editing) return;
    if (composerOpen && draft.trim()) {
      if (!window.confirm("Catatan belum disimpan. Tutup kotak catatan?")) return;
    }
    setSaveErr("");
    if (composerOpen) setDraft("");
    setComposerOpen((open) => !open);
  };

  const startEdit = () => {
    if (composerOpen && draft.trim()) {
      if (!window.confirm("Catatan belum disimpan. Tutup kotak catatan?")) return;
    }
    setComposerOpen(false);
    setDraft("");
    setSaveErr("");
    setEditDraft(saved);
    setEditErr("");
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setEditDraft("");
    setEditErr("");
  };

  const saveAppend = async () => {
    const text = draft.trim();
    if (!text) {
      setSaveErr("Catatan tidak boleh kosong.");
      return;
    }
    setSaveErr("");
    try {
      await onAppend(text);
      setDraft("");
      setComposerOpen(false);
    } catch (e) {
      setSaveErr(formatApiError(e, "Gagal menyimpan catatan"));
    }
  };

  const saveReplace = async () => {
    const text = editDraft.trim();
    if (!text) {
      setEditErr("Catatan tidak boleh kosong.");
      return;
    }
    setEditErr("");
    try {
      await onReplace(text);
      setEditing(false);
      setEditDraft("");
    } catch (e) {
      setEditErr(formatApiError(e, "Gagal menyimpan catatan"));
    }
  };

  return (
    <section
      className={`panel po-notes-panel${!hasNotes && !composerOpen && !editing ? " is-empty" : ""}`}
      data-testid="notes-panel"
    >
      <header className="panel-head po-om-section-head">
        <h3>Catatan Produksi</h3>
        <span className="po-notes-actions">
          {showEdit && (
            <button
              type="button"
              className="ghost sm"
              data-testid="edit-note-button"
              onClick={startEdit}
              disabled={busy}
            >
              <Pencil size={14} /> Edit
            </button>
          )}
          {canAppend && !editing && (
            <button
              type="button"
              className="ghost sm"
              data-testid="toggle-note-composer"
              aria-expanded={composerOpen}
              onClick={toggleComposer}
            >
              Tambah Catatan
            </button>
          )}
        </span>
      </header>
      {editing ? (
        <div className="note-add" data-testid="note-edit">
          <textarea
            value={editDraft}
            onChange={(e) => { setEditDraft(e.target.value); if (editErr) setEditErr(""); }}
            rows={4}
            data-testid="edit-note-input"
            className="manual-input"
          />
          {editErr && <p className="error" data-testid="edit-note-error">{editErr}</p>}
          <button
            type="button"
            className="ghost sm"
            onClick={saveReplace}
            disabled={busy || !editDraft.trim()}
            data-testid="save-edit-note-button"
          >
            Simpan
          </button>
          <button
            type="button"
            className="ghost sm"
            onClick={cancelEdit}
            disabled={busy}
            data-testid="cancel-edit-note-button"
          >
            Batal
          </button>
        </div>
      ) : (
        hasNotes ? <pre className="notes-view" data-testid="notes-saved">{saved}</pre> : (
          <p className="muted small po-notes-empty">Belum ada catatan produksi.</p>
        )
      )}
      {canAppend && !editing && composerOpen && (
        <div className="note-add">
          <textarea
            value={draft}
            onChange={(e) => { setDraft(e.target.value); if (saveErr) setSaveErr(""); }}
            placeholder="Tambahkan catatan..."
            rows={2}
            data-testid="note-input"
            className="manual-input"
          />
          {saveErr && <p className="error" data-testid="note-save-error">{saveErr}</p>}
          <button className="ghost sm" onClick={saveAppend} disabled={busy || !draft.trim()} data-testid="save-note-button">
            <MessageSquare size={14} /> Simpan catatan
          </button>
        </div>
      )}
    </section>
  );
}
