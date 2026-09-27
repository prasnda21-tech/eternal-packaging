import { useCallback, useState } from "react";
import api from "../../lib/api";
import { dataUrlToBlob } from "../../lib/paymentProofOpen";
import { isCrossOriginDocumentUrl } from "../../lib/pdfDocument";
import { browserSafeOpenTarget, designFileMeta, latestDesignFile, openBrowserSafeFile } from "../../lib/mobileFileOpen";
import InAppDocumentViewer from "../../components/InAppDocumentViewer";

export default function MobileDesignFile({ order }) {
  const file = latestDesignFile(order);
  const meta = designFileMeta(order, file);
  const [pdfOpen, setPdfOpen] = useState(false);
  const target = file ? browserSafeOpenTarget(file) : { ok: false };
  const storagePath = file?.storage_path || "";
  const dataUrl = String(file?.data_url || "");
  const href = target.href || "";
  const externalPdf = target.kind === "pdf" && href && isCrossOriginDocumentUrl(href);

  const loadDesign = useCallback(({ signal } = {}) => {
    if (storagePath) return api.fetchStoredFileBlob(storagePath, { signal });
    if (dataUrl.startsWith("data:")) return Promise.resolve(dataUrlToBlob(dataUrl));
    if (href && !isCrossOriginDocumentUrl(href)) {
      return fetch(href, { credentials: "include", cache: "no-store", signal }).then(async (response) => {
        if (!response.ok) {
          const error = new Error("design");
          error.status = response.status;
          error.response = { status: response.status };
          throw error;
        }
        const type = String(response.headers.get("content-type") || "application/pdf").split(";")[0].trim();
        return new Blob([await response.arrayBuffer()], { type });
      });
    }
    const error = new Error("missing");
    error.status = 404;
    return Promise.reject(error);
  }, [storagePath, dataUrl, href]);

  const open = () => {
    if (target.kind === "pdf" && !externalPdf) {
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
      {externalPdf ? (
        <p className="mop-muted" data-testid="mobile-design-pdf-external">PDF dari origin lain tetap dibuka di browser.</p>
      ) : null}
      {pdfOpen && file && (
        <InAppDocumentViewer
          documentKey={file.id || file.storage_path || file.name || "desain"}
          title={meta.name || "Desain"}
          fileName={file.name || "desain.pdf"}
          contentType={file.content_type || "application/pdf"}
          allowDownload={false}
          loadFile={loadDesign}
          onClose={() => setPdfOpen(false)}
          testId="mobile-design-pdf-viewer"
        />
      )}
    </div>
  );
}
