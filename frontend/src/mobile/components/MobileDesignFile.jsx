import { useCallback, useRef, useState } from "react";
import api from "../../lib/api";
import { dataUrlToBlob } from "../../lib/paymentProofOpen";
import { isCrossOriginDocumentUrl } from "../../lib/pdfDocument";
import { browserSafeOpenTarget, designFileMeta, latestDesignFile, openBrowserSafeFile } from "../../lib/mobileFileOpen";
import InAppDocumentViewer from "../../components/InAppDocumentViewer";

const MISSING_ID_MESSAGE = "File desain tidak dapat dibuka. ID tidak tersedia.";
const MISSING_FILE_MESSAGE = "File atau isi desain tidak tersedia.";

function abortError() {
  const error = new Error("aborted");
  error.name = "AbortError";
  return error;
}

function isCanceledError(error) {
  return error?.name === "AbortError" || error?.name === "CanceledError" || error?.code === "ERR_CANCELED";
}

function statusOf(error) {
  return Number(error?.response?.status || error?.status || 0);
}

function designFileIsPdf(file) {
  if (!file) return false;
  const name = String(file.name || file.filename || "");
  const type = String(file.content_type || file.mime || "").toLowerCase();
  return type.includes("pdf") || /\.pdf$/i.test(name);
}

function sourceKeyFor(orderId, fileId, file, storagePath, dataUrl, href) {
  const name = String(file?.name || file?.filename || "");
  const type = String(file?.content_type || file?.mime || "");
  const hrefKey = href.startsWith("data:") ? `data:${href.length}` : href;
  const dataMarker = dataUrl ? `data:${dataUrl.length}:${dataUrl.slice(-24)}` : "";
  return [orderId, fileId, storagePath, dataMarker, hrefKey, name, type].join("|");
}

export default function MobileDesignFile({ order }) {
  const file = latestDesignFile(order);
  const meta = designFileMeta(order, file);
  const [pdfOpen, setPdfOpen] = useState(false);
  const [loadError, setLoadError] = useState("");
  const target = file ? browserSafeOpenTarget(file) : { ok: false };
  const storagePath = file?.storage_path || "";
  const dataUrl = String(file?.data_url || "");
  const href = target.href || "";
  const externalPdf = target.kind === "pdf" && href && isCrossOriginDocumentUrl(href);
  const orderId = String(order?.id ?? "").trim();
  const fileId = String(file?.id ?? "").trim();
  const metadataPdf = Boolean(file) && !target.ok && designFileIsPdf(file);
  const metadataById = Boolean(file) && !storagePath && !dataUrl.startsWith("data:") && !href && orderId && fileId;
  const inlinePdf = (target.kind === "pdf" && !externalPdf) || metadataPdf || metadataById;
  const sourceKey = sourceKeyFor(orderId, fileId, file, storagePath, dataUrl, href);
  const epochRef = useRef(0);
  const [seenKey, setSeenKey] = useState(sourceKey);
  if (seenKey !== sourceKey) {
    epochRef.current += 1;
    setSeenKey(sourceKey);
    if (loadError) setLoadError("");
  }

  const loadDesign = useCallback(({ signal } = {}) => {
    const epoch = epochRef.current;
    const current = () => {
      if (signal?.aborted || epoch !== epochRef.current) throw abortError();
    };
    const finish = (error) => {
      if (signal?.aborted || isCanceledError(error) || epoch !== epochRef.current) {
        throw isCanceledError(error) ? error : abortError();
      }
      if (statusOf(error) === 404) {
        setLoadError(MISSING_FILE_MESSAGE);
        setPdfOpen(false);
        throw abortError();
      }
      throw error;
    };
    const run = async () => {
      current();
      if (storagePath) return api.fetchStoredFileBlob(storagePath, { signal });
      if (dataUrl.startsWith("data:")) return dataUrlToBlob(dataUrl);
      if (href && !isCrossOriginDocumentUrl(href)) {
        const response = await fetch(href, { credentials: "include", cache: "no-store", signal });
        current();
        if (!response.ok) {
          const error = new Error("design");
          error.status = response.status;
          error.response = { status: response.status };
          throw error;
        }
        const type = String(response.headers.get("content-type") || "application/pdf").split(";")[0].trim();
        return new Blob([await response.arrayBuffer()], { type });
      }
      if (orderId && fileId) {
        return api.getOrderAttachment(orderId, "design", fileId, { signal });
      }
      if (designFileIsPdf(file)) {
        if (!signal?.aborted && epoch === epochRef.current) {
          setLoadError(MISSING_ID_MESSAGE);
          setPdfOpen(false);
        }
        throw abortError();
      }
      const error = new Error("missing");
      error.status = 404;
      throw error;
    };
    return run().then((blob) => {
      current();
      return blob;
    }).catch(finish);
  }, [storagePath, dataUrl, href, file, orderId, fileId]);

  const open = () => {
    setLoadError("");
    if (inlinePdf) {
      if (metadataPdf && (!orderId || !fileId)) {
        setLoadError(MISSING_ID_MESSAGE);
        return;
      }
      setPdfOpen(true);
      return;
    }
    const result = openBrowserSafeFile(file);
    if (!result.ok || result.opened === false) {
      window.alert(result.reason || "File tidak dapat dibuka.");
    }
  };
  return (
    <div className="mop-file" data-testid="mobile-design-file">
      <strong>File Desain</strong>
      <p className="mop-card-title">{meta.name}</p>
      <p className="mop-card-meta">
        <span>{meta.type || "File"}</span>
        <span>{meta.approval}</span>
      </p>
      <button type="button" className="mop-btn-ghost" style={{ width: "100%", marginTop: 8 }} onClick={open} disabled={!file} data-testid="mobile-lihat-desain">
        Lihat Desain
      </button>
      {loadError ? (
        <p className="mop-error" role="alert" data-testid="mobile-design-open-error">{loadError}</p>
      ) : null}
      {externalPdf ? (
        <p className="mop-muted" data-testid="mobile-design-pdf-external">PDF dari origin lain tetap dibuka di browser.</p>
      ) : null}
      {pdfOpen && file && (
        <InAppDocumentViewer
          documentKey={sourceKey}
          title={meta.name || "Desain"}
          fileName={file.name || file.filename || "desain.pdf"}
          contentType={file.content_type || file.mime || "application/pdf"}
          allowDownload={false}
          loadFile={loadDesign}
          onClose={() => setPdfOpen(false)}
          testId="mobile-design-pdf-viewer"
        />
      )}
    </div>
  );
}
