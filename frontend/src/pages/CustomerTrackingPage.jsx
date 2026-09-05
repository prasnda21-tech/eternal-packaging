import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import api, { publicApiUrl } from "../lib/api";
import BrandMark from "../components/BrandMark";
import { trackingDisplayLabel, trackingStatusClass } from "../lib/customerTracking";
import { displayOrDash, historyTimeLabel } from "../lib/deliveryHistory";

function DocActions({ doc, testIdPrefix }) {
  if (!doc) return null;
  const preview = publicApiUrl(doc.preview_url);
  const download = publicApiUrl(doc.download_url);
  return (
    <div className="tracking-doc-actions" data-testid={`${testIdPrefix}-actions`}>
      <a
        href={preview}
        target="_blank"
        rel="noopener noreferrer"
        className="ghost sm"
        data-testid={`${testIdPrefix}-open`}
      >
        Buka
      </a>
      <a
        href={download}
        className="ghost sm"
        download={doc.name || "file"}
        data-testid={`${testIdPrefix}-download`}
      >
        Unduh
      </a>
    </div>
  );
}

export default function CustomerTrackingPage() {
  const { token } = useParams();
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [data, setData] = useState(null);
  const [preview, setPreview] = useState(null);

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

  const proofs = (data?.delivery_proofs || []).slice(0, 5);
  const designDocs = data?.documents?.design_approved || [];
  const sjDocs = data?.documents?.surat_jalan || [];
  const shipmentsEvidence = data?.shipments_evidence || [];

  return (
    <main className="tracking-page" data-testid="customer-tracking-page">
      <div className="tracking-card">
        <header className="tracking-head">
          <BrandMark compact />
          <p className="eyebrow">Tracking Pesanan</p>
        </header>

        {loading && <p className="muted" data-testid="tracking-loading">Memuat...</p>}

        {!loading && err && (
          <p className="tracking-err" data-testid="tracking-error">{err}</p>
        )}

        {!loading && data && (
          <>
            <div className="tracking-po" data-testid="tracking-customer">
              <span className="lbl">Nama Customer</span>
              <strong data-testid="tracking-customer-name">{data.customer_name || "—"}</strong>
            </div>
            <div className="tracking-po" data-testid="tracking-po">
              <span className="lbl">No PO</span>
              <strong data-testid="tracking-order-number">{data.order_number}</strong>
            </div>
            {data.delivery_reschedule?.scheduled_date && (
              <div className="tracking-po" data-testid="tracking-reschedule">
                <span className="lbl">Pengiriman dijadwalkan ulang</span>
                <strong data-testid="tracking-reschedule-date">
                  Tanggal: {data.delivery_reschedule.scheduled_date}
                </strong>
              </div>
            )}
            <section className="tracking-workflow" data-testid="tracking-workflow">
              <h2>Alur Kerja</h2>
              <table className="tracking-table">
                <thead>
                  <tr>
                    <th>Tahap</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.workflow || []).map((row) => (
                    <tr key={row.stage} data-testid={`tracking-stage-${row.stage}`}>
                      <td>{row.stage}</td>
                      <td>
                        <span className={trackingStatusClass(row.status)}>
                          {trackingDisplayLabel(row)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {(designDocs.length > 0 || sjDocs.length > 0) && (
              <section className="tracking-docs" data-testid="tracking-documents">
                <h2>Dokumen</h2>
                {designDocs.length > 0 && (
                  <div className="tracking-doc-group" data-testid="tracking-design-approved">
                    <h3>Design Approved</h3>
                    {designDocs.map((d) => (
                      <article key={d.id} className="tracking-doc-row" data-testid={`tracking-design-${d.id}`}>
                        <strong>{d.name || "Design"}</strong>
                        <DocActions doc={d} testIdPrefix={`tracking-design-${d.id}`} />
                      </article>
                    ))}
                  </div>
                )}
                {sjDocs.length > 0 && (
                  <div className="tracking-doc-group" data-testid="tracking-surat-jalan">
                    <h3>Surat Jalan</h3>
                    {sjDocs.map((d) => (
                      <article key={d.id} className="tracking-doc-row" data-testid={`tracking-sj-${d.id}`}>
                        <strong>{d.sj_number || d.name || "Surat Jalan"}</strong>
                        {d.has_signed && <span className="muted small"> (bertanda tangan)</span>}
                        <DocActions doc={d} testIdPrefix={`tracking-sj-${d.id}`} />
                      </article>
                    ))}
                  </div>
                )}
              </section>
            )}

            {(data.delivery_history || []).length > 0 && (
              <section className="tracking-proofs" data-testid="tracking-delivery-history">
                <h2>Riwayat Pengiriman</h2>
                {(data.delivery_history || []).map((row, i) => (
                  <article key={row.time || i} className="delivery-history-entry" data-testid={`tracking-history-row-${i}`}>
                    <dl className="delivery-history-dl">
                      <dt>Nama Driver</dt><dd data-testid={`tracking-history-driver-${i}`}>{displayOrDash(row.driver_name)}</dd>
                      <dt>Barang / Item</dt><dd data-testid={`tracking-history-item-${i}`}>{displayOrDash(row.item)}</dd>
                      <dt>Qty</dt><dd data-testid={`tracking-history-qty-${i}`}>{row.qty != null ? Number(row.qty).toLocaleString("id-ID") : "—"}</dd>
                      <dt>Waktu</dt><dd data-testid={`tracking-history-time-${i}`}>{historyTimeLabel(row.time)}</dd>
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
                          {historyTimeLabel(rec?.recipient_signed_at || ev.time)}
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

            {shipmentsEvidence.length === 0 && (
              <section className="tracking-proofs" data-testid="tracking-proofs">
                <h2>Bukti Pengiriman</h2>
                {proofs.length === 0 ? (
                  <p className="muted small">Belum ada bukti kirim.</p>
                ) : (
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
                )}
              </section>
            )}
          </>
        )}
      </div>
      {preview && (
        <div className="tracking-lightbox" data-testid="tracking-lightbox" onClick={() => setPreview(null)}>
          <img src={preview} alt="Pratinjau" />
        </div>
      )}
    </main>
  );
}
