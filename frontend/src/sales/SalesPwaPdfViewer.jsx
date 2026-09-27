import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";

GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL || ""}/pdf.worker.min.mjs`;

const MAX_PAGE_WIDTH = 920;

function fittedWidth(node) {
  const measured = Math.max(0, (node?.clientWidth || 0) - 8);
  if (measured >= 120) return Math.min(MAX_PAGE_WIDTH, measured);
  const viewport = typeof window !== "undefined" ? window.innerWidth : 390;
  return Math.max(280, Math.min(MAX_PAGE_WIDTH, viewport - 24));
}

function PdfPage({ pdf, pageNumber, width }) {
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
        const availableWidth = Math.max(280, Math.min(MAX_PAGE_WIDTH, width));
        const cssScale = Math.min(2, availableWidth / base.width);
        const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
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
  }, [pdf, pageNumber, width]);

  return (
    <section className="spwa-pdf-page" aria-label={`Halaman ${pageNumber}`}>
      {error ? <div className="spwa-viewer-state is-error">{error}</div> : null}
      <canvas ref={canvasRef} />
      <span>{pageNumber}</span>
    </section>
  );
}

export default function SalesPwaPdfViewer({ blob, label }) {
  const frameRef = useRef(null);
  const [pageWidth, setPageWidth] = useState(() => fittedWidth(null));
  const [state, setState] = useState({ loading: true, pdf: null, error: "" });

  useEffect(() => {
    let active = true;
    let loadingTask = null;
    let pdfDoc = null;
    setState({ loading: true, pdf: null, error: "" });

    blob.arrayBuffer()
      .then((buffer) => {
        if (!active) return null;
        loadingTask = getDocument({
          data: new Uint8Array(buffer),
          isEvalSupported: false,
          useWorkerFetch: false,
        });
        return loadingTask.promise;
      })
      .then((pdf) => {
        pdfDoc = pdf || null;
        if (!active) {
          pdf?.destroy?.();
          return;
        }
        if (pdf) setState({ loading: false, pdf, error: "" });
      })
      .catch(() => {
        if (active) setState({ loading: false, pdf: null, error: "PDF gagal ditampilkan." });
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
  }, [blob]);

  useEffect(() => {
    const node = frameRef.current;
    if (!node || !state.pdf) return undefined;
    const apply = () => setPageWidth(fittedWidth(node));
    apply();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => observer.disconnect();
  }, [state.pdf]);

  if (state.loading) return <div className="spwa-viewer-state">Menyiapkan PDF...</div>;
  if (state.error) return <div className="spwa-viewer-state is-error">{state.error}</div>;

  return (
    <div className="spwa-pdf" ref={frameRef} role="document" aria-label={label} data-testid="sales-pdf-viewer">
      {Array.from({ length: state.pdf.numPages }, (_, index) => (
        <PdfPage key={`${index + 1}`} pdf={state.pdf} pageNumber={index + 1} width={pageWidth} />
      ))}
    </div>
  );
}
