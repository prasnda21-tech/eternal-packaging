import { Fragment, useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Check, Copy, FileText, Image as ImageIcon } from "lucide-react";
import api, { publicApiUrl, publicBlobRequest } from "../lib/api";
import BrandMark from "../components/BrandMark";
import InAppDocumentViewer from "../components/InAppDocumentViewer";
import TrackingInvoices from "../components/TrackingInvoices";
import TrackingPaymentProofs from "../components/TrackingPaymentProofs";
import {
  approvalActionPath,
  compactItemSummary,
  currentHeadline,
  documentTiles,
  formatEstimasiPengiriman,
  formatItemQty,
  formatTrackingDate,
  hasApprovalCta,
  itemLineLabel,
  stageBlurb,
  railTone,
  stageShortLabel,
  trackingDisplayLabel,
  trackingStatusClass,
  workflowRailModel,
} from "../lib/customerTracking";
import { formatTrackingPageDateTime } from "../lib/constants";
import { displayOrDash } from "../lib/deliveryHistory";

function openPublicHref(href, { image, onPreview }) {
  const url = publicApiUrl(href);
  if (!url) return;
  if (image && onPreview) {
    onPreview(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

function RailNode({ row, x, y, testId, index }) {
  const tone = railTone(row?.status);
  const statusText = row?.status === "completed"
    ? "Selesai"
    : row?.status === "in_progress"
      ? "Sedang diproses"
      : row?.status === "rejected"
        ? "Perlu perhatian"
        : "Menunggu";
  return (
    <li
      className={`tracking-rail-node tracking-map-node is-${tone} is-${row?.status || "waiting"}`}
      style={{ left: `${x}%`, top: `${y}%` }}
      data-testid={testId || `tracking-stage-${row?.stage}`}
      data-rail-index={index}
      aria-label={`${row?.stage}: ${statusText}`}
    >
      <span className="tracking-map-dot" aria-hidden="true">
        {row?.status === "completed" ? <Check size={9} /> : null}
      </span>
      <em>{stageShortLabel(row?.stage)}</em>
    </li>
  );
}

function TrackingWorkflowRail({ workflow }) {
  const rail = workflowRailModel(workflow);
  return (
    <div className="tracking-rail" data-testid="tracking-workflow-rail" data-branched="0">
      <svg className="tracking-rail-svg" viewBox="0 0 1000 360" preserveAspectRatio="none" aria-hidden="true">
        <path
          className="rail-seg is-wait"
          d="M100 56 H900 C960 56 960 180 900 180 H100 C40 180 40 304 100 304 H900"
        />
        {rail.segments.map((seg) => (
          <path key={`${seg.from}-${seg.to}`} className={`rail-seg is-${seg.tone}`} d={seg.d} />
        ))}
      </svg>
      <ol className="tracking-rail-nodes" data-testid="tracking-rail-order">
        {rail.nodes.map((row) => (
          <RailNode
            key={row.stage}
            row={row}
            x={row.x}
            y={row.y}
            testId={`tracking-stage-${row.stage}`}
            index={row.displayIndex}
          />
        ))}
      </ol>
    </div>
  );
}

function TrackingSheet({ title, testId, onClose, children }) {
  return (
    <div className="tracking-sheet-root" data-testid={testId} onClick={onClose}>
      <div className="tracking-sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <header className="tracking-sheet-head">
          <h2>{title}</h2>
          <button type="button" className="ghost sm" onClick={onClose} data-testid={`${testId}-close`}>Tutup</button>
        </header>
        <div className="tracking-sheet-body">{children}</div>
      </div>
    </div>
  );
}

export default function CustomerTrackingPage() {
  const params = useParams();
  const token = String(params.code || params.token || "").trim();
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [data, setData] = useState(null);
  const [preview, setPreview] = useState(null);
  const [copied, setCopied] = useState(false);
  const [sheet, setSheet] = useState("");
  const [docViewer, setDocViewer] = useState(null);
  const loadTrackedDocument = useCallback(({ download = false, signal } = {}) => {
    const path = download ? docViewer?.downloadPath : docViewer?.previewPath;
    if (!path) {
      const error = new Error("missing");
      error.status = 404;
      return Promise.reject(error);
    }
    return publicBlobRequest(path, { signal });
  }, [docViewer?.downloadPath, docViewer?.previewPath]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr("");
    setData(null);
    api.publicTracking(token).then((d) => {
      if (!cancelled) setData(d);
    }).catch((e) => {
      if (cancelled) return;
      const status = e?.response?.status;
      if (status === 404) setErr("Tracking tidak ditemukan.");
      else setErr("Tracking tidak dapat dibuka.");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [token]);

  useEffect(() => {
    if (!sheet && !preview && !docViewer) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") {
        setSheet("");
        setPreview(null);
        setDocViewer(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheet, preview, docViewer]);

  const proofs = (data?.delivery_proofs || []).slice(0, 5);
  const designDocs = data?.documents?.design_approved || [];
  const sjDocs = data?.documents?.surat_jalan || [];
  const signedSjDocs = sjDocs.filter((d) => d?.has_signed);
  const shipmentsEvidence = data?.shipments_evidence || [];
  const summary = data?.summary || {};
  const items = summary.items || [];
  const itemSummary = compactItemSummary(items);
  const approval = data?.design_approval;
  const ctaPath = approvalActionPath(approval);
  const tiles = documentTiles(data);
  const headline = currentHeadline(data?.workflow, approval);
  const estimasiView = formatEstimasiPengiriman(summary.estimasi);
  const estimasi = estimasiView.text;

  const copyPo = async () => {
    const value = data?.order_number;
    if (!value || !navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  const openTrackedDocument = (doc, { title, fileName, kind, fallbackType }) => {
    const previewPath = doc?.preview_url || "";
    if (!previewPath) return;
    setDocViewer({
      key: `${kind}:${doc.id || previewPath}`,
      title,
      fileName,
      contentType: doc.content_type || fallbackType || "",
      previewPath,
      downloadPath: doc.download_url || previewPath,
      kind,
    });
  };

  const openDesignDocument = (doc) => {
    openTrackedDocument(doc, {
      title: doc?.name || "Design",
      fileName: doc?.name || "design",
      kind: "design",
      fallbackType: "",
    });
  };

  const openSuratJalanDocument = (doc) => {
    openTrackedDocument(doc, {
      title: doc?.sj_number || doc?.name || "Surat Jalan",
      fileName: doc?.name || "surat-jalan.pdf",
      kind: "sj",
      fallbackType: "application/pdf",
    });
  };

  const openTile = (tile) => {
    if (!tile?.available || !tile.href) return;
    if (tile.approval && ctaPath) {
      window.location.assign(ctaPath);
      return;
    }
    if (tile.key === "design") {
      const doc = designDocs.find((row) => row.preview_url === tile.href) || designDocs[0] || {
        preview_url: tile.href,
        content_type: tile.image ? "image/png" : "application/pdf",
        name: "Design",
      };
      openDesignDocument(doc);
      return;
    }
    if (tile.key === "sj") {
      const doc = sjDocs.find((row) => row.preview_url === tile.href) || sjDocs[0];
      if (doc) openSuratJalanDocument(doc);
      return;
    }
    if (tile.key === "ship") {
      setSheet("delivery");
      return;
    }
    openPublicHref(tile.href, { image: tile.image, onPreview: setPreview });
  };

  const renderDocTile = (tile) => {
    const shared = {
      className: `tracking-doc-tile${tile.available ? "" : " is-off"}`,
      "data-testid": `tracking-doc-tile-${tile.key}`,
      "aria-label": `${tile.label}: ${tile.status}${tile.available ? "" : ", tidak tersedia"}`,
    };
    const body = (
      <>
        <span className="tracking-doc-ico">
          {tile.kind === "pdf" ? <FileText size={14} /> : <ImageIcon size={14} />}
        </span>
        <strong>{tile.label}</strong>
        <span className="muted small">{tile.available ? `✓ ${tile.status}` : `○ ${tile.status}`}</span>
        <span className="tracking-doc-action">Lihat</span>
      </>
    );
    if (tile.available && tile.approval && ctaPath) {
      return (
        <a href={ctaPath} {...shared}>{body}</a>
      );
    }
    return (
      <button
        type="button"
        disabled={!tile.available}
        onClick={() => openTile(tile)}
        {...shared}
      >
        {body}
      </button>
    );
  };

  return (
    <main className="tracking-page is-onescreen" data-testid="customer-tracking-page">
      <header className="tracking-hero" data-testid="tracking-header">
        <BrandMark compact onDark />
        <div className="tracking-hero-copy">
          <h1>Live Tracking Pesanan</h1>
        </div>
      </header>

      <div className="tracking-shell">
        {loading && <p className="muted" data-testid="tracking-loading">Memuat...</p>}

        {!loading && err && (
          <p className="tracking-err" data-testid="tracking-error">{err}</p>
        )}

        {!loading && data && (
          <>
            <section className="tracking-card tracking-summary" data-testid="tracking-po-summary">
              <div className="tracking-summary-top">
                <div className="tracking-po" data-testid="tracking-po">
                  <span className="lbl">No. PO</span>
                  <div className="tracking-po-row">
                    <strong data-testid="tracking-order-number">{data.order_number}</strong>
                    <button
                      type="button"
                      className="tracking-copy"
                      onClick={copyPo}
                      data-testid="tracking-copy-po"
                      aria-label="Salin nomor PO"
                    >
                      {copied ? <Check size={14} /> : <Copy size={14} />}
                    </button>
                  </div>
                </div>
                <span className="tracking-status-pill" data-testid="tracking-status-badge">{headline}</span>
              </div>
              <div className="tracking-facts">
                <div className="tracking-po" data-testid="tracking-customer">
                  <span className="lbl">Customer</span>
                  <strong data-testid="tracking-customer-name">{data.customer_name || "—"}</strong>
                </div>
                <div className="tracking-fact-row" data-testid="tracking-item-0">
                  <span className="lbl">Item</span>
                  <strong>{itemSummary.item}</strong>
                </div>
                <div className="tracking-fact-row">
                  <span className="lbl">Qty</span>
                  <strong>{itemSummary.qty}</strong>
                </div>
                <div className="tracking-fact-row" data-testid="tracking-deadline">
                  <span className="lbl">Estimasi</span>
                  <strong data-testid="tracking-estimasi" data-estimasi-state={estimasiView.displayState}>
                    {estimasi || "—"}
                  </strong>
                </div>
              </div>
            </section>

            {hasApprovalCta(approval) && (
              <section className="tracking-action" data-testid="tracking-approval-card">
                <span className="tracking-action-badge">Perlu Tindakan Anda</span>
                <div className="tracking-action-body">
                  <div className="tracking-action-thumb" data-testid="tracking-approval-thumb">
                    {approval.preview_url ? (
                      <img src={publicApiUrl(approval.preview_url)} alt="" />
                    ) : (
                      <FileText size={18} />
                    )}
                  </div>
                  <div>
                    <h2>Approval Design</h2>
                    <p>Design pesanan Anda sudah siap untuk diperiksa.</p>
                  </div>
                  <a
                    className="tracking-cta"
                    href={ctaPath}
                    data-testid="tracking-approval-cta"
                  >
                    Lihat & Approval
                  </a>
                </div>
              </section>
            )}

            {approval?.status === "approved" && !hasApprovalCta(approval) && (
              <section className="tracking-action is-done" data-testid="tracking-approval-done">
                <h2>Design Disetujui</h2>
                {designDocs[0]?.preview_url && (
                  <button
                    type="button"
                    className="tracking-cta is-ghost"
                    data-testid="tracking-lihat-design"
                    onClick={() => openDesignDocument(designDocs[0])}
                  >
                    Lihat Design
                  </button>
                )}
              </section>
            )}

            {approval?.status === "rejected" && (
              <section className="tracking-action is-danger" data-testid="tracking-approval-revision">
                <h2>Revisi Diminta</h2>
              </section>
            )}

            <section className="tracking-card tracking-workflow" data-testid="tracking-workflow">
              <div className="tracking-map-head">
                <h2>Progress Pesanan</h2>
                <button type="button" className="tracking-link-btn" data-testid="tracking-open-timeline" onClick={() => setSheet("timeline")}>
                  Lihat Timeline
                </button>
              </div>
              <TrackingWorkflowRail workflow={data.workflow} />
            </section>

            <section className="tracking-card tracking-docs" data-testid="tracking-documents">
              <h2>Dokumen Pesanan</h2>
              <div className="tracking-doc-grid">
                {tiles.map((tile) => (
                  <Fragment key={tile.key}>
                    {tile.key === "payment" ? <TrackingPaymentProofs token={token} /> : renderDocTile(tile)}
                    {tile.key === "payment" ? <TrackingInvoices token={token} /> : null}
                  </Fragment>
                ))}
              </div>
              {signedSjDocs.length > 0 && (
                <div className="tracking-doc-group" data-testid="tracking-surat-jalan">
                  {signedSjDocs.map((d) => (
                    <article
                      key={d.id}
                      className="tracking-doc-row"
                      data-testid={`tracking-sj-${d.id}`}
                      data-shipment-id={d.shipment_id || ""}
                    >
                      <span className="tracking-doc-ico"><FileText size={16} /></span>
                      <div>
                        <strong>{d.sj_number || d.name || "Surat Jalan"}</strong>
                        <span className="muted small">
                          Bertanda tangan
                          {d.recipient_name ? ` · ${d.recipient_name}` : ""}
                          {d.signed_at ? ` · ${formatTrackingPageDateTime(d.signed_at) || "—"}` : ""}
                        </span>
                      </div>
                      <div className="tracking-doc-actions" data-testid={`tracking-sj-${d.id}-actions`}>
                        <button
                          type="button"
                          className="ghost sm"
                          data-testid={`tracking-sj-${d.id}-open`}
                          onClick={() => openSuratJalanDocument(d)}
                        >
                          Lihat PDF
                        </button>
                        <a
                          href={publicApiUrl(d.download_url)}
                          className="ghost sm"
                          download={d.name || "surat-jalan.pdf"}
                          data-testid={`tracking-sj-${d.id}-download`}
                        >
                          Unduh
                        </a>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>

            <nav className="tracking-utils" data-testid="tracking-help">
              <button type="button" data-testid="tracking-detail-toggle" onClick={() => setSheet("detail")}>Detail Pesanan</button>
              <button type="button" onClick={() => setSheet("timeline")}>Lihat Timeline</button>
              <button type="button" data-testid="tracking-open-help" onClick={() => setSheet("help")}>Bantuan</button>
            </nav>
            <p className="tracking-foot">© Eternal Packaging</p>
          </>
        )}
      </div>

      {sheet === "detail" && data && (
        <TrackingSheet title="Detail Pesanan" testId="tracking-detail" onClose={() => setSheet("")}>
          <div data-testid="tracking-detail-body">
            <dl className="tracking-dl">
              <div><dt>No. PO</dt><dd>{data.order_number}</dd></div>
              {summary.order_date && (
                <div><dt>Tanggal Order</dt><dd>{formatTrackingDate(summary.order_date)}</dd></div>
              )}
              <div><dt>Customer</dt><dd>{data.customer_name || "—"}</dd></div>
              {summary.outlet && (
                <div><dt>Outlet</dt><dd>{summary.outlet}</dd></div>
              )}
              {items.map((item, idx) => (
                <div key={`detail-item-${idx}`}>
                  <dt>Item {items.length > 1 ? idx + 1 : ""}</dt>
                  <dd>{itemLineLabel(item)} · {formatItemQty(item)}</dd>
                </div>
              ))}
              <div>
                <dt>Estimasi selesai</dt>
                <dd data-testid="tracking-detail-estimasi">{estimasi || "—"}</dd>
              </div>
              {(summary.estimasi?.jadwal_ulang || data.delivery_reschedule?.scheduled_date) && (
                <div data-testid="tracking-reschedule">
                  <dt>Pengiriman dijadwalkan ulang</dt>
                  <dd data-testid="tracking-reschedule-date">
                    Tanggal:{" "}
                    {formatTrackingDate(
                      summary.estimasi?.jadwal_ulang || data.delivery_reschedule?.scheduled_date
                    ) || "—"}
                  </dd>
                </div>
              )}
            </dl>
          </div>
        </TrackingSheet>
      )}

      {sheet === "timeline" && data && (
        <TrackingSheet title="Progress Pesanan" testId="tracking-timeline-sheet" onClose={() => setSheet("")}>
          <ol className="tracking-timeline" data-testid="tracking-timeline">
            {(data.workflow || []).map((row) => (
              <li
                key={row.stage}
                className={`tracking-tl-row is-${row.status}`}
              >
                <span className="tracking-tl-node" aria-hidden="true" />
                <div className="tracking-tl-copy">
                  <strong>{row.stage}</strong>
                  <span className={trackingStatusClass(row.status)}>
                    {row.stage === "Approval Design" && !approval?.status
                      ? "Menunggu link approval"
                      : trackingDisplayLabel(row)}
                  </span>
                  <em>{stageBlurb(row.stage, row, approval)}</em>
                </div>
              </li>
            ))}
          </ol>
        </TrackingSheet>
      )}

      {sheet === "help" && (
        <TrackingSheet title="Butuh Bantuan?" testId="tracking-help-sheet" onClose={() => setSheet("")}>
          <p>Tim kami siap membantu Anda.</p>
        </TrackingSheet>
      )}

      {sheet === "delivery" && data && (
        <TrackingSheet title="Bukti Pengiriman" testId="tracking-delivery-sheet" onClose={() => setSheet("")}>
          {(data.delivery_history || []).length > 0 && (
            <section className="tracking-proofs" data-testid="tracking-delivery-history">
              <h2>Riwayat Pengiriman</h2>
              {(data.delivery_history || []).map((row, i) => (
                <article key={row.time || i} className="delivery-history-entry" data-testid={`tracking-history-row-${i}`}>
                  <dl className="delivery-history-dl">
                    <dt>Nama Driver</dt><dd data-testid={`tracking-history-driver-${i}`}>{displayOrDash(row.driver_name)}</dd>
                    <dt>Barang / Item</dt><dd data-testid={`tracking-history-item-${i}`}>{displayOrDash(row.item)}</dd>
                    <dt>Qty</dt><dd data-testid={`tracking-history-qty-${i}`}>{row.qty != null ? Number(row.qty).toLocaleString("id-ID") : "—"}</dd>
                    <dt>Waktu</dt><dd data-testid={`tracking-history-time-${i}`}>{formatTrackingPageDateTime(row.time) || "—"}</dd>
                    <dt>Plat Kendaraan</dt><dd data-testid={`tracking-history-plate-${i}`}>{displayOrDash(row.vehicle_plate)}</dd>
                  </dl>
                  {(row.photos || []).length > 0 && (
                    <div className="delivery-history-photos">
                      {(row.photos || []).map((p) => (
                        <button
                          type="button"
                          key={p.id}
                          onClick={() => setPreview(publicApiUrl(p.url))}
                          data-testid={`tracking-history-photo-${p.id}`}
                        >
                          <img src={publicApiUrl(p.url)} alt="Bukti foto pengiriman" />
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              ))}
            </section>
          )}
          {shipmentsEvidence.length > 0 && (
            <section className="tracking-proofs" data-testid="tracking-shipments-evidence">
              <h2>Bukti Pengiriman</h2>
              {shipmentsEvidence.map((ev, i) => {
                const photos = (ev.photos || []).slice(0, 2);
                const rec = ev.recipient;
                const drv = ev.driver;
                return (
                  <article
                    key={ev.confirmation_id || ev.shipment_id || i}
                    className="delivery-history-entry"
                    data-testid={`tracking-evidence-${i}`}
                  >
                    <dl className="delivery-history-dl">
                      <dt>Nama Penerima</dt>
                      <dd data-testid={`tracking-evidence-recipient-${i}`}>
                        {displayOrDash(rec?.recipient_name)}
                      </dd>
                      <dt>Waktu Ditandatangani</dt>
                      <dd data-testid={`tracking-evidence-signed-at-${i}`}>
                        {formatTrackingPageDateTime(rec?.recipient_signed_at || ev.time) || "—"}
                      </dd>
                      {drv?.driver_name && (
                        <>
                          <dt>Nama Driver</dt>
                          <dd data-testid={`tracking-evidence-driver-${i}`}>
                            {displayOrDash(drv.driver_name)}
                          </dd>
                        </>
                      )}
                    </dl>
                    {photos.length > 0 && (
                      <div className="delivery-history-photos" data-testid={`tracking-evidence-photos-${i}`}>
                        {photos.map((p, pi) => (
                          <button
                            type="button"
                            key={p.id}
                            onClick={() => setPreview(publicApiUrl(p.url))}
                            data-testid={`tracking-evidence-photo-${i}-${pi}`}
                          >
                            <img src={publicApiUrl(p.url)} alt={`Foto bukti ${pi + 1}`} />
                          </button>
                        ))}
                      </div>
                    )}
                    {rec?.url && (
                      <div className="tracking-signature-preview" data-testid={`tracking-evidence-signature-${i}`}>
                        <span className="lbl">Tanda Tangan Penerima</span>
                        <button type="button" onClick={() => setPreview(publicApiUrl(rec.url))}>
                          <img src={publicApiUrl(rec.url)} alt="Tanda tangan penerima" />
                        </button>
                      </div>
                    )}
                    {drv?.url && (
                      <div className="tracking-signature-preview" data-testid={`tracking-evidence-driver-signature-${i}`}>
                        <span className="lbl">Tanda Tangan Driver</span>
                        <button type="button" onClick={() => setPreview(publicApiUrl(drv.url))}>
                          <img src={publicApiUrl(drv.url)} alt="Tanda tangan driver" />
                        </button>
                      </div>
                    )}
                    {ev.sj_file_id && (
                      <p className="muted small" data-testid={`tracking-evidence-sj-${i}`}>
                        Surat Jalan terkait: lihat bagian Dokumen
                      </p>
                    )}
                  </article>
                );
              })}
            </section>
          )}
          {shipmentsEvidence.length === 0 && proofs.length > 0 && (
            <section className="tracking-proofs" data-testid="tracking-proofs">
              <h2>Bukti Pengiriman</h2>
              <div className="tracking-proof-grid">
                {proofs.map((p) => (
                  <button
                    type="button"
                    key={p.id}
                    className="tracking-proof-thumb"
                    onClick={() => setPreview(publicApiUrl(p.url))}
                    data-testid={`tracking-proof-${p.id}`}
                  >
                    <img src={publicApiUrl(p.url)} alt="Bukti pengiriman" loading="lazy" />
                  </button>
                ))}
              </div>
            </section>
          )}
        </TrackingSheet>
      )}

      {sheet === "docs" && data && (
        <TrackingSheet title="Dokumen Pesanan" testId="tracking-docs-sheet" onClose={() => setSheet("")}>
          {designDocs.length > 0 && (
            <div className="tracking-doc-group" data-testid="tracking-design-approved">
              {designDocs.map((d) => (
                <article key={d.id} className="tracking-doc-row" data-testid={`tracking-design-${d.id}`}>
                  <span className="tracking-doc-ico"><ImageIcon size={16} /></span>
                  <div>
                    <strong>{d.name || "Design"}</strong>
                    <span className="muted small">Disetujui</span>
                  </div>
                  <div className="tracking-doc-actions" data-testid={`tracking-design-${d.id}-actions`}>
                    <button
                      type="button"
                      className="ghost sm"
                      data-testid={`tracking-design-${d.id}-open`}
                      onClick={() => openDesignDocument(d)}
                    >
                      Lihat
                    </button>
                    <a
                      href={publicApiUrl(d.download_url)}
                      className="ghost sm"
                      download={d.name || "file"}
                      data-testid={`tracking-design-${d.id}-download`}
                    >
                      Unduh
                    </a>
                  </div>
                </article>
              ))}
            </div>
          )}
          {sjDocs.some((d) => !d?.has_signed) && (
            <div className="tracking-doc-group" data-testid="tracking-surat-jalan-unsigned">
              {sjDocs.filter((d) => !d?.has_signed).map((d) => (
                <article key={d.id} className="tracking-doc-row" data-testid={`tracking-sj-${d.id}`}>
                  <span className="tracking-doc-ico"><FileText size={16} /></span>
                  <div>
                    <strong>{d.sj_number || d.name || "Surat Jalan"}</strong>
                    <span className="muted small">
                      Tersedia{d.has_signed ? " · bertanda tangan" : ""}
                    </span>
                  </div>
                  <div className="tracking-doc-actions" data-testid={`tracking-sj-${d.id}-actions`}>
                    <button
                      type="button"
                      className="ghost sm"
                      data-testid={`tracking-sj-${d.id}-open`}
                      onClick={() => openSuratJalanDocument(d)}
                    >
                      Lihat PDF
                    </button>
                    <a
                      href={publicApiUrl(d.download_url)}
                      className="ghost sm"
                      download={d.name || "surat-jalan.pdf"}
                      data-testid={`tracking-sj-${d.id}-download`}
                    >
                      Unduh
                    </a>
                  </div>
                </article>
              ))}
            </div>
          )}
        </TrackingSheet>
      )}

      {preview && (
        <div className="tracking-lightbox" data-testid="tracking-lightbox" onClick={() => setPreview(null)}>
          <img src={preview} alt="Pratinjau" />
        </div>
      )}
      {docViewer && (
        <InAppDocumentViewer
          documentKey={docViewer.key}
          title={docViewer.title}
          fileName={docViewer.fileName}
          contentType={docViewer.contentType}
          loadFile={loadTrackedDocument}
          allowDownload
          onClose={() => setDocViewer(null)}
          testId={docViewer.kind === "sj" ? "tracking-sj-viewer" : "tracking-design-viewer"}
        />
      )}
    </main>
  );
}
