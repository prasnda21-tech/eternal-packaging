import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Minus, Plus, RotateCcw } from "lucide-react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";

GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL || ""}/pdf.worker.min.mjs`;

const MAX_PAGE_WIDTH = 920;
const FIT_ZOOM = 1;

function emptyView(key, attempt = 0) {
  return {
    key,
    attempt,
    status: "loading",
    message: "",
    blob: null,
    contentType: "",
    pdf: null,
  };
}

function statusOf(error) {
  return Number(error?.response?.status || error?.status || 0);
}

function isPdf(contentType, name, bytes) {
  if (bytes && bytes.length >= 5 && String.fromCharCode(...bytes.slice(0, 5)) === "%PDF-") return true;
  const type = String(contentType || "").toLowerCase();
  return type.includes("pdf") || /\.pdf$/i.test(name || "");
}

function isImage(contentType, name, bytes) {
  const type = String(contentType || "").toLowerCase();
  if (type.includes("svg") || type.includes("html")) return false;
  if (bytes && bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return true;
  if (bytes && bytes.length >= 4 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return true;
  return type === "image/jpeg" || type === "image/png" || /\.(jpe?g|png)$/i.test(name || "");
}

/** Width-only fit (default historical shell). */
function fittedPageWidth(node) {
  const inner = Math.max(0, (node?.clientWidth || 0) - 16);
  if (!inner) return 0;
  return Math.min(MAX_PAGE_WIDTH, inner);
}

/**
 * Compact fit-to-page: scale by both available width and height so one page
 * is fully visible without initial scroll. pageAspect = pageHeight / pageWidth.
 */
export function fittedPageWidthWithinFrame(node, pageAspect = 1) {
  const pad = 16;
  const innerW = Math.max(0, (node?.clientWidth || 0) - pad);
  const innerH = Math.max(0, (node?.clientHeight || 0) - pad);
  if (!innerW) return 0;
  const aspect = Number(pageAspect) > 0 ? Number(pageAspect) : 1;
  const byHeight = innerH > 0 ? innerH / aspect : innerW;
  return Math.max(1, Math.floor(Math.min(MAX_PAGE_WIDTH, innerW, byHeight)));
}

function PdfPage({ pdf, pageNumber, width, testId, onFail }) {
  const canvasRef = useRef(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!width) return undefined;
    let active = true;
    let renderTask = null;
    let pdfPage = null;
    setFailed(false);
    const draw = async () => {
      try {
        pdfPage = await pdf.getPage(pageNumber);
        if (!active || !canvasRef.current) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const cssScale = width / base.width;
        const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
        const viewport = pdfPage.getViewport({ scale: cssScale * pixelRatio });
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d", { alpha: false });
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        canvas.style.width = `${Math.ceil(width)}px`;
        canvas.style.height = `${Math.ceil(base.height * cssScale)}px`;
        canvas.style.maxWidth = "100%";
        renderTask = pdfPage.render({ canvasContext: context, viewport });
        await renderTask.promise;
      } catch (error) {
        if (active && error?.name !== "RenderingCancelledException") {
          setFailed(true);
          onFail();
        }
      }
    };
    draw();
    return () => {
      active = false;
      renderTask?.cancel?.();
      pdfPage?.cleanup?.();
    };
  }, [pdf, pageNumber, width, onFail]);

  return (
    <section className="inapp-doc-sheet" aria-label={`Halaman ${pageNumber}`} data-page={pageNumber}>
      {failed ? <p className="muted">Halaman gagal ditampilkan.</p> : null}
      <canvas ref={canvasRef} data-testid={`${testId}-canvas`} data-page={pageNumber} />
      <span>{pageNumber}</span>
    </section>
  );
}

/**
 * In-app PDF.js / image viewer. All PDF pages stack in the scrolling stage (default).
 * layout="compact" (receipt proof): fit-to-viewport image/PDF without initial scroll.
 * The header stays outside that scroll. Does not open a new tab.
 */
export default function InAppDocumentViewer({
  documentKey,
  title = "Dokumen",
  fileName = "dokumen",
  contentType = "",
  allowDownload = true,
  pdfDownload = false,
  loadFile,
  onClose,
  testId = "inapp-doc-viewer",
  /** "default" keeps historical fullscreen shell; "compact" is opt-in for receipt proof only. */
  layout = "default",
}) {
  const compact = layout === "compact";
  const [view, setView] = useState(() => emptyView(documentKey));
  if (view.key !== documentKey) {
    setView(emptyView(documentKey));
  }
  const frameRef = useRef(null);
  const objectUrlRef = useRef("");
  const [objectUrl, setObjectUrl] = useState("");
  const [pageWidth, setPageWidth] = useState(0);
  const [pageAspect, setPageAspect] = useState(1);
  const [pdfPage, setPdfPage] = useState(1);
  const [imageZoom, setImageZoom] = useState(FIT_ZOOM);

  useEffect(() => {
    setPdfPage(1);
    setImageZoom(FIT_ZOOM);
    setPageAspect(1);
  }, [documentKey]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let task = null;
    setView((current) => (
      current.key === documentKey ? emptyView(documentKey, current.attempt) : current
    ));
    loadFile({ download: false, signal: controller.signal })
      .then(async (blob) => {
        if (!active) return;
        const type = blob?.type || contentType || "";
        const buffer = new Uint8Array(await blob.arrayBuffer());
        if (!active) return;
        if (String(type).includes("html") || String(type).includes("svg")) {
          setView((current) => (
            current.key === documentKey
              ? { ...current, status: "unsupported", message: "Jenis file tidak dapat ditampilkan.", blob: null, pdf: null }
              : current
          ));
          return;
        }
        if (isPdf(type, fileName, buffer)) {
          task = getDocument({ data: buffer, isEvalSupported: false, useWorkerFetch: false });
          const doc = await task.promise;
          if (!active) {
            doc?.destroy?.();
            return;
          }
          if (compact) {
            try {
              const first = await doc.getPage(1);
              const base = first.getViewport({ scale: 1 });
              if (active && base.width > 0) {
                setPageAspect(base.height / base.width);
              }
              first.cleanup?.();
            } catch {
              /* keep default aspect */
            }
          }
          setView((current) => (
            current.key === documentKey
              ? {
                ...current,
                status: "ready",
                blob,
                contentType: type || "application/pdf",
                pdf: doc,
              }
              : current
          ));
          return;
        }
        if (isImage(type, fileName, buffer)) {
          setView((current) => (
            current.key === documentKey
              ? { ...current, status: "ready", blob, contentType: type || "image/jpeg", pdf: null }
              : current
          ));
          return;
        }
        setView((current) => (
          current.key === documentKey
            ? { ...current, status: "unsupported", message: "Jenis file tidak dapat ditampilkan.", blob: null, pdf: null }
            : current
        ));
      })
      .catch((error) => {
        if (!active || error?.code === "ERR_CANCELED" || error?.name === "CanceledError" || error?.name === "AbortError") return;
        const code = statusOf(error);
        if (code === 401 || code === 403 || code === 404) {
          setView((current) => (
            current.key === documentKey
              ? { ...current, status: "denied", message: "Akses ditolak.", blob: null, pdf: null }
              : current
          ));
          return;
        }
        if (code === 415) {
          setView((current) => (
            current.key === documentKey
              ? { ...current, status: "unsupported", message: "Jenis file tidak dapat ditampilkan.", blob: null, pdf: null }
              : current
          ));
          return;
        }
        setView((current) => (
          current.key === documentKey
            ? { ...current, status: "error", message: "Dokumen gagal dimuat.", blob: null, pdf: null }
            : current
        ));
      });
    return () => {
      active = false;
      controller.abort();
      const pending = task;
      task = null;
      Promise.resolve(pending?.destroy?.()).catch(() => {});
    };
  }, [documentKey, contentType, fileName, loadFile, view.attempt, compact]);

  const pdfMode = view.status === "ready" && view.pdf && view.key === documentKey;
  const pageCount = view.pdf?.numPages || 0;

  useEffect(() => {
    if (!pdfMode || pageCount < 1) return;
    setPdfPage((current) => Math.min(Math.max(1, current), pageCount));
  }, [pdfMode, pageCount]);

  useEffect(() => {
    const node = frameRef.current;
    if (!node || !pdfMode) {
      setPageWidth(0);
      return undefined;
    }
    const apply = () => {
      if (compact) {
        setPageWidth(fittedPageWidthWithinFrame(node, pageAspect));
      } else {
        setPageWidth(fittedPageWidth(node));
      }
    };
    apply();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => observer.disconnect();
  }, [pdfMode, documentKey, compact, pageAspect]);

  useEffect(() => {
    const imageReady = view.status === "ready" && view.blob && isImage(view.contentType, fileName) && view.key === documentKey;
    if (!imageReady) {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = "";
      }
      setObjectUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(view.blob);
    objectUrlRef.current = url;
    setObjectUrl(url);
    return () => {
      URL.revokeObjectURL(url);
      if (objectUrlRef.current === url) objectUrlRef.current = "";
    };
  }, [view.status, view.blob, view.contentType, view.key, documentKey, fileName]);

  useEffect(() => () => {
    try { view.pdf?.destroy?.(); } catch { /* already destroyed */ }
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
  }, [view.pdf]);

  const failRef = useRef(() => {});
  failRef.current = () => {
    setView((current) => (
      current.key === documentKey
        ? { ...current, status: "error", message: "Dokumen gagal dimuat.", pdf: null, blob: null }
        : current
    ));
  };
  const failPages = useCallback(() => failRef.current(), []);

  const saveBlob = (file, name) => {
    const url = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = url;
    link.download = name || "dokumen";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const failDownload = () => {
    setView((current) => (
      current.key === documentKey
        ? { ...current, status: "error", message: "Dokumen gagal dimuat.", pdf: null, blob: null }
        : current
    ));
  };

  const download = async () => {
    try {
      saveBlob(await loadFile({ download: true }), fileName || "dokumen");
    } catch (error) {
      if (error?.name === "AbortError" || error?.name === "CanceledError") return;
      failDownload();
    }
  };

  const downloadPdf = async () => {
    try {
      const previewed = view.blob && view.key === documentKey ? view.blob : null;
      const file = previewed || await loadFile({ download: true });
      saveBlob(file, fileName || "dokumen.pdf");
    } catch (error) {
      if (error?.name === "AbortError" || error?.name === "CanceledError") return;
      failDownload();
    }
  };

  const imageMode = view.status === "ready" && objectUrl && view.key === documentKey;
  const zoomed = compact && imageMode && imageZoom > FIT_ZOOM + 0.01;
  const showCompactZoom = compact && imageMode && view.status === "ready";
  const showPdfNav = compact && pdfMode && pageCount > 1;

  return (
    <div
      className={`inapp-doc-overlay${compact ? " inapp-doc-overlay--compact" : ""}`}
      data-testid={testId}
      data-layout={compact ? "compact" : "default"}
      data-fit={compact ? "viewport" : "default"}
      data-image-zoom={compact && imageMode ? String(imageZoom) : "1"}
      data-document-key={documentKey}
      data-page-count={pdfMode ? String(pageCount) : "0"}
      data-page-width={pdfMode ? String(pageWidth) : "0"}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className={`inapp-doc${compact ? " inapp-doc--compact" : ""}${zoomed ? " is-zoomed" : ""}`}>
        <header className="inapp-doc-head">
          <strong>{title}</strong>
          <div className="inapp-doc-head-actions">
            {showPdfNav && (
              <>
                <button
                  type="button"
                  data-testid={`${testId}-prev-page`}
                  disabled={pdfPage <= 1}
                  onClick={() => setPdfPage((p) => Math.max(1, p - 1))}
                >
                  Sebelumnya
                </button>
                <span className="inapp-doc-page-label" data-testid={`${testId}-page-label`}>
                  {pdfPage} / {pageCount}
                </span>
                <button
                  type="button"
                  data-testid={`${testId}-next-page`}
                  disabled={pdfPage >= pageCount}
                  onClick={() => setPdfPage((p) => Math.min(pageCount, p + 1))}
                >
                  Berikutnya
                </button>
              </>
            )}
            {showCompactZoom && (
              <>
                <button
                  type="button"
                  data-testid={`${testId}-zoom-out`}
                  disabled={imageZoom <= FIT_ZOOM}
                  onClick={() => setImageZoom((z) => Math.max(FIT_ZOOM, Math.round((z - 0.25) * 100) / 100))}
                  aria-label="Perkecil"
                >
                  <Minus size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  data-testid={`${testId}-zoom-fit`}
                  onClick={() => setImageZoom(FIT_ZOOM)}
                  aria-label="Fit layar"
                >
                  <RotateCcw size={14} aria-hidden="true" />
                  Fit
                </button>
                <button
                  type="button"
                  data-testid={`${testId}-zoom-in`}
                  disabled={imageZoom >= 3}
                  onClick={() => setImageZoom((z) => Math.min(3, Math.round((z + 0.25) * 100) / 100))}
                  aria-label="Perbesar"
                >
                  <Plus size={14} aria-hidden="true" />
                </button>
              </>
            )}
            {pdfDownload && (
              <button
                type="button"
                data-testid={`${testId}-download`}
                onClick={downloadPdf}
                disabled={view.status !== "ready" || view.key !== documentKey}
              >
                <Download size={14} aria-hidden="true" />
                Unduh PDF
              </button>
            )}
            {allowDownload && !pdfDownload && (
              <button type="button" data-testid={`${testId}-download`} onClick={download} disabled={view.status !== "ready"}>Unduh</button>
            )}
            <button type="button" data-testid={`${testId}-close`} onClick={onClose}>Tutup</button>
          </div>
        </header>
        <div
          className={`inapp-doc-stage${compact ? " inapp-doc-stage--fit" : ""}`}
          ref={frameRef}
          data-testid={`${testId}-stage`}
          data-overflow={zoomed ? "scroll" : compact ? "fit" : "scroll"}
        >
          {view.status === "loading" && <p data-testid={`${testId}-loading`}>Memuat dokumen...</p>}
          {view.status === "error" && (
            <div data-testid={`${testId}-error`}>
              <p>{view.message}</p>
              <button type="button" data-testid={`${testId}-retry`} onClick={() => setView((current) => ({ ...emptyView(documentKey, current.attempt + 1) }))}>Coba lagi</button>
            </div>
          )}
          {view.status === "denied" && <p data-testid={`${testId}-denied`}>{view.message}</p>}
          {view.status === "unsupported" && <p data-testid={`${testId}-unsupported`}>{view.message}</p>}
          {pdfMode && !compact && (
            <div className="inapp-doc-pages" data-testid={`${testId}-pages`}>
              {Array.from({ length: pageCount }, (_, index) => (
                <PdfPage
                  key={`${documentKey}:${index + 1}`}
                  pdf={view.pdf}
                  pageNumber={index + 1}
                  width={pageWidth}
                  testId={testId}
                  onFail={failPages}
                />
              ))}
            </div>
          )}
          {pdfMode && compact && pageWidth > 0 && (
            <div className="inapp-doc-pages inapp-doc-pages--fit" data-testid={`${testId}-pages`}>
              <PdfPage
                key={`${documentKey}:${pdfPage}`}
                pdf={view.pdf}
                pageNumber={pdfPage}
                width={pageWidth}
                testId={testId}
                onFail={failPages}
              />
            </div>
          )}
          {imageMode && (
            <img
              data-testid={`${testId}-image`}
              alt={fileName}
              src={objectUrl}
              className={compact ? "inapp-doc-image--contain" : undefined}
              style={compact ? {
                transform: imageZoom === FIT_ZOOM ? undefined : `scale(${imageZoom})`,
                transformOrigin: "center center",
              } : undefined}
            />
          )}
        </div>
      </div>
    </div>
  );
}
