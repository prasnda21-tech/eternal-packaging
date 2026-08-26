import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import api, { publicApiUrl } from "../lib/api";
import BrandMark from "../components/BrandMark";
import { trackingStatusClass, trackingStatusLabel } from "../lib/customerTracking";

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
