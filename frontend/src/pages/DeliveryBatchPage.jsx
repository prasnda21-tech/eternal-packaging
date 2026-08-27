import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { outletAddressLabel, stopLocationOk } from "../lib/deliveryRoute";
import OutletLocationModal from "../components/OutletLocationModal";

export default function DeliveryBatchPage() {
  const { batchId } = useParams();
  const { hasPerm } = useAuth();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [locRow, setLocRow] = useState(null);

  const canSetLocation = hasPerm("delivery.location.manage") || hasPerm("delivery.schedule.create");

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      setData(await api.getDeliveryBatch(batchId));
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat batch"));
    } finally {
      setBusy(false);
    }
  }, [batchId]);

  useEffect(() => { load(); }, [load]);

  const batch = data?.batch;
  const stops = batch?.stops || [];

  if (!batch && !err) return <div className="loading">Memuat...</div>;

  return (
    <div className="ship-status-page" data-testid="delivery-batch-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <p className="eyebrow">Produksi · Jadwal Pengiriman</p>
          <h1>Batch {batch?.delivery_date || ""}</h1>
          <p className="subhead">{stops.length} stop terjadwal</p>
        </header>
        {err && <div className="error" data-testid="dbatch-error">{err}</div>}
        <div className="ship-status-toolbar">
          <button type="button" className="ship-icon-btn" onClick={load} disabled={busy}><RefreshCw size={14} /></button>
        </div>
      </div>

      {stops.some((s) => !stopLocationOk(s) || s.location_status === "need_location" || s.lat == null) && (
        <div className="error" data-testid="dbatch-missing-location">
          Lokasi outlet belum diatur
          {stops.filter((s) => !stopLocationOk(s) || s.location_status === "need_location" || s.lat == null).map((s) => (
            canSetLocation ? (
              <button
                key={s.stop_id}
                type="button"
                className="ghost sm"
                onClick={() => setLocRow(s)}
                data-testid={`dbatch-queue-set-location-${s.stop_id}`}
              >
                Atur Lokasi · {s.customer || s.order_number}
              </button>
            ) : null
          ))}
        </div>
      )}

      <div className="ship-status-grid">
        <table className="ship-table" data-testid="dbatch-stops-table">
          <thead>
            <tr>
              <th>No PO</th>
              <th>Customer</th>
              <th>Alamat / Outlet</th>
              <th>Qty</th>
              <th>Status Lokasi</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {stops.map((s) => {
              const locOk = stopLocationOk(s) || (s.lat != null && s.lng != null);
              return (
                <tr key={s.stop_id} data-testid={`dbatch-stop-${s.stop_id}`}>
                  <td className="mono">
                    {s.order_id ? <Link to={`/po/${s.order_id}`}>{s.order_number}</Link> : (s.order_number || "—")}
                  </td>
                  <td>{s.customer || "—"}</td>
                  <td>{outletAddressLabel(s)}</td>
                  <td className="mono">{s.qty ?? "—"}</td>
                  <td>
                    <span className={locOk ? "route-loc-ok" : "route-loc-need"}>
                      {s.location_label || (locOk ? "Lokasi Siap" : "Belum Ada Lokasi")}
                    </span>
                  </td>
                  <td>
                    {canSetLocation && (
                      <button
                        type="button"
                        className="ghost sm"
                        onClick={() => setLocRow(s)}
                        data-testid={`dbatch-set-location-${s.stop_id}`}
                      >
                        {locOk ? "Edit Lokasi" : "Atur Lokasi"}
                      </button>
                    )}
                    {s.order_id && (
                      <Link className="ghost sm" to={`/po/${s.order_id}/kerja?divisi=terkirim`}>Kerjakan</Link>
                    )}
                  </td>
                </tr>
              );
            })}
            {!stops.length && !busy && (
              <tr><td colSpan={6} className="empty-state">Tidak ada stop pada batch ini.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <OutletLocationModal
        open={!!locRow}
        row={locRow}
        batchId={batchId}
        canEdit={canSetLocation}
        onClose={() => setLocRow(null)}
        onSaved={() => load()}
      />
      <p className="muted small"><Link to="/produksi/jadwal-pengiriman">Kembali ke Jadwal Pengiriman</Link></p>
    </div>
  );
}
