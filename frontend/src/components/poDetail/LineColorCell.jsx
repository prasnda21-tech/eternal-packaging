import { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { salesOrderLineColor } from "../../lib/salesOrderLineColor";

function lineStableId(line) {
  const lineId = String(line?.line_id || "").trim();
  if (lineId) return lineId;
  return String(line?.accurate_line_id || "").trim();
}

function colorSaveError(err) {
  const status = err?.response?.status;
  const message = formatApiError(err, "Gagal menyimpan warna");
  if (status === 409) {
    return message || "Data berubah. Muat ulang halaman lalu coba lagi.";
  }
  return message;
}

export default function LineColorCell({
  orderId,
  line,
  lineIdx,
  canEdit,
  active,
  onActivate,
  onDeactivate,
  onSaved,
}) {
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const sessionRef = useRef(null);
  const genRef = useRef(0);
  const onDeactivateRef = useRef(onDeactivate);
  onDeactivateRef.current = onDeactivate;
  const stableId = lineStableId(line);

  useEffect(() => {
    if (!active) return;
    const session = sessionRef.current;
    if (!session) return;
    if (session.lineIdx !== lineIdx || session.stableId !== stableId) {
      sessionRef.current = null;
      genRef.current += 1;
      setSaving(false);
      onDeactivateRef.current();
    }
  }, [active, lineIdx, stableId]);

  function openEditor() {
    sessionRef.current = { lineIdx, stableId };
    setDraft(salesOrderLineColor(line));
    setError("");
    setSaving(false);
    onActivate(lineIdx);
  }

  function cancelEditor() {
    genRef.current += 1;
    sessionRef.current = null;
    setSaving(false);
    setError("");
    onDeactivate();
  }

  async function saveEditor() {
    if (saving) return;
    const ticket = genRef.current + 1;
    genRef.current = ticket;
    setSaving(true);
    setError("");
    try {
      const res = await api.updateSalesOrderLineColor(orderId, lineIdx, {
        color: draft,
        line_id: stableId || null,
      });
      if (ticket !== genRef.current) return;
      if (!res?.order) {
        throw new Error("Respons pesanan tidak lengkap");
      }
      sessionRef.current = null;
      onSaved(res.order);
      onDeactivate();
    } catch (err) {
      if (ticket !== genRef.current) return;
      setError(colorSaveError(err));
      setSaving(false);
    }
  }

  if (active) {
    return (
      <div className="li-color-editor">
        <input
          type="text"
          value={draft}
          disabled={saving}
          aria-label="Warna"
          data-testid={`line-color-input-${lineIdx}`}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button
          type="button"
          className="primary sm"
          disabled={saving}
          data-testid={`line-color-save-${lineIdx}`}
          onClick={saveEditor}
        >
          {saving ? "Menyimpan" : "Simpan"}
        </button>
        <button
          type="button"
          className="ghost sm"
          disabled={saving}
          data-testid={`line-color-cancel-${lineIdx}`}
          onClick={cancelEditor}
        >
          Batal
        </button>
        {error ? (
          <p className="li-color-error" data-testid={`line-color-error-${lineIdx}`}>{error}</p>
        ) : null}
      </div>
    );
  }

  return (
    <span className="li-color-view">
      <span className="li-color-value" data-testid={`line-color-value-${lineIdx}`}>
        {salesOrderLineColor(line) || "-"}
      </span>
      {canEdit ? (
        <button
          type="button"
          className="ghost sm li-color-edit-btn"
          aria-label="Ubah warna"
          data-testid={`line-color-edit-${lineIdx}`}
          onClick={openEditor}
        >
          <Pencil size={14} />
        </button>
      ) : null}
    </span>
  );
}
