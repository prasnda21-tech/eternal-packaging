import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";

GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL || ""}/pdf.worker.min.mjs`;

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];

function emptyView(key, attempt = 0) {
  return {
    key,
    attempt,
    status: "loading",
    message: "",
    blob: null,
    contentType: "",
    pdf: null,
    page: 1,
    pageCount: 1,
    zoom: 1,
    fitWidth: false,
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

function prefersFitWidth() {
  if (typeof window === "undefined" || !window.matchMedia) return true;
  return window.matchMedia("(max-width: 768px)").matches;
}

/**
 * In-app PDF.js / image viewer. Does not open a new tab and does not persist
 * tokens or file bytes. Later PDF features should reuse this component.
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
  const canvasRef = useRef(null);
  const objectUrlRef = useRef("");
  const [objectUrl, setObjectUrl] = useState("");

  useEffect(() => {
    const media = window.matchMedia ? window.matchMedia("(max-width: 768px)") : null;
    if (!media) return undefined;
    const onChange = () => {
      if (media.matches) {
        setView((current) => (current.key === documentKey ? { ...current, fitWidth: true } : current));
      }
    };
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, [documentKey]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let task = null;
    setView((current) => (
      current.key === documentKey
        ? { ...emptyView(documentKey, current.attempt), fitWidth: prefersFitWidth() }
        : current
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
                page: 1,
                pageCount: doc.numPages || 1,
              }
              : current
          ));
          return;
        }
        if (isImage(type, fileName, buffer)) {
          setView((current) => (
            current.key === documentKey
              ? { ...current, status: "ready", blob, contentType: type || "image/jpeg", pdf: null, page: 1, pageCount: 1 }
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

  useEffect(() => {
    if (view.status !== "ready" || !view.pdf || view.key !== documentKey || !canvasRef.current) return undefined;
    let active = true;
    let renderTask = null;
    let pdfPage = null;
    const draw = async () => {
      try {
        pdfPage = await view.pdf.getPage(view.page);
        if (!active || !canvasRef.current) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const frameWidth = Math.max(280, (frameRef.current?.clientWidth || 640) - 24);
        const scale = view.fitWidth ? frameWidth / base.width : view.zoom;
        const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
        const viewport = pdfPage.getViewport({ scale: scale * pixelRatio });
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d", { alpha: false });
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        canvas.style.width = `${Math.ceil(viewport.width / pixelRatio)}px`;
        canvas.style.height = `${Math.ceil(viewport.height / pixelRatio)}px`;
        canvas.style.maxWidth = "100%";
        canvas.dataset.scale = String(Math.round(scale * 1000) / 1000);
        renderTask = pdfPage.render({ canvasContext: context, viewport });
        await renderTask.promise;
      } catch (error) {
        if (active && error?.name !== "RenderingCancelledException") {
          setView((current) => (
            current.key === documentKey ? { ...current, status: "error", message: "Dokumen gagal dimuat.", pdf: null } : current
          ));
        }
      }
    };
    draw();
    return () => {
      active = false;
      renderTask?.cancel?.();
      pdfPage?.cleanup?.();
    };
  }, [view.status, view.pdf, view.page, view.zoom, view.fitWidth, view.key, documentKey]);

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

  const pdfMode = view.status === "ready" && view.pdf && view.key === documentKey;
  const imageMode = view.status === "ready" && objectUrl && view.key === documentKey;
  const pageCount = view.pageCount || 1;

  return (
    <div
      className="inapp-doc-overlay"
      data-testid={testId}
      data-document-key={documentKey}
      data-fit={view.fitWidth ? "width" : "zoom"}
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
        <div className="inapp-doc-tools">
          <button type="button" data-testid={`${testId}-prev`} disabled={!pdfMode || view.page <= 1} onClick={() => setView((current) => ({ ...current, page: Math.max(1, current.page - 1) }))}>Sebelumnya</button>
          <span data-testid={`${testId}-page`}>{pdfMode ? `Halaman ${view.page} / ${pageCount}` : "Halaman —"}</span>
          <button type="button" data-testid={`${testId}-next`} disabled={!pdfMode || view.page >= pageCount} onClick={() => setView((current) => ({ ...current, page: Math.min(pageCount, current.page + 1) }))}>Berikutnya</button>
          <button type="button" data-testid={`${testId}-zoom-out`} disabled={view.status !== "ready"} onClick={() => setView((current) => ({ ...current, fitWidth: false, zoom: Math.max(ZOOM_STEPS[0], Math.round((current.zoom - 0.25) * 100) / 100) }))}>Zoom -</button>
          <button type="button" data-testid={`${testId}-zoom-in`} disabled={view.status !== "ready"} onClick={() => setView((current) => ({ ...current, fitWidth: false, zoom: Math.min(ZOOM_STEPS[ZOOM_STEPS.length - 1], Math.round((current.zoom + 0.25) * 100) / 100) }))}>Zoom +</button>
          <button type="button" data-testid={`${testId}-fit`} disabled={view.status !== "ready"} onClick={() => setView((current) => ({ ...current, fitWidth: true }))}>Sesuaikan Lebar</button>
        </div>
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
          {pdfMode && <canvas ref={canvasRef} data-testid={`${testId}-canvas`} />}
          {imageMode && (
            <img
              data-testid={`${testId}-image`}
              alt={fileName}
              src={objectUrl}
              style={{ width: `${Math.round(view.zoom * 100)}%`, maxWidth: view.fitWidth ? "100%" : "none" }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
