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

function filenameFromDisposition(disposition) {
  const value = String(disposition || "");
  const encoded = /filename\*\s*=\s*[^']*''([^;]+)/i.exec(value);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ""));
    } catch {
      /* keep the plain filename below */
    }
  }
  const quoted = /filename\s*=\s*"([^"]+)"/i.exec(value);
  if (quoted) return quoted[1].trim();
  const plain = /filename\s*=\s*([^;]+)/i.exec(value);
  return plain ? plain[1].trim() : "";
}

function loadErrorMessage(error) {
  const status = error?.response?.status;
  if (status === 401) return "Sesi berakhir. Masuk kembali untuk membuka dokumen.";
  if (status === 403) return "Anda tidak memiliki akses ke dokumen ini.";
  if (status === 404) return "Dokumen tidak ditemukan.";
  return "Dokumen gagal dimuat.";
}

function canceled(error, signal) {
  return signal?.aborted || error?.code === "ERR_CANCELED" || error?.name === "CanceledError";
}

function DocumentActions({ onRetry, url, filename, allowOriginal }) {
  return (
    <div className="spwa-viewer-actions">
      <button type="button" onClick={onRetry} data-testid="sales-document-retry">Coba lagi</button>
      {allowOriginal && url ? (
        <a href={url} download={filename} data-testid="sales-document-open-original">Buka PDF asli</a>
      ) : null}
    </div>
  );
}

export default function SalesPwaDocumentViewerPage() {
  const { id, kind, documentId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const directGuard = useRef(false);
  const requestRef = useRef(0);
  const signatureRef = useRef("");
  const [attempt, setAttempt] = useState(0);
  const [pdfError, setPdfError] = useState("");
  const [viewer, setViewer] = useState({
    loading: true, blob: null, url: "", type: "", error: "", filename: "",
  });
  const label = LABELS[kind] || "Dokumen";
  const orderPath = `/sales/po/${encodeURIComponent(id)}`;
  const signature = `${id}|${kind}|${documentId}|${attempt}`;
  if (signatureRef.current !== signature) {
    signatureRef.current = signature;
    requestRef.current += 1;
  }
  const requestId = requestRef.current;

  useEffect(() => {
    setPdfError("");
  }, [signature]);

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

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    const currentId = requestId;
    const controller = new AbortController();
    let objectUrl = "";

    setViewer({ loading: true, blob: null, url: "", type: "", error: "", filename: "" });
    api.salesPwaDocumentFile(id, kind, documentId, { signal: controller.signal })
      .then(({ blob, contentType, disposition }) => {
        if (currentId !== requestRef.current) return;
        const type = String(contentType || blob?.type || fallbackType(kind)).split(";")[0].trim();
        const named = filenameFromDisposition(disposition);
        if (!blob || blob.size === 0) {
          setViewer({
            loading: false,
            blob: null,
            url: "",
            type,
            error: "Dokumen kosong.",
            filename: named,
          });
          return;
        }
        const typedBlob = blob?.type ? blob : new Blob([blob], { type });
        objectUrl = URL.createObjectURL(typedBlob);
        setViewer({
          loading: false,
          blob: typedBlob,
          url: objectUrl,
          type,
          error: "",
          filename: named,
        });
      })
      .catch((error) => {
        if (currentId !== requestRef.current || canceled(error, controller.signal)) return;
        setViewer({
          loading: false,
          blob: null,
          url: "",
          type: "",
          error: loadErrorMessage(error),
          filename: "",
        });
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id, kind, documentId, requestId]);

  const filename = useMemo(() => {
    if (viewer.filename) return viewer.filename;
    const safe = String(documentId || "dokumen").replace(/[^a-zA-Z0-9._-]+/g, "-");
    const ext = kind === "proof" ? "jpg" : "pdf";
    return `${kind || "dokumen"}-${safe}.${ext}`;
  }, [kind, documentId, viewer.filename]);

  const displayName = viewer.filename || label;
  const isImage = viewer.type.startsWith("image/");
  const isPdf = viewer.type === "application/pdf" || (kind !== "proof" && !isImage);
  const showPdf = !viewer.loading && !viewer.error && !pdfError && viewer.blob && isPdf && !isImage;
  const failure = viewer.error || pdfError;

  return (
    <div className="spwa-viewer" data-testid="sales-document-viewer">
      <header className="spwa-viewer-head">
        <button type="button" onClick={goBack} className="spwa-back" data-testid="sales-document-back">
          <ChevronLeft size={16} /> Kembali
        </button>
        <div>
          <p className="spwa-kicker">DOKUMEN PO</p>
          <h1 title={displayName}>{displayName}</h1>
        </div>
        {viewer.url ? (
          <a className="spwa-viewer-download" href={viewer.url} download={filename} data-testid="sales-document-download">
            <Download size={15} /> Unduh
          </a>
        ) : <span />}
      </header>

      <main className="spwa-viewer-body">
        {viewer.loading && <div className="spwa-viewer-state">Memuat dokumen...</div>}
        {!viewer.loading && failure && (
          <div className="spwa-viewer-state is-error" data-testid="sales-document-error">
            <p>{failure}</p>
            <DocumentActions
              onRetry={retry}
              url={viewer.url}
              filename={filename}
              allowOriginal={Boolean(viewer.url) && isPdf}
            />
          </div>
        )}
        {!viewer.loading && !failure && viewer.url && isImage && (
          <img className="spwa-viewer-image" src={viewer.url} alt={displayName} />
        )}
        {showPdf && (
          <SalesPwaPdfViewer
            key={signature}
            blob={viewer.blob}
            label={displayName}
            mode="windowed"
            onError={setPdfError}
          />
        )}
        {!viewer.loading && !failure && viewer.url && !isImage && !isPdf && (
          <div className="spwa-viewer-state">
            <FileText size={28} />
            Preview tidak tersedia. Gunakan tombol Unduh.
          </div>
        )}
      </main>
    </div>
  );
}
