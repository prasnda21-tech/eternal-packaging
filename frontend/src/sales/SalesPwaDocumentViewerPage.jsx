import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, Download, FileText } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import api from "../lib/api";
import SalesPwaPdfViewer from "./SalesPwaPdfViewer";

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
  const location = useLocation();
  const navigate = useNavigate();
  const directGuard = useRef(false);
  const [viewer, setViewer] = useState({ loading: true, blob: null, url: "", type: "", error: "" });
  const label = LABELS[kind] || "Dokumen";
  const orderPath = `/sales/po/${encodeURIComponent(id)}`;

  useEffect(() => {
    if (location.state?.salesDocumentFrom === orderPath) return undefined;

    directGuard.current = true;
    window.history.pushState(
      { ...(window.history.state || {}), salesDocumentGuard: true },
      "",
      window.location.href
    );
    const handlePopState = () => navigate(orderPath, { replace: true });
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [location.state, navigate, orderPath]);

  const goBack = useCallback(() => {
    if (directGuard.current) {
      window.history.back();
      return;
    }
    navigate(-1);
  }, [navigate]);

  useEffect(() => {
    let active = true;
    let objectUrl = "";

    setViewer({ loading: true, blob: null, url: "", type: "", error: "" });
    api.salesPwaDocumentFile(id, kind, documentId)
      .then(({ blob, contentType }) => {
        if (!active) return;
        const type = String(contentType || blob?.type || fallbackType(kind)).split(";")[0].trim();
        const typedBlob = blob?.type ? blob : new Blob([blob], { type });
        objectUrl = URL.createObjectURL(typedBlob);
        setViewer({ loading: false, blob: typedBlob, url: objectUrl, type, error: "" });
      })
      .catch((error) => {
        if (!active) return;
        const status = error?.response?.status;
        const detail = error?.response?.data?.detail;
        setViewer({
          loading: false,
          blob: null,
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
        <button type="button" onClick={goBack} className="spwa-back" data-testid="sales-document-back">
          <ChevronLeft size={16} /> Kembali
        </button>
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
        {!viewer.loading && !viewer.error && viewer.blob && isPdf && !isImage && (
          <SalesPwaPdfViewer blob={viewer.blob} label={label} />
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
