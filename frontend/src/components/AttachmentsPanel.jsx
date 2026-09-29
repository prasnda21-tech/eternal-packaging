import { useCallback, useEffect, useRef, useState } from "react";
import { Upload, Image as ImageIcon } from "lucide-react";
import api from "../lib/api";
import { dataUrlToBlob } from "../lib/paymentProofOpen";
import { isCrossOriginDocumentUrl } from "../lib/pdfDocument";
import InAppDocumentViewer from "./InAppDocumentViewer";
import { formatDate, formatDisplayDateTime, ROLES } from "../lib/constants";
import { getWorkflowReady } from "../lib/stageState";
import { useAuth } from "../lib/auth";
import ConfirmActionModal from "./ConfirmActionModal";

/**
 * Dua card terpisah:
 * - File Design → actor Designer
 * - Cetak Sample → actor Operator
 * Tidak digabung dalam satu panel.
 */
export default function AttachmentsPanel({ order, user, onUpdated, designSlot = null, sampleSlot = null, section = "all", confirmUpload = false }) {
  const { user: authUser, hasPerm } = useAuth();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState(null);
  const [pdfPreview, setPdfPreview] = useState(null);
  const [previewErr, setPreviewErr] = useState("");
  const previewObjectUrl = useRef(null);
  const [pendingSample, setPendingSample] = useState(null);
  const [pendingDesign, setPendingDesign] = useState(null);
  const [pendingDeleteDesign, setPendingDeleteDesign] = useState(null);
  const [deletingDesign, setDeletingDesign] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");
  const designInput = useRef(null);
  const sampleInput = useRef(null);

  const perms = user?.permissions || [];
  const canUploadDesign = perms.includes("design.upload") || (typeof hasPerm === "function" && hasPerm("design.upload"));
  const canUploadSample = perms.includes("operator.sample_upload") || (typeof hasPerm === "function" && hasPerm("operator.sample_upload"));

  const handleDeleteDesign = async () => {
    if (!pendingDeleteDesign) return;
    setDeletingDesign(true);
    setDeleteErr("");
    try {
      const res = await api.deleteDesignFile(order.id, pendingDeleteDesign.id);
      onUpdated?.(res.order || res);
      setPendingDeleteDesign(null);
    } catch (e) {
      const d = e.response?.data?.detail;
      setDeleteErr(typeof d === "string" ? d : (d?.message || "Gagal menghapus file design"));
    } finally {
      setDeletingDesign(false);
    }
  };


  const handleFile = async (file, kind) => {
    if (!file) return;
    if (kind === "design" && confirmUpload) {
      setPendingDesign({
        file,
        time: formatDisplayDateTime(new Date().toISOString()),
      });
      if (designInput.current) designInput.current.value = "";
      return;
    }
    if (kind === "sample") {
      const ready = getWorkflowReady(order);
      if (!ready.sample_upload) {
        setErr(order?.screener_gate_reason || "Qty Screener harus diisi sebelum Cetak Sample.");
        return;
      }
      setPendingSample({
        file,
        time: formatDisplayDateTime(new Date().toISOString()),
      });
      if (sampleInput.current) sampleInput.current.value = "";
      return;
    }
    await uploadFile(file, kind);
  };

  const uploadFile = async (file, kind) => {
    if (!file) return;
    if (file.size > 4 * 1024 * 1024) {
      setErr("Ukuran file maksimal 4MB");
      return;
    }
    setErr("");
    setBusy(true);
    try {
      const reader = new FileReader();
      reader.onload = async () => {
        try {
          const res = await api.addAttachment(order.id, {
            name: file.name,
            data_url: reader.result,
            kind,
          });
          onUpdated(res.order);
          setPendingSample(null);
          setPendingDesign(null);
        } catch (e) {
          const d = e.response?.data?.detail;
          setErr(typeof d === "string" ? d : (d?.message || "Gagal upload file"));
        } finally {
          setBusy(false);
        }
      };
      reader.readAsDataURL(file);
    } catch (e) {
      setErr("Gagal membaca file");
      setBusy(false);
    }
  };

  const revokePreviewObjectUrl = () => {
    if (previewObjectUrl.current) {
      URL.revokeObjectURL(previewObjectUrl.current);
      previewObjectUrl.current = null;
    }
  };

  const closePreview = () => {
    revokePreviewObjectUrl();
    setPreview(null);
    setPreviewErr("");
  };

  useEffect(() => () => revokePreviewObjectUrl(), []);

  const fileLooksImage = (file, src, blobType) => {
    const name = file?.name || "";
    const ct = String(file?.content_type || blobType || "");
    return (
      (typeof src === "string" && src.startsWith("data:image/")) ||
      ct.startsWith("image/") ||
      /\.(jpe?g|png|gif|webp)$/i.test(name)
    );
  };

  const fileLooksPdf = (file, src, blobType) => {
    const name = file?.name || "";
    const ct = String(file?.content_type || blobType || "");
    return (
      (typeof src === "string" && src.startsWith("data:application/pdf")) ||
      ct.includes("pdf") ||
      /\.pdf$/i.test(name)
    );
  };

  const openFetchedImage = (file, blob) => {
    const objectUrl = URL.createObjectURL(blob);
    revokePreviewObjectUrl();
    previewObjectUrl.current = objectUrl;
    setPreview({ ...file, objectUrl });
  };

  const openInlineSource = (file, url) => {
    if (fileLooksImage(file, url)) {
      setPreview(file);
      return;
    }
    window.open(url, "_blank", "noopener,noreferrer");
  };

  const loadPdfPreview = useCallback(async ({ signal } = {}) => {
    const file = pdfPreview?.file;
    const kind = pdfPreview?.kind;
    const url = file?.data_url || file?.url || "";
    if (url.startsWith("data:")) return dataUrlToBlob(url);
    if (url && !isCrossOriginDocumentUrl(url)) {
      const response = await fetch(url, { credentials: "include", cache: "no-store", signal });
      if (!response.ok) {
        const error = new Error("attachment");
        error.status = response.status;
        error.response = { status: response.status };
        throw error;
      }
      const type = String(response.headers.get("content-type") || file?.content_type || "application/pdf").split(";")[0].trim();
      return new Blob([await response.arrayBuffer()], { type });
    }
    if (!order?.id || !file?.id || (kind !== "design" && kind !== "payment")) {
      const error = new Error("missing");
      error.status = 404;
      throw error;
    }
    return api.getOrderAttachment(order.id, kind, file.id, { signal });
  }, [pdfPreview, order?.id]);

  const openAttachment = async (file, kind) => {
    const url = file?.data_url || file?.url;
    setPreviewErr("");
    if (fileLooksPdf(file, url)) {
      if (url && isCrossOriginDocumentUrl(url)) {
        window.open(url, "_blank", "noopener,noreferrer");
        return;
      }
      if (!url && kind !== "design" && kind !== "payment") {
        setErr("File tidak memiliki data yang dapat dibuka.");
        return;
      }
      setPdfPreview({ file, kind });
      return;
    }
    if (url) {
      openInlineSource(file, url);
      return;
    }
    if (kind !== "design" && kind !== "payment") {
      setErr("File tidak memiliki data yang dapat dibuka.");
      return;
    }
    try {
      const blob = await api.getOrderAttachment(order.id, kind, file.id);
      openFetchedImage(file, blob);
    } catch (e) {
      setPreviewErr("Gagal memuat file. Coba lagi.");
    }
  };

  const designProofStatus = () => {
    if (order?.design_bypassed) return { tone: "pill-bypass", label: "Design Bypass" };
    const uploaded = order?.design_files || [];
    const customerApproved = String(order?.customer_approval?.status || "").trim().toLowerCase() === "approved";
    if (order?.design_status === "Approved" || customerApproved) return { tone: "pill-ok", label: "Design Approved" };
    if (uploaded.length === 0) return { tone: "pill-warn", label: "Belum Ada File Design" };
    return { tone: "pill-warn", label: "Menunggu Approval Design" };
  };

  const renderList = (files, kind) => {
    const proofStatus = kind === "design" ? designProofStatus() : null;
    return (
    <div className="attachments-grid" data-testid={`${kind}-files`}>
      {(files || []).length === 0 && <p className="empty">Belum ada file.</p>}
      {(files || []).map((f) => {
        const uploader = f.uploaded_by_name || f.uploaded_by || ROLES[f.uploaded_by_role] || f.uploaded_by_role || "—";
        const preview = (
          <>
            {f.data_url?.startsWith("data:image") ? (
              <img src={f.data_url} alt={f.name} />
            ) : (
              <div className="file-thumb"><ImageIcon size={kind === "design" ? 36 : 22} /><span>{f.name.split(".").pop().toUpperCase()}</span></div>
            )}
          </>
        );
        return (
        <div
          key={f.id}
          className={"attachment-card" + (kind === "design" ? " design-proof-card" : "")}
          data-testid={`${kind}-file-${f.id}`}
        >
          <div
            className={kind === "design" ? "design-proof-preview" : undefined}
            role="button"
            tabIndex={0}
            onClick={() => openAttachment(f, kind)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") openAttachment(f, kind); }}
            style={{ cursor: "pointer" }}
          >
            {preview}
          </div>
          {kind === "design" ? (
            <div className="attachment-meta design-proof-meta">
              <strong>{f.name}</strong>
              <dl className="design-proof-facts">
                <div>
                  <dt>Uploader</dt>
                  <dd data-testid={`design-file-uploader-${f.id}`}>{uploader}</dd>
                </div>
                <div>
                  <dt>Tanggal</dt>
                  <dd data-testid={`design-file-uploaded-at-${f.id}`}>{formatDate(f.uploaded_at)}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd><span className={`pill sm ${proofStatus.tone}`} data-testid={`design-file-status-${f.id}`}>{proofStatus.label}</span></dd>
                </div>
              </dl>
            </div>
          ) : (
            <div className="attachment-meta" style={{ marginTop: 6 }}>
              <strong>{f.name}</strong>
              <span>{formatDate(f.uploaded_at)}</span>
              <span className="role-tag">{ROLES[f.uploaded_by_role] || f.uploaded_by_role}</span>
            </div>
          )}
          <div className={"attachment-actions" + (kind === "design" ? " design-proof-actions" : "")} style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
            <button
              type="button"
              className="ghost sm"
              onClick={() => openAttachment(f, kind)}
              data-testid={`view-${kind}-file-${f.id}`}
            >
              Lihat
            </button>
            {kind === "design" && canUploadDesign && (
              <button
                type="button"
                className="ghost sm danger"
                onClick={() => {
                  setDeleteErr("");
                  setPendingDeleteDesign(f);
                }}
                data-testid={`delete-design-file-${f.id}`}
                title="Hapus File Design"
              >
                Hapus File Design
              </button>
            )}
          </div>
        </div>
        );
      })}
    </div>
    );
  };


  const showDesign = section === "all" || section === "design";
  const showSample = section === "all" || section === "sample";
  const sampleReady = getWorkflowReady(order).sample_upload;
  const sampleLockReason = order?.screener_gate_reason || "Qty Screener harus diisi sebelum Cetak Sample.";

  return (
    <>
      {showDesign && (
      <section className="panel" data-testid="design-files-panel">
        <header className="panel-head">
          <h3>File Design</h3>
          <span className="pill sm" data-testid="design-owner-badge" style={{ background: "#e6ecf3", color: "#3a4b60" }}>
            Designer
          </span>
          {canUploadDesign && (
            <>
              <input
                type="file"
                ref={designInput}
                hidden
                accept="image/*,application/pdf"
                onChange={(e) => handleFile(e.target.files?.[0], "design")}
                data-testid="design-file-input"
              />
              <button className="primary sm design-upload-btn" onClick={() => designInput.current?.click()} disabled={busy} data-testid="upload-design-button">
                + Upload desain
              </button>
            </>
          )}
        </header>
        <p className="muted small" style={{ marginTop: 0 }}>
          File design. Upload di sini; approval berada di Approval Design.
        </p>
        {renderList(order.design_files, "design")}
        {designSlot}
      </section>
      )}

      {showSample && (
      <section className="panel" data-testid="sample-files-panel">
        <header className="panel-head">
          <h3>Cetak Sample</h3>
          <span className="pill sm" data-testid="sample-owner-badge" style={{ background: "#efe6d3", color: "#7a6f52" }}>
            Operator
          </span>
          {canUploadSample && sampleReady && (
            <>
              <input
                type="file"
                ref={sampleInput}
                hidden
                accept="image/*"
                onChange={(e) => handleFile(e.target.files?.[0], "sample")}
                data-testid="sample-file-input"
              />
              <button className="ghost sm" onClick={() => sampleInput.current?.click()} disabled={busy} data-testid="upload-sample-button">
                <Upload size={14} /> Upload sample
              </button>
            </>
          )}
        </header>
        <p className="muted small" style={{ marginTop: 0 }}>
          Foto/file sample cetak Operator — terpisah dari File Design.
        </p>
        {canUploadSample && !sampleReady && (
          <p className="muted small" data-testid="sample-screener-gate-note">{sampleLockReason}</p>
        )}
        {renderList(order.sample_files, "sample")}
        {sampleSlot}
      </section>
      )}

      {err && <div className="error" data-testid="attachment-error">{err}</div>}
      {previewErr && <div className="error" data-testid="attachment-preview-error">{previewErr}</div>}

      {preview && (
        <div className="lightbox" onClick={closePreview} data-testid="attachment-preview">
          <img src={preview.objectUrl || preview.data_url || preview.url} alt={preview.name} />
        </div>
      )}
      {pdfPreview && (
        <InAppDocumentViewer
          documentKey={`${pdfPreview.kind}:${pdfPreview.file?.id || pdfPreview.file?.name || "pdf"}`}
          title={pdfPreview.file?.name || "PDF"}
          fileName={pdfPreview.file?.name || "dokumen.pdf"}
          contentType={pdfPreview.file?.content_type || "application/pdf"}
          allowDownload={false}
          loadFile={loadPdfPreview}
          onClose={() => setPdfPreview(null)}
          testId={`attachment-pdf-${pdfPreview.file?.id || "file"}`}
        />
      )}
      <ConfirmActionModal
        open={!!pendingSample}
        stage="Konfirmasi Sample"
        name={authUser?.name || user?.name || user?.email}
        time={pendingSample?.time}
        extra={pendingSample?.file ? <p className="muted small">Upload: {pendingSample.file.name}</p> : null}
        busy={busy}
        error={err}
        onCancel={() => { if (!busy) setPendingSample(null); }}
        onConfirm={() => uploadFile(pendingSample?.file, "sample")}
      />
      <ConfirmActionModal
        open={!!pendingDesign}
        title="KONFIRMASI PEKERJAAN"
        stage="Design"
        poNumber={order?.po_number}
        customer={order?.customer_name || order?.customer}
        action="Upload Design"
        name={authUser?.name || user?.name || user?.email}
        time={pendingDesign?.time}
        extra={pendingDesign?.file ? <p className="muted small">Upload: {pendingDesign.file.name}</p> : null}
        busy={busy}
        error={err}
        onCancel={() => { if (!busy) setPendingDesign(null); }}
        onConfirm={() => uploadFile(pendingDesign?.file, "design")}
      />
      {pendingDeleteDesign && (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          data-testid="delete-design-confirm-modal"
          onClick={deletingDesign ? undefined : () => { setPendingDeleteDesign(null); setDeleteErr(""); }}
        >
          <div className="modal-inner confirm-action-inner" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 440 }}>
            <div className="modal-head">
              <h3>Hapus file design ini?</h3>
            </div>
            <div className="confirm-action-body" style={{ padding: "16px 20px" }}>
              <p style={{ margin: "0 0 10px 0", fontSize: 14 }}>
                File yang akan dihapus: <strong className="mono" data-testid="delete-design-filename">{pendingDeleteDesign.name}</strong>
              </p>
              <p className="muted small" style={{ margin: 0, lineHeight: 1.5 }}>
                File attachment akan dihapus dari Design.<br />
                Record revisi dan history tetap dipertahankan.
              </p>
              {deleteErr && <p className="error" data-testid="delete-design-error" style={{ marginTop: 12 }}>{deleteErr}</p>}
            </div>
            <div className="delete-modal-actions">
              <button
                type="button"
                className="ghost"
                onClick={() => { setPendingDeleteDesign(null); setDeleteErr(""); }}
                disabled={deletingDesign}
                data-testid="cancel-delete-design-button"
              >
                Batal
              </button>
              <button
                type="button"
                className="danger"
                onClick={handleDeleteDesign}
                disabled={deletingDesign}
                data-testid="confirm-delete-design-button"
              >
                {deletingDesign ? "Menghapus…" : "Hapus File Design"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>

  );
}
