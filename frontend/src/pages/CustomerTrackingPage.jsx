import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import api, { publicApiUrl } from "../lib/api";
import BrandMark from "../components/BrandMark";
import { trackingStatusClass, trackingStatusLabel } from "../lib/customerTracking";
import { displayOrDash, historyTimeLabel } from "../lib/deliveryHistory";

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
                          {trackingStatusLabel(row.status)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
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
          </>
        )}
      </div>
      {preview && (
        <div className="tracking-lightbox" data-testid="tracking-lightbox" onClick={() => setPreview(null)}>
          <img src={preview} alt="Bukti pengiriman" />
        </div>
      )}
    </main>
  );
}
