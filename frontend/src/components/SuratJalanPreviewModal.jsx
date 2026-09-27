import { useEffect, useState } from "react";
import { formatDate } from "../lib/constants";
import SalesPwaPdfViewer from "../sales/SalesPwaPdfViewer";
import {
  downloadSuratJalanFile,
  fetchSuratJalanBlob,
  isPreviewableSj,
} from "../lib/suratJalan";

function previewFailMessage(err) {
  const status = err?.status;
  if (status === 403) return "File Surat Jalan tidak dapat ditampilkan. Izin ditolak.";
  if (status === 404) return "File Surat Jalan tidak dapat ditampilkan. File tidak ditemukan.";
  if (status === 502) return "File Surat Jalan tidak dapat ditampilkan. Penyimpanan tidak tersedia.";
  return "File Surat Jalan tidak dapat ditampilkan.";
}

export default function SuratJalanPreviewModal({
  open,
  order,
  entries = [],
  canDownload = false,
  onClose,
}) {
  const list = Array.isArray(entries) ? entries.filter((e) => e && (e.file_id || e.id)) : [];
  const [activeId, setActiveId] = useState(null);
  const [blobUrl, setBlobUrl] = useState("");
  const [pdfBlob, setPdfBlob] = useState(null);
  const [previewKind, setPreviewKind] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [fileAvailable, setFileAvailable] = useState(false);

  const active = list.find((e) => (e.file_id || e.id) === activeId) || list[0] || null;

  useEffect(() => {
    if (!open) return undefined;
    setActiveId(list[0] ? (list[0].file_id || list[0].id) : null);
    setErr("");
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, order?.id, list.length]);

  useEffect(() => {
    if (!open) return undefined;
    const body = document.body;
    const html = document.documentElement;
    const previous = {
      bodyOverflow: body.style.overflow,
      htmlOverflow: html.style.overflow,
    };
    const scrollX = window.scrollX || html.scrollLeft || 0;
    const scrollY = window.scrollY || html.scrollTop || 0;
    body.style.overflow = "hidden";
    html.style.overflow = "hidden";
    return () => {
      body.style.overflow = previous.bodyOverflow;
      html.style.overflow = previous.htmlOverflow;
      if (window.scrollX !== scrollX || window.scrollY !== scrollY) {
        window.scrollTo(scrollX, scrollY);
      }
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    const fileId = active?.file_id || active?.id;
    const kindHint = active ? isPreviewableSj(active) : null;
    if (!open || !fileId || !order?.id) return undefined;
    let cancelled = false;
    let created = "";
    (async () => {
      setBusy(true);
      setErr("");
      setFileAvailable(false);
      setPdfBlob(null);
      setPreviewKind(kindHint);
      try {
        const blob = await fetchSuratJalanBlob(order.id, fileId, {
          download: false,
          filename: active?.name,
        });
        if (cancelled) return;
        if (!blob || blob.size === 0) {
          setBlobUrl("");
          setPdfBlob(null);
          setErr("File Surat Jalan tidak dapat ditampilkan. File tidak ditemukan.");
          return;
        }
        setFileAvailable(true);
        const kind = isPreviewableSj({ ...active, content_type: blob.type || active?.content_type, name: active?.name });
        setPreviewKind(kind);
        if (kind === "pdf") {
          setPdfBlob(blob);
          setBlobUrl("");
        } else if (kind === "image") {
          created = URL.createObjectURL(blob);
          setBlobUrl(created);
          setPdfBlob(null);
        } else {
          setPdfBlob(null);
          setBlobUrl("");
          setErr("File Surat Jalan tidak dapat ditampilkan. Format tidak didukung.");
        }
      } catch (e) {
        if (!cancelled) {
          setBlobUrl("");
          setPdfBlob(null);
          setFileAvailable(e?.status !== 404 && e?.status !== 502);
          setErr(previewFailMessage(e));
        }
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
      setBlobUrl("");
      setPdfBlob(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, order?.id, activeId]);

  if (!open) return null;

  const loadFailed = Boolean(err) && !busy && !pdfBlob && !blobUrl;
  const showPreviewFrame = !loadFailed && (
    busy
    || (previewKind === "pdf" && pdfBlob)
    || (previewKind === "image" && blobUrl)
    || (!err && !previewKind)
  );

  const onDownload = async () => {
    if (!canDownload || !active) return;
    try {
      await downloadSuratJalanFile(order.id, active.file_id || active.id, active.name);
    } catch (e) {
      setErr(previewFailMessage(e));
    }
  };

  return (
    <div className={`modal sj-preview-modal${loadFailed ? " is-failed" : ""}`} role="dialog" aria-modal="true" data-testid="sj-preview-modal" onClick={onClose}>
      <div className="modal-inner modal-wide sj-preview-inner" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>SURAT JALAN</h3>
        </div>
        <div className="sj-preview-body">
          <dl className="confirm-action-dl" data-testid="sj-preview-meta">
            <div><dt>No PO</dt><dd>{order?.po_number || "—"}</dd></div>
            <div><dt>Customer</dt><dd>{order?.customer_name || order?.customer || "—"}</dd></div>
            <div><dt>Nama File</dt><dd data-testid="sj-preview-filename">{active?.name || "—"}</dd></div>
            <div><dt>Uploaded By</dt><dd>{active?.uploaded_by_name || active?.uploaded_by || "—"}</dd></div>
            <div><dt>Uploaded At</dt><dd className="mono">{active?.uploaded_at ? formatDate(active.uploaded_at) : "—"}</dd></div>
          </dl>

          {list.length > 1 && (
            <ul className="sj-preview-list" data-testid="sj-preview-file-list">
              {list.map((e) => {
                const id = e.file_id || e.id;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      className={`ghost sm${id === (active?.file_id || active?.id) ? " is-active" : ""}`}
                      onClick={() => setActiveId(id)}
                      data-testid={`sj-preview-pick-${id}`}
                    >
                      {e.name || "Surat Jalan"} · {e.uploaded_at ? formatDate(e.uploaded_at) : ""}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {err && <div className="error" data-testid="sj-preview-error">{err}</div>}

          {showPreviewFrame && (
            <div className="sj-preview-frame" data-testid="sj-preview-frame">
              {busy && <p className="muted small">Memuat preview…</p>}
              {!busy && previewKind === "pdf" && pdfBlob && !err && (
                <div className="sj-preview-pdf" data-testid="sj-preview-pdf" data-content-type="application/pdf">
                  <SalesPwaPdfViewer
                    key={active?.file_id || active?.id}
                    blob={pdfBlob}
                    label={active?.name || "Surat Jalan"}
                  />
                </div>
              )}
              {!busy && previewKind === "image" && blobUrl && !err && (
                <img
                  src={blobUrl}
                  alt={active?.name || "Surat Jalan"}
                  data-testid="sj-preview-image"
                  onError={() => setErr("File Surat Jalan tidak dapat ditampilkan. Format tidak didukung.")}
                />
              )}
              {!busy && !previewKind && !err && (
                <p className="muted" data-testid="sj-preview-unavailable">File Surat Jalan tidak dapat ditampilkan. Format tidak didukung.</p>
              )}
            </div>
          )}
        </div>
        <div className="delete-modal-actions sj-preview-footer" data-testid="sj-preview-footer">
          <button type="button" className="ghost" onClick={onClose} data-testid="sj-preview-close">Tutup</button>
          {canDownload && fileAvailable && (active?.file_id || active?.id) ? (
            <button type="button" className="primary" onClick={onDownload} data-testid="sj-preview-download">Download</button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
