import { useCallback, useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";

GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL || ""}/pdf.worker.min.mjs`;

const MAX_PAGE_WIDTH = 920;

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

function fittedPageWidth(node) {
  const inner = Math.max(0, (node?.clientWidth || 0) - 16);
  if (!inner) return 0;
  return Math.min(MAX_PAGE_WIDTH, inner);
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
 * In-app PDF.js / image viewer. All PDF pages stack in the scrolling stage.
 * The header stays outside that scroll. Does not open a new tab.
 */
export default function InAppDocumentViewer({
  documentKey,
  title = "Dokumen",
  fileName = "dokumen",
  contentType = "",
  allowDownload = true,
  loadFile,
  onClose,
  testId = "inapp-doc-viewer",
}) {
  const [view, setView] = useState(() => emptyView(documentKey));
  if (view.key !== documentKey) {
    setView(emptyView(documentKey));
  }
  const frameRef = useRef(null);
  const objectUrlRef = useRef("");
  const [objectUrl, setObjectUrl] = useState("");
  const [pageWidth, setPageWidth] = useState(0);

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
  }, [documentKey, contentType, fileName, loadFile, view.attempt]);

  const pdfMode = view.status === "ready" && view.pdf && view.key === documentKey;

  useEffect(() => {
    const node = frameRef.current;
    if (!node || !pdfMode) {
      setPageWidth(0);
      return undefined;
    }
    const apply = () => setPageWidth(fittedPageWidth(node));
    apply();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => observer.disconnect();
  }, [pdfMode, documentKey]);

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

  const download = async () => {
    try {
      const file = await loadFile({ download: true });
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName || "dokumen";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      if (error?.name === "AbortError" || error?.name === "CanceledError") return;
      setView((current) => (
        current.key === documentKey
          ? { ...current, status: "error", message: "Dokumen gagal dimuat.", pdf: null, blob: null }
          : current
      ));
    }
  };

  const imageMode = view.status === "ready" && objectUrl && view.key === documentKey;
  const pageCount = view.pdf?.numPages || 0;

  return (
    <div
      className="inapp-doc-overlay"
      data-testid={testId}
      data-document-key={documentKey}
      data-page-count={pdfMode ? String(pageCount) : "0"}
      data-page-width={pdfMode ? String(pageWidth) : "0"}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="inapp-doc">
        <header className="inapp-doc-head">
          <strong>{title}</strong>
          <div className="inapp-doc-head-actions">
            {allowDownload && (
              <button type="button" data-testid={`${testId}-download`} onClick={download} disabled={view.status !== "ready"}>Unduh</button>
            )}
            <button type="button" data-testid={`${testId}-close`} onClick={onClose}>Tutup</button>
          </div>
        </header>
        <div className="inapp-doc-stage" ref={frameRef} data-testid={`${testId}-stage`}>
          {view.status === "loading" && <p data-testid={`${testId}-loading`}>Memuat dokumen...</p>}
          {view.status === "error" && (
            <div data-testid={`${testId}-error`}>
              <p>{view.message}</p>
              <button type="button" data-testid={`${testId}-retry`} onClick={() => setView((current) => ({ ...emptyView(documentKey, current.attempt + 1) }))}>Coba lagi</button>
            </div>
          )}
          {view.status === "denied" && <p data-testid={`${testId}-denied`}>{view.message}</p>}
          {view.status === "unsupported" && <p data-testid={`${testId}-unsupported`}>{view.message}</p>}
          {pdfMode && (
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
          {imageMode && (
            <img data-testid={`${testId}-image`} alt={fileName} src={objectUrl} />
          )}
        </div>
      </div>
    </div>
  );
}
