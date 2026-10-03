import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";

GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL || ""}/pdf.worker.min.mjs`;

const MAX_PAGE_WIDTH = 920;
const MAX_LIVE_PAGES = 3;
const WINDOW_PIXEL_RATIO = 1.5;
const WINDOW_CANVAS_WIDTH = 1400;
const ZOOM_LEVELS = [1, 1.5, 2];

function fittedWidth(node) {
  const measured = Math.max(0, (node?.clientWidth || 0) - 8);
  if (measured >= 120) return Math.min(MAX_PAGE_WIDTH, measured);
  const viewport = typeof window !== "undefined" ? window.innerWidth : 390;
  return Math.max(280, Math.min(MAX_PAGE_WIDTH, viewport - 24));
}

function scrollParent(node) {
  let current = node?.parentElement;
  while (current) {
    const overflow = window.getComputedStyle(current).overflowY;
    if (overflow === "auto" || overflow === "scroll") return current;
    current = current.parentElement;
  }
  return null;
}

function windowedPdfMessage(error) {
  const name = String(error?.name || "");
  if (name === "PasswordException") return "PDF terproteksi dan tidak dapat ditampilkan.";
  if (name === "InvalidPDFException" || name === "FormatError") return "PDF rusak atau tidak dapat dibaca.";
  if (name === "EmptyPdfException") return "Dokumen kosong.";
  return "PDF gagal ditampilkan.";
}

function PdfCanvas({ pdf, pageNumber, width, pixelRatioCap, maxCanvasWidth }) {
  const canvasRef = useRef(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!width) return undefined;
    let active = true;
    let renderTask = null;
    let page = null;

    const render = async () => {
      try {
        page = await pdf.getPage(pageNumber);
        if (!active || !canvasRef.current) return;
        const base = page.getViewport({ scale: 1 });
        const availableWidth = Math.max(280, Math.min(MAX_PAGE_WIDTH * 2, width));
        let cssScale = Math.min(2, availableWidth / base.width);
        const pixelRatio = Math.min(pixelRatioCap, window.devicePixelRatio || 1);
        if (base.width * cssScale * pixelRatio > maxCanvasWidth) {
          cssScale = maxCanvasWidth / (base.width * pixelRatio);
        }
        const viewport = page.getViewport({ scale: cssScale * pixelRatio });
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d", { alpha: false });
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        canvas.style.width = `${Math.ceil(viewport.width / pixelRatio)}px`;
        canvas.style.height = `${Math.ceil(viewport.height / pixelRatio)}px`;
        renderTask = page.render({ canvasContext: context, viewport });
        await renderTask.promise;
      } catch (renderError) {
        if (active && renderError?.name !== "RenderingCancelledException") {
          setError("Halaman PDF gagal ditampilkan.");
        }
      }
    };

    render();
    return () => {
      active = false;
      renderTask?.cancel?.();
      page?.cleanup?.();
    };
  }, [pdf, pageNumber, width, pixelRatioCap, maxCanvasWidth]);

  if (error) return <div className="spwa-viewer-state is-error">{error}</div>;
  return <canvas ref={canvasRef} />;
}

function PdfPage({ pdf, pageNumber, width }) {
  return (
    <section className="spwa-pdf-page" aria-label={`Halaman ${pageNumber}`}>
      <PdfCanvas
        pdf={pdf}
        pageNumber={pageNumber}
        width={width}
        pixelRatioCap={2}
        maxCanvasWidth={2400}
      />
      <span>{pageNumber}</span>
    </section>
  );
}

function samePages(left, right) {
  if (left.size !== right.size) return false;
  for (const page of right) {
    if (!left.has(page)) return false;
  }
  return true;
}

function WindowedPdf({ pdf, label, pageWidth }) {
  const frameRef = useRef(null);
  const visibleRef = useRef(new Map());
  const [zoomIndex, setZoomIndex] = useState(0);
  const [current, setCurrent] = useState(1);
  const [aspect, setAspect] = useState(1.294);
  const [activePages, setActivePages] = useState(() => new Set([1]));
  const zoom = ZOOM_LEVELS[zoomIndex];
  const renderWidth = Math.round(pageWidth * zoom);

  useEffect(() => {
    let active = true;
    pdf.getPage(1).then((page) => {
      const viewport = page.getViewport({ scale: 1 });
      page.cleanup?.();
      if (active && viewport.width) setAspect(viewport.height / viewport.width);
    }).catch(() => {});
    return () => {
      active = false;
    };
  }, [pdf]);

  useEffect(() => {
    setZoomIndex(0);
    setCurrent(1);
    setActivePages(new Set([1]));
    visibleRef.current = new Map();
  }, [pdf]);

  useEffect(() => {
    const node = frameRef.current;
    if (!node) return undefined;
    const slots = [...node.querySelectorAll("[data-pdf-page]")];
    if (typeof IntersectionObserver === "undefined") {
      const fallback = new Set([1, Math.min(2, pdf.numPages)].filter((page) => page >= 1));
      setActivePages(fallback);
      setCurrent(1);
      return undefined;
    }

    const root = scrollParent(node);
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        const page = Number(entry.target.getAttribute("data-pdf-page"));
        if (!page) return;
        if (entry.isIntersecting) visibleRef.current.set(page, entry.intersectionRatio || 0.01);
        else visibleRef.current.delete(page);
      });
      let focus = 0;
      let best = -1;
      visibleRef.current.forEach((ratio, page) => {
        if (ratio > best || (ratio === best && (focus === 0 || page < focus))) {
          best = ratio;
          focus = page;
        }
      });
      if (!focus) return;
      const start = Math.max(1, focus - 1);
      const end = Math.min(pdf.numPages, start + MAX_LIVE_PAGES - 1);
      const next = new Set();
      for (let page = start; page <= end; page += 1) next.add(page);
      setCurrent(focus);
      setActivePages((prev) => (samePages(prev, next) ? prev : next));
    }, { root, rootMargin: "160px 0px", threshold: [0, 0.2, 0.6] });

    slots.forEach((slot) => observer.observe(slot));
    return () => observer.disconnect();
  }, [pdf, pageWidth, zoom]);

  const slotHeight = Math.max(160, Math.round(renderWidth * aspect));

  return (
    <div
      className={`spwa-pdf is-windowed${zoom > 1 ? " is-zoomed" : ""}`}
      ref={frameRef}
      role="document"
      aria-label={label}
      data-testid="sales-pdf-viewer"
      data-zoom={zoom}
    >
      <div className="spwa-pdf-toolbar" data-testid="sales-pdf-toolbar">
        <span data-testid="sales-pdf-page-label">Halaman {current} dari {pdf.numPages}</span>
        <div className="spwa-pdf-zoom">
          <button
            type="button"
            data-testid="sales-pdf-zoom-out"
            disabled={zoomIndex === 0}
            onClick={() => setZoomIndex((index) => Math.max(0, index - 1))}
          >
            Perkecil
          </button>
          <button
            type="button"
            data-testid="sales-pdf-zoom-in"
            disabled={zoomIndex === ZOOM_LEVELS.length - 1}
            onClick={() => setZoomIndex((index) => Math.min(ZOOM_LEVELS.length - 1, index + 1))}
          >
            Perbesar
          </button>
        </div>
      </div>
      {Array.from({ length: pdf.numPages }, (_, index) => {
        const pageNumber = index + 1;
        const active = activePages.has(pageNumber);
        return (
          <section
            key={pageNumber}
            className="spwa-pdf-page"
            data-pdf-page={pageNumber}
            aria-label={`Halaman ${pageNumber}`}
            style={{ minHeight: slotHeight, width: zoom > 1 ? renderWidth : "100%" }}
          >
            {active ? (
              <PdfCanvas
                pdf={pdf}
                pageNumber={pageNumber}
                width={renderWidth}
                pixelRatioCap={WINDOW_PIXEL_RATIO}
                maxCanvasWidth={WINDOW_CANVAS_WIDTH}
              />
            ) : (
              <span className="spwa-pdf-slot">{pageNumber}</span>
            )}
          </section>
        );
      })}
    </div>
  );
}

export default function SalesPwaPdfViewer({ blob, label, mode = "stack", onError }) {
  const frameRef = useRef(null);
  const [pageWidth, setPageWidth] = useState(() => fittedWidth(null));
  const [reload, setReload] = useState(0);
  const [state, setState] = useState({ loading: true, pdf: null, error: "" });
  const windowed = mode === "windowed";

  useEffect(() => {
    let active = true;
    let loadingTask = null;
    let pdfDoc = null;
    setState({ loading: true, pdf: null, error: "" });

    blob.arrayBuffer()
      .then((buffer) => {
        if (!active) return null;
        const bytes = new Uint8Array(buffer || new ArrayBuffer(0));
        if (windowed && bytes.byteLength < 5) {
          const empty = new Error("empty");
          empty.name = "EmptyPdfException";
          throw empty;
        }
        loadingTask = getDocument({
          data: bytes,
          isEvalSupported: false,
          useWorkerFetch: false,
        });
        return loadingTask.promise;
      })
      .then((pdf) => {
        if (!pdf) return;
        pdfDoc = pdf;
        if (!active) {
          pdf.destroy?.();
          return;
        }
        if (windowed && !pdf.numPages) {
          pdf.destroy?.();
          pdfDoc = null;
          setState({ loading: false, pdf: null, error: "Dokumen kosong." });
          return;
        }
        setState({ loading: false, pdf, error: "" });
      })
      .catch((error) => {
        if (!active) return;
        const message = windowed ? windowedPdfMessage(error) : "PDF gagal ditampilkan.";
        setState({ loading: false, pdf: null, error: message });
      });

    return () => {
      active = false;
      const task = loadingTask;
      const doc = pdfDoc;
      loadingTask = null;
      pdfDoc = null;
      try {
        if (doc) doc.destroy?.();
        else task?.destroy?.();
      } catch {
        /* render already cancelled */
      }
    };
  }, [blob, reload, windowed]);

  useEffect(() => {
    if (state.error) onError?.(state.error);
  }, [state.error, onError]);

  useEffect(() => {
    const node = frameRef.current;
    if (!node || !state.pdf || windowed) return undefined;
    const apply = () => setPageWidth(fittedWidth(node));
    apply();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => observer.disconnect();
  }, [state.pdf, windowed]);

  useEffect(() => {
    if (!windowed || !state.pdf) return undefined;
    const apply = () => setPageWidth(fittedWidth(frameRef.current?.parentElement || frameRef.current));
    apply();
    if (typeof ResizeObserver === "undefined") return undefined;
    const target = frameRef.current?.parentElement || frameRef.current;
    if (!target) return undefined;
    const observer = new ResizeObserver(apply);
    observer.observe(target);
    return () => observer.disconnect();
  }, [state.pdf, windowed]);

  if (state.loading) return <div className="spwa-viewer-state">Menyiapkan PDF...</div>;
  if (state.error) {
    if (onError) return null;
    return (
      <div className="spwa-viewer-state is-error">
        <p>{state.error}</p>
        {windowed ? (
          <button type="button" data-testid="sales-document-retry" onClick={() => setReload((value) => value + 1)}>
            Coba lagi
          </button>
        ) : null}
      </div>
    );
  }

  if (windowed) {
    return (
      <div ref={frameRef}>
        <WindowedPdf pdf={state.pdf} label={label} pageWidth={pageWidth} />
      </div>
    );
  }

  return (
    <div className="spwa-pdf" ref={frameRef} role="document" aria-label={label} data-testid="sales-pdf-viewer">
      {Array.from({ length: state.pdf.numPages }, (_, index) => (
        <PdfPage key={`${index + 1}`} pdf={state.pdf} pageNumber={index + 1} width={pageWidth} />
      ))}
    </div>
  );
}
