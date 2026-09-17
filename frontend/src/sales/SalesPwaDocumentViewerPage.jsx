import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, Download, FileText } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import api from "../lib/api";

const LABELS = {
  po: "Purchase Order",
  sj: "Surat Jalan",
  invoice: "Invoice",
  proof: "Bukti Kirim",
};

function fallbackType(kind) {
  return kind === "proof" ? "image/jpeg" : "application/pdf";
}

export default function SalesPwaDocumentViewerPage() {
  const { id, kind, documentId } = useParams();
  const [viewer, setViewer] = useState({ loading: true, url: "", type: "", error: "" });
  const label = LABELS[kind] || "Dokumen";

  useEffect(() => {
    let active = true;
    let objectUrl = "";

    setViewer({ loading: true, url: "", type: "", error: "" });
    api.salesPwaDocumentFile(id, kind, documentId)
      .then(({ blob, contentType }) => {
        if (!active) return;
        const type = String(contentType || blob?.type || fallbackType(kind)).split(";")[0].trim();
        const typedBlob = blob?.type ? blob : new Blob([blob], { type });
        objectUrl = URL.createObjectURL(typedBlob);
        setViewer({ loading: false, url: objectUrl, type, error: "" });
      })
      .catch((error) => {
        if (!active) return;
        const status = error?.response?.status;
        const detail = error?.response?.data?.detail;
        setViewer({
          loading: false,
          url: "",
          type: "",
          error: status === 404 ? "Dokumen tidak ditemukan." : (detail || "Dokumen gagal dimuat."),
        });
      });

    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id, kind, documentId]);

  const filename = useMemo(() => {
    const safe = String(documentId || "dokumen").replace(/[^a-zA-Z0-9._-]+/g, "-");
    const ext = kind === "proof" ? "jpg" : "pdf";
    return `${kind || "dokumen"}-${safe}.${ext}`;
  }, [kind, documentId]);

  const isImage = viewer.type.startsWith("image/");
  const isPdf = viewer.type === "application/pdf" || kind !== "proof";

  return (
    <div className="spwa-viewer" data-testid="sales-document-viewer">
      <header className="spwa-viewer-head">
        <Link to={`/sales/po/${encodeURIComponent(id)}`} className="spwa-back" data-testid="sales-document-back">
          <ChevronLeft size={16} /> Kembali
        </Link>
        <div>
          <p className="spwa-kicker">DOKUMEN PO</p>
          <h1>{label}</h1>
        </div>
        {viewer.url ? (
          <a className="spwa-viewer-download" href={viewer.url} download={filename}>
            <Download size={15} /> Unduh
          </a>
        ) : <span />}
      </header>

      <main className="spwa-viewer-body">
        {viewer.loading && <div className="spwa-viewer-state">Memuat dokumen...</div>}
        {viewer.error && <div className="spwa-viewer-state is-error">{viewer.error}</div>}
        {!viewer.loading && !viewer.error && viewer.url && isImage && (
          <img className="spwa-viewer-image" src={viewer.url} alt={label} />
        )}
        {!viewer.loading && !viewer.error && viewer.url && isPdf && !isImage && (
          <iframe
            className="spwa-viewer-frame"
            src={viewer.url}
            title={label}
            data-testid="sales-document-frame"
          />
        )}
        {!viewer.loading && !viewer.error && viewer.url && !isImage && !isPdf && (
          <div className="spwa-viewer-state">
            <FileText size={28} />
            Preview tidak tersedia. Gunakan tombol Unduh.
          </div>
        )}
      </main>
    </div>
  );
}
