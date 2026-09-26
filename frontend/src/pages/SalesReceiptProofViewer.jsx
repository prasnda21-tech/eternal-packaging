import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import api from "../lib/api";

GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL || ""}/pdf.worker.min.mjs`;

function isPdf(contentType, name) {
  return String(contentType || "").includes("pdf") || /\.pdf$/i.test(name || "");
}

function isImage(contentType, name) {
  const ct = String(contentType || "").toLowerCase();
  if (ct === "image/svg+xml" || ct.includes("html")) return false;
  return ct === "image/jpeg" || ct === "image/png" || /\.(jpe?g|png)$/i.test(name || "");
}

export default function SalesReceiptProofViewer({ receipt, onClose }) {
  const [status, setStatus] = useState("loading");
  const [message, setMessage] = useState("");
  const [blob, setBlob] = useState(null);
  const [contentType, setContentType] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [pdf, setPdf] = useState(null);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [fitWidth, setFitWidth] = useState(true);
  const [objectUrl, setObjectUrl] = useState("");
  const canvasRef = useRef(null);
  const frameRef = useRef(null);
  const objectUrlRef = useRef("");

  const name = receipt?.payment_proof?.name || "bukti-pembayaran";

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setStatus("loading");
    setMessage("");
    setBlob(null);
    setPdf(null);
    setPage(1);
    api.downloadSalesReceiptPaymentProof(receipt.id, false, { signal: controller.signal })
      .then((data) => {
        if (!active) return;
        const type = data?.type || receipt?.payment_proof?.content_type || "";
        if (String(type).includes("html") || String(type).includes("svg")) {
          setStatus("rejected");
          setMessage("Jenis file tidak dapat ditampilkan sebagai bukti pembayaran.");
          return;
        }
        setBlob(data);
        setContentType(type);
        setStatus("ready");
      })
      .catch((error) => {
        if (!active || error?.code === "ERR_CANCELED" || error?.name === "CanceledError") return;
        const code = error?.response?.status;
        if (code === 403 || code === 401) {
          setStatus("denied");
          setMessage("Akses ditolak.");
          return;
        }
        setStatus("error");
        setMessage("Bukti pembayaran gagal dimuat.");
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [receipt.id, attempt]);

  useEffect(() => {
    if (!blob || !isPdf(contentType, name)) {
      setPdf(null);
      return undefined;
    }
    let active = true;
    let task = null;
    blob.arrayBuffer().then((buffer) => {
      if (!active) return null;
      task = getDocument({ data: new Uint8Array(buffer), isEvalSupported: false, useWorkerFetch: false });
      return task.promise;
    }).then((doc) => {
      if (active && doc) {
        setPdf(doc);
        setPageCount(doc.numPages || 1);
        setPage(1);
      }
    }).catch(() => {
      if (active) {
        setStatus("error");
        setMessage("PDF gagal ditampilkan.");
      }
    });
    return () => {
      active = false;
      task?.destroy?.();
    };
  }, [blob, contentType, name]);

  useEffect(() => {
    if (!pdf || !canvasRef.current) return undefined;
    let active = true;
    let renderTask = null;
    let pdfPage = null;
    const draw = async () => {
      try {
        pdfPage = await pdf.getPage(page);
        if (!active || !canvasRef.current) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const frameWidth = Math.max(280, (frameRef.current?.clientWidth || 640) - 24);
        const scale = fitWidth ? frameWidth / base.width : zoom;
        const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
        const viewport = pdfPage.getViewport({ scale: scale * pixelRatio });
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d", { alpha: false });
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        canvas.style.width = `${Math.ceil(viewport.width / pixelRatio)}px`;
        canvas.style.height = `${Math.ceil(viewport.height / pixelRatio)}px`;
        canvas.style.maxWidth = "100%";
        renderTask = pdfPage.render({ canvasContext: context, viewport });
        await renderTask.promise;
      } catch (error) {
        if (active && error?.name !== "RenderingCancelledException") {
          setStatus("error");
          setMessage("Halaman PDF gagal ditampilkan.");
        }
      }
    };
    draw();
    return () => {
      active = false;
      renderTask?.cancel?.();
      pdfPage?.cleanup?.();
    };
  }, [pdf, page, zoom, fitWidth]);

  useEffect(() => {
    if (!blob || !isImage(contentType, name)) {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = "";
      }
      setObjectUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(blob);
    objectUrlRef.current = url;
    setObjectUrl(url);
    return () => {
      URL.revokeObjectURL(url);
      if (objectUrlRef.current === url) objectUrlRef.current = "";
    };
  }, [blob, contentType, name]);

  const download = async () => {
    const file = await api.downloadSalesReceiptPaymentProof(receipt.id, true);
    const url = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const pdfMode = blob && isPdf(contentType, name);
  const imageMode = blob && isImage(contentType, name);

  return (
    <div
      className="modal-overlay"
      data-testid="receipt-proof-viewer"
      role="dialog"
      aria-modal="true"
      aria-label="Bukti pembayaran"
      style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.55)", zIndex: 120, display: "flex", justifyContent: "center", padding: 16 }}
    >
      <div style={{ background: "#fff", width: "min(960px, 100%)", maxHeight: "100%", display: "flex", flexDirection: "column", borderRadius: 10, overflow: "hidden" }}>
        <header style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", padding: "10px 14px", borderBottom: "1px solid #e5e7eb", background: "#f8fafc" }}>
          <strong style={{ fontSize: 14 }}>Bukti pembayaran</strong>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" data-testid="btn-download-receipt-proof" onClick={download} style={{ border: "1px solid #cbd5e1", background: "#fff", borderRadius: 6, padding: "6px 10px" }}>
              Unduh
            </button>
            <button type="button" data-testid="btn-close-receipt-proof" onClick={onClose} style={{ border: "none", background: "#047857", color: "#fff", borderRadius: 6, padding: "6px 10px" }}>
              Tutup
            </button>
          </div>
        </header>
        {pdfMode && status === "ready" && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 14px", borderBottom: "1px solid #e5e7eb", flexWrap: "wrap" }}>
            <button type="button" data-testid="proof-pdf-prev" disabled={page <= 1} onClick={() => setPage((n) => Math.max(1, n - 1))}>Sebelumnya</button>
            <span data-testid="proof-pdf-page">Halaman {page} / {pageCount}</span>
            <button type="button" data-testid="proof-pdf-next" disabled={page >= pageCount} onClick={() => setPage((n) => Math.min(pageCount, n + 1))}>Berikutnya</button>
            <button type="button" data-testid="proof-pdf-zoom-out" onClick={() => { setFitWidth(false); setZoom((z) => Math.max(0.5, Math.round((z - 0.25) * 100) / 100)); }}>Zoom -</button>
            <button type="button" data-testid="proof-pdf-zoom-in" onClick={() => { setFitWidth(false); setZoom((z) => Math.min(3, Math.round((z + 0.25) * 100) / 100)); }}>Zoom +</button>
            <button type="button" data-testid="proof-pdf-fit" onClick={() => setFitWidth(true)}>Sesuaikan Lebar</button>
          </div>
        )}
        {imageMode && status === "ready" && (
          <div style={{ display: "flex", gap: 8, padding: "8px 14px", borderBottom: "1px solid #e5e7eb" }}>
            <button type="button" data-testid="proof-image-zoom-out" onClick={() => setZoom((z) => Math.max(0.5, Math.round((z - 0.25) * 100) / 100))}>Zoom -</button>
            <button type="button" data-testid="proof-image-zoom-in" onClick={() => setZoom((z) => Math.min(3, Math.round((z + 0.25) * 100) / 100))}>Zoom +</button>
          </div>
        )}
        <div ref={frameRef} data-testid="receipt-proof-stage" style={{ overflow: "auto", padding: 12, flex: 1, background: "#f1f5f9" }}>
          {status === "loading" && <p>Memuat bukti pembayaran...</p>}
          {status === "error" && (
            <div>
              <p data-testid="proof-load-error">{message}</p>
              <button type="button" data-testid="proof-retry" onClick={() => setAttempt((n) => n + 1)}>Coba Lagi</button>
            </div>
          )}
          {status === "denied" && <p data-testid="proof-access-denied">{message}</p>}
          {status === "rejected" && <p data-testid="proof-type-rejected">{message}</p>}
          {status === "ready" && pdfMode && <canvas ref={canvasRef} data-testid="proof-pdf-canvas" />}
          {status === "ready" && imageMode && objectUrl && (
            <img
              data-testid="proof-image"
              alt={name}
              src={objectUrl}
              style={{ display: "block", maxWidth: "100%", width: `${Math.round(zoom * 100)}%`, height: "auto", objectFit: "contain" }}
            />
          )}
          {status === "ready" && !pdfMode && !imageMode && (
            <p data-testid="proof-type-rejected">Jenis file tidak dapat ditampilkan sebagai bukti pembayaran.</p>
          )}
        </div>
      </div>
    </div>
  );
}
