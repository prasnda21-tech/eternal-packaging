import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, Download, FileText } from "lucide-react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { fetchSuratJalanBlob } from "../lib/suratJalan";
import SalesPwaPdfViewer from "../sales/SalesPwaPdfViewer";

function safeMobileReturn(raw) {
  const value = String(raw || "").trim();
  if (!value.startsWith("/m/") || value.startsWith("//")) return "";
  return value;
}

export default function MobileSuratJalanViewerPage() {
  const { id, fileId } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const directGuard = useRef(false);
  const workspace = params.get("ws") === "delivery" ? "delivery" : "loading";
  const requestedReturn = safeMobileReturn(params.get("return_to"));
  const fallback = `/m/orders/${encodeURIComponent(id)}?ws=${workspace}`;
  const returnTarget = requestedReturn || fallback;
  const [viewer, setViewer] = useState({ loading: true, blob: null, url: "", error: "" });

  useEffect(() => {
    if (requestedReturn) return undefined;
    directGuard.current = true;
    window.history.pushState(
      { ...(window.history.state || {}), mobileSjDocumentGuard: true },
      "",
      window.location.href,
    );
    const handlePopState = () => navigate(fallback, { replace: true });
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [fallback, navigate, requestedReturn]);

  const goBack = useCallback(() => {
    if (directGuard.current) {
      window.history.back();
      return;
    }
    navigate(returnTarget, { replace: true });
  }, [navigate, returnTarget]);

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    setViewer({ loading: true, blob: null, url: "", error: "" });
    fetchSuratJalanBlob(id, fileId, { filename: "surat-jalan.pdf" })
      .then((blob) => {
        if (!active) return;
        const typedBlob = blob?.type
          ? blob
          : new Blob([blob], { type: "application/pdf" });
        objectUrl = URL.createObjectURL(typedBlob);
        setViewer({ loading: false, blob: typedBlob, url: objectUrl, error: "" });
      })
      .catch((error) => {
        if (!active) return;
        setViewer({
          loading: false,
          blob: null,
          url: "",
          error: error?.message || "Surat Jalan gagal dimuat.",
        });
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileId, id]);

  const filename = useMemo(() => {
    const safe = String(fileId || "surat-jalan").replace(/[^a-zA-Z0-9._-]+/g, "-");
    return `surat-jalan-${safe}.pdf`;
  }, [fileId]);

  return (
    <div className="mop-sj-viewer" data-testid="mobile-sj-document-viewer">
      <header className="mop-sj-viewer-head">
        <button type="button" onClick={goBack} className="mop-sj-viewer-back" data-testid="mobile-sj-document-back">
          <ChevronLeft size={18} aria-hidden="true" />
          Kembali
        </button>
        <div>
          <p>Dokumen Operasional</p>
          <h1>Surat Jalan</h1>
        </div>
        {viewer.url ? (
          <a className="mop-sj-viewer-download" href={viewer.url} download={filename}>
            <Download size={17} aria-hidden="true" />
            Unduh
          </a>
        ) : <span />}
      </header>
      <main className="mop-sj-viewer-body">
        {viewer.loading ? <div className="mop-sj-viewer-state">Memuat Surat Jalan...</div> : null}
        {viewer.error ? <div className="mop-sj-viewer-state is-error">{viewer.error}</div> : null}
        {!viewer.loading && !viewer.error && viewer.blob ? (
          <SalesPwaPdfViewer blob={viewer.blob} label="Surat Jalan" />
        ) : null}
        {!viewer.loading && !viewer.error && !viewer.blob ? (
          <div className="mop-sj-viewer-state"><FileText size={28} />Dokumen tidak tersedia.</div>
        ) : null}
      </main>
    </div>
  );
}

export { safeMobileReturn };
