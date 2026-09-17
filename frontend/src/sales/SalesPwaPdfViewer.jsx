import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";

GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL || ""}/pdf.worker.min.mjs`;

function PdfPage({ pdf, pageNumber }) {
  const canvasRef = useRef(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let renderTask = null;
    let page = null;

    const render = async () => {
      try {
        page = await pdf.getPage(pageNumber);
        if (!active || !canvasRef.current) return;
        const base = page.getViewport({ scale: 1 });
        const availableWidth = Math.max(280, Math.min(920, window.innerWidth - 24));
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
  }, [pdf, pageNumber]);

  return (
    <section className="spwa-pdf-page" aria-label={`Halaman ${pageNumber}`}>
      {error ? <div className="spwa-viewer-state is-error">{error}</div> : null}
      <canvas ref={canvasRef} />
      <span>{pageNumber}</span>
    </section>
  );
}

export default function SalesPwaPdfViewer({ blob, label }) {
  const [state, setState] = useState({ loading: true, pdf: null, error: "" });

  useEffect(() => {
    let active = true;
    let loadingTask = null;
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
        if (active && pdf) setState({ loading: false, pdf, error: "" });
      })
      .catch(() => {
        if (active) setState({ loading: false, pdf: null, error: "PDF gagal ditampilkan." });
      });

    return () => {
      active = false;
      loadingTask?.destroy?.();
    };
  }, [blob]);

  if (state.loading) return <div className="spwa-viewer-state">Menyiapkan PDF...</div>;
  if (state.error) return <div className="spwa-viewer-state is-error">{state.error}</div>;

  return (
    <div className="spwa-pdf" role="document" aria-label={label} data-testid="sales-pdf-viewer">
      {Array.from({ length: state.pdf.numPages }, (_, index) => (
        <PdfPage key={index + 1} pdf={state.pdf} pageNumber={index + 1} />
      ))}
    </div>
  );
}
