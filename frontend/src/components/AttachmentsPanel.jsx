import { useRef, useState } from "react";
import { Upload, Image as ImageIcon } from "lucide-react";
import api from "../lib/api";
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
export default function AttachmentsPanel({ order, user, onUpdated, designSlot = null, sampleSlot = null, section = "all" }) {
  const { user: authUser } = useAuth();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState(null);
  const [pendingSample, setPendingSample] = useState(null);
  const designInput = useRef(null);
  const sampleInput = useRef(null);

  const perms = user?.permissions || [];
  const canUploadDesign = perms.includes("design.upload");
  const canUploadSample = perms.includes("operator.sample_upload");

  const handleFile = async (file, kind) => {
    if (!file) return;
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

  const openAttachment = (file) => {
    const url = file?.data_url || file?.url;

    if (!url) {
      setErr("File tidak memiliki data yang dapat dibuka.");
      return;
    }

    const isImage =
      url.startsWith("data:image/") ||
      /\.(jpe?g|png|gif|webp)$/i.test(file?.name || "");

    const isPdf =
      url.startsWith("data:application/pdf") ||
      /\.pdf$/i.test(file?.name || "");

    if (isImage) {
      setPreview(file);
      return;
    }

    if (isPdf && url.startsWith("data:application/pdf")) {
      try {
        const commaIndex = url.indexOf(",");
        if (commaIndex < 0) throw new Error("Invalid PDF data URL");

        const encoded = url.slice(commaIndex + 1);
        const binary = atob(encoded);
        const bytes = new Uint8Array(binary.length);

        for (let i = 0; i < binary.length; i += 1) {
          bytes[i] = binary.charCodeAt(i);
        }

        const blob = new Blob([bytes], { type: "application/pdf" });
        const blobUrl = URL.createObjectURL(blob);

        const opened = window.open(blobUrl, "_blank");

        if (!opened) {
          setErr("Browser memblokir tab PDF. Izinkan pop-up untuk situs ini.");
        }

        setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      } catch (e) {
        console.error("Gagal membuka PDF attachment:", e);
        setErr("PDF gagal dibuka. Silakan coba kembali.");
      }

      return;
    }

    window.open(url, "_blank", "noopener,noreferrer");
  };

  const renderList = (files, kind) => (
    <div className="attachments-grid" data-testid={`${kind}-files`}>
      {(files || []).length === 0 && <p className="empty">Belum ada file.</p>}
      {(files || []).map((f) => (
        <button
          key={f.id}
          className="attachment-card"
          type="button"
          onClick={() => openAttachment(f)}
          data-testid={`${kind}-file-${f.id}`}
        >
          {f.data_url?.startsWith("data:image") ? (
            <img src={f.data_url} alt={f.name} />
          ) : (
            <div className="file-thumb"><ImageIcon size={22} /><span>{f.name.split(".").pop().toUpperCase()}</span></div>
          )}
          <div className="attachment-meta">
            <strong>{f.name}</strong>
            <span>{formatDate(f.uploaded_at)}</span>
            <span className="role-tag">{ROLES[f.uploaded_by_role] || f.uploaded_by_role}</span>
          </div>
        </button>
      ))}
    </div>
  );

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
              <button className="ghost sm" onClick={() => designInput.current?.click()} disabled={busy} data-testid="upload-design-button">
                <Upload size={14} /> Upload desain
              </button>
            </>
          )}
        </header>
        <p className="muted small" style={{ marginTop: 0 }}>
          File design. Upload di sini; approval berada di card Approval Design.
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

      {preview && (
        <div className="lightbox" onClick={() => setPreview(null)} data-testid="attachment-preview">
          <img src={preview.data_url || preview.url} alt={preview.name} />
        </div>
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
    </>
  );
}
