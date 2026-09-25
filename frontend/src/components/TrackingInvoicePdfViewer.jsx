import { useEffect, useRef, useState } from "react";
import { publicApiUrl } from "../lib/api";

async function openPdfDocument(bytes) {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL || ""}/pdf.worker.min.mjs`;
  return pdfjs.getDocument({
    data: bytes,
    isEvalSupported: false,
    useWorkerFetch: false,
  });
}

const ZOOM_STEPS = [0.75, 1, 1.25, 1.5, 2];

function stageWidth(node) {
  const measured = node?.clientWidth || 0;
  if (measured >= 120) return measured;
  const viewport = typeof window !== "undefined" ? window.innerWidth : 390;
  return Math.max(280, Math.min(920, viewport - 24));
}

function isPdfBytes(bytes) {
  return bytes.length >= 5 && String.fromCharCode(...bytes.slice(0, 5)) === "%PDF-";
}

export default function TrackingInvoicePdfViewer({ pdfPath, invoiceId }) {
  const stageRef = useRef(null);
  const loadedFor = useRef("");
  const [attempt, setAttempt] = useState(0);
  const [phase, setPhase] = useState("loading");
  const [pdf, setPdf] = useState(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [fitMode, setFitMode] = useState(true);
  const [zoomIndex, setZoomIndex] = useState(1);
  const [layoutTick, setLayoutTick] = useState(0);
  const widthRef = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    let loadingTask = null;
    let cancelled = false;
    loadedFor.current = "";
    setPhase("loading");
    setPdf(null);
    setPageNumber(1);
    setFitMode(true);
    setZoomIndex(1);

    const load = async () => {
      try {
        const response = await fetch(publicApiUrl(pdfPath), {
          credentials: "omit",
          cache: "no-store",
          signal: controller.signal,
        });
        if (cancelled || controller.signal.aborted) return;
        if (response.status === 401 || response.status === 403 || response.status === 404) {
          setPhase("denied");
          return;
        }
        if (!response.ok) {
          setPhase("error");
          return;
        }
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (cancelled || controller.signal.aborted) return;
        if (!isPdfBytes(bytes)) {
          setPhase("error");
          return;
        }
        loadingTask = await openPdfDocument(bytes);
        const doc = await loadingTask.promise;
        if (cancelled || controller.signal.aborted) {
          doc?.destroy?.();
          return;
        }
        loadedFor.current = invoiceId;
        setPdf(doc);
        setPhase("ready");
      } catch (error) {
        if (cancelled || error?.name === "AbortError") return;
        setPhase("error");
      }
    };

    load();
    return () => {
      cancelled = true;
      controller.abort();
      const task = loadingTask;
      loadingTask = null;
      Promise.resolve(task?.destroy?.()).catch(() => {});
    };
  }, [pdfPath, invoiceId, attempt]);

  useEffect(() => {
    const node = stageRef.current;
    if (!node || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => {
      const width = node.clientWidth;
      if (Math.abs(width - widthRef.current) < 2) return;
      widthRef.current = width;
      setLayoutTick((value) => value + 1);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [phase]);

  useEffect(() => {
    if (phase !== "ready" || !pdf || loadedFor.current !== invoiceId) return undefined;
    const canvas = stageRef.current?.querySelector("canvas");
    if (!canvas) return undefined;
    let active = true;
    let renderTask = null;
    let page = null;

    const render = async () => {
      try {
        page = await pdf.getPage(pageNumber);
        if (!active) return;
        const base = page.getViewport({ scale: 1 });
        const fit = Math.min(2.5, Math.max(0.2, (stageWidth(stageRef.current) - 16) / base.width));
        const cssScale = fit * (fitMode ? 1 : ZOOM_STEPS[zoomIndex]);
        const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
        const viewport = page.getViewport({ scale: cssScale * pixelRatio });
        const context = canvas.getContext("2d", { alpha: false });
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        canvas.style.width = `${Math.ceil(viewport.width / pixelRatio)}px`;
        canvas.style.height = `${Math.ceil(viewport.height / pixelRatio)}px`;
        canvas.dataset.scale = String(Math.round(cssScale * 1000) / 1000);
        renderTask = page.render({ canvasContext: context, viewport });
        await renderTask.promise;
      } catch (renderError) {
        if (active && renderError?.name !== "RenderingCancelledException") {
          setPhase("error");
        }
      }
    };

    render();
    return () => {
      active = false;
      renderTask?.cancel?.();
      page?.cleanup?.();
    };
  }, [pdf, phase, pageNumber, fitMode, zoomIndex, layoutTick, invoiceId]);

  const pageCount = pdf?.numPages || 0;
  const zoomLabel = `${Math.round((fitMode ? 1 : ZOOM_STEPS[zoomIndex]) * 100)}%`;

  const zoomBy = (direction) => {
    const current = fitMode ? 1 : zoomIndex;
    const next = Math.min(ZOOM_STEPS.length - 1, Math.max(0, current + direction));
    setFitMode(false);
    setZoomIndex(next);
  };

  return (
    <div className="tracking-pdf" data-testid="tracking-invoice-pdf-viewer" data-invoice-id={invoiceId}>
      <div className="tracking-pdf-tools">
        <button
          type="button"
          data-testid="tracking-pdf-prev"
          disabled={phase !== "ready" || pageNumber <= 1}
          onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
        >
          Sebelumnya
        </button>
        <span className="tracking-pdf-page" data-testid="tracking-pdf-page">
          {phase === "ready" ? `${pageNumber} / ${pageCount}` : "—"}
        </span>
        <button
          type="button"
          data-testid="tracking-pdf-next"
          disabled={phase !== "ready" || pageNumber >= pageCount}
          onClick={() => setPageNumber((value) => Math.min(pageCount, value + 1))}
        >
          Berikutnya
        </button>
        <button type="button" data-testid="tracking-pdf-zoom-out" disabled={phase !== "ready"} onClick={() => zoomBy(-1)}>
          Perkecil
        </button>
        <span className="tracking-pdf-page" data-testid="tracking-pdf-zoom-label">{zoomLabel}</span>
        <button type="button" data-testid="tracking-pdf-zoom-in" disabled={phase !== "ready"} onClick={() => zoomBy(1)}>
          Perbesar
        </button>
        <button
          type="button"
          data-testid="tracking-pdf-fit"
          disabled={phase !== "ready"}
          onClick={() => {
            setFitMode(true);
            setZoomIndex(1);
          }}
        >
          Sesuaikan Lebar
        </button>
      </div>
      <div className="tracking-pdf-stage" ref={stageRef} data-testid="tracking-pdf-stage">
        {phase === "loading" && <p className="tracking-pdf-state" data-testid="tracking-pdf-loading">Memuat PDF...</p>}
        {phase === "error" && (
          <div className="tracking-pdf-state is-error" data-testid="tracking-pdf-error">
            <p>PDF gagal dimuat.</p>
            <button type="button" className="tracking-inv-btn" data-testid="tracking-pdf-retry" onClick={() => setAttempt((value) => value + 1)}>
              Coba lagi
            </button>
          </div>
        )}
        {phase === "denied" && (
          <div className="tracking-pdf-state is-error" data-testid="tracking-pdf-denied">
            <p>Akses PDF ditolak.</p>
            <button type="button" className="tracking-inv-btn" data-testid="tracking-pdf-retry" onClick={() => setAttempt((value) => value + 1)}>
              Coba lagi
            </button>
          </div>
        )}
        {phase === "ready" && <canvas data-testid="tracking-pdf-canvas" />}
      </div>
    </div>
  );
}
