import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Eye, FileText, Trash2, Upload } from "lucide-react";
import api from "../lib/api";
import { errDetail, formatPurchaseDateTime } from "../lib/purchaseUi";

/**
 * Reusable Dokumen section for purchasing transactions.
 * Inline (not a separate popup): document name + upload + file list.
 */
export default function PurchaseDocuments({
  entityType,
  entityId,
  canEdit = false,
  hint = "",
  testId = "purchase-documents",
}) {
  const [docs, setDocs] = useState([]);
  const [documentName, setDocumentName] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    if (!entityType || !entityId) {
      setDocs([]);
      return;
    }
    setLoading(true);
    setErr("");
    try {
      const res = await api.listPurchaseDocuments({ entity_type: entityType, entity_id: entityId });
      setDocs(res.documents || []);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setLoading(false);
    }
  }, [entityType, entityId]);

  useEffect(() => {
    load();
  }, [load]);

  const onPick = async (e) => {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = "";
    if (!file || !entityId || busy) return;
    setBusy(true);
    setErr("");
    setOk("");
    try {
      const res = await api.uploadPurchaseDocument({
        entity_type: entityType,
        entity_id: entityId,
        document_name: documentName || file.name,
        file,
      });
      setOk(`Dokumen "${res.document?.document_name || file.name}" diunggah.`);
      setDocumentName("");
      await load();
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const openDoc = async (doc, { download = false } = {}) => {
    try {
      const blob = await api.fetchPurchaseDocumentBlob(doc.id, { download });
      const url = URL.createObjectURL(blob);
      if (download) {
        const a = document.createElement("a");
        a.href = url;
        a.download = doc.original_filename || doc.document_name || "dokumen";
        document.body.appendChild(a);
        a.click();
        a.remove();
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (ex) {
      setErr(errDetail(ex));
    }
  };

  const removeDoc = async (doc) => {
    if (!canEdit || busy) return;
    const label = doc.document_name || doc.original_filename || "dokumen";
    if (!window.confirm(`Hapus dokumen "${label}"?`)) return;
    setBusy(true);
    setErr("");
    try {
      await api.deletePurchaseDocument(doc.id);
      setOk(`Dokumen "${label}" dihapus.`);
      await load();
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  if (!entityId) {
    return (
      <section className="purchase-documents" data-testid={testId}>
        <h4 className="purchase-documents-title">Dokumen</h4>
        <p className="muted small">Simpan transaksi terlebih dahulu untuk mengunggah dokumen.</p>
      </section>
    );
  }

  return (
    <section className="purchase-documents" data-testid={testId}>
      <h4 className="purchase-documents-title">Dokumen</h4>
      {hint ? <p className="muted small purchase-documents-hint">{hint}</p> : null}
      {err ? (
        <div className="purchase-error-panel" role="alert" style={{ marginBottom: 8 }}>
          <span>{err}</span>
        </div>
      ) : null}
      {ok ? <p className="purchase-inline-ok" data-testid={`${testId}-ok`}>{ok}</p> : null}

      {canEdit ? (
        <div className="purchase-documents-upload" data-testid={`${testId}-upload`}>
          <label className="purchase-documents-name-label">
            <span>Nama Dokumen</span>
            <input
              type="text"
              value={documentName}
              onChange={(e) => setDocumentName(e.target.value)}
              placeholder="Contoh: Nota penawaran / Bukti transfer"
              data-testid={`${testId}-name`}
            />
          </label>
          <button
            type="button"
            className="ghost"
            disabled={busy}
            data-testid={`${testId}-btn-upload`}
            onClick={() => fileRef.current?.click()}
          >
            <Upload size={14} /> Unggah Dokumen
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            capture="environment"
            style={{ display: "none" }}
            data-testid={`${testId}-file`}
            onChange={onPick}
          />
        </div>
      ) : null}

      <div className="purchase-documents-list" data-testid={`${testId}-list`}>
        {loading && <p className="muted small">Memuat dokumen…</p>}
        {!loading && docs.length === 0 && (
          <p className="muted small">Belum ada dokumen.</p>
        )}
        {!loading && docs.map((doc) => (
          <div key={doc.id} className="purchase-document-row" data-testid={`${testId}-row-${doc.id}`}>
            <div className="purchase-document-main">
              <FileText size={16} />
              <div>
                <div className="purchase-document-name">{doc.document_name}</div>
                <div className="muted small">
                  {doc.original_filename}
                  {" · "}
                  {doc.uploaded_by_name || "Tidak tersedia"}
                  {" · "}
                  {formatPurchaseDateTime(doc.uploaded_at)}
                </div>
              </div>
            </div>
            <div className="purchase-document-actions">
              <button type="button" className="ghost sm" title="Buka / preview" onClick={() => openDoc(doc)} data-testid={`${testId}-preview-${doc.id}`}>
                <Eye size={14} />
              </button>
              <button type="button" className="ghost sm" title="Unduh" onClick={() => openDoc(doc, { download: true })} data-testid={`${testId}-download-${doc.id}`}>
                <Download size={14} />
              </button>
              {canEdit ? (
                <button type="button" className="ghost sm danger" title="Hapus" disabled={busy} onClick={() => removeDoc(doc)} data-testid={`${testId}-delete-${doc.id}`}>
                  <Trash2 size={14} />
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
