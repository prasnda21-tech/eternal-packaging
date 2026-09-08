import { useRef, useState } from "react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { getWorkflowReady } from "../../lib/stageState";
import { compressImage } from "../../lib/imageCompress";

export default function MobileSampleWorkspace({ order, canEdit, onUpdated }) {
  const ready = getWorkflowReady(order);
  const locked = ready.sample_upload !== true || !canEdit;
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const files = order?.sample_files || [];

  const upload = async (file) => {
    if (!file) return;
    if (locked) {
      setErr(order?.screener_gate_reason || "Upload sampel belum tersedia.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const compressed = await compressImage(file);
      const blob = compressed.file;
      if (blob.size > 4 * 1024 * 1024) {
        setErr("Ukuran file maksimal 4MB");
        return;
      }
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      const res = await api.addAttachment(order.id, { kind: "sample", name: blob.name || file.name, data_url: dataUrl });
      onUpdated(res.order);
    } catch (e) {
      setErr(formatApiError(e, "Gagal mengunggah sampel."));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <section data-testid="mobile-ws-sample">
      <div className="mop-ws-header"><h2>UPLOAD SAMPEL CETAK</h2></div>
      {locked && <p className="mop-card-meta">{order?.screener_gate_reason || "Upload sampel menunggu Screener."}</p>}
      {err && <div className="mop-error">{err}</div>}
      <input
        ref={inputRef}
        type="file"
        accept="image/*,application/pdf"
        capture="environment"
        hidden
        onChange={(e) => upload(e.target.files?.[0])}
        data-testid="mobile-sample-input"
      />
      <button type="button" className="mop-btn-primary" disabled={locked || busy} onClick={() => inputRef.current?.click()}>
        {busy ? "Mengunggah..." : "Ambil / Pilih Sampel"}
      </button>
      <div style={{ marginTop: 12 }}>
        {files.map((f, i) => (
          <div className="mop-file" key={f.id || i}>
            <strong>{f.name || `Sampel ${i + 1}`}</strong>
          </div>
        ))}
        {!files.length && <p className="mop-empty">Belum ada sampel.</p>}
      </div>
    </section>
  );
}
