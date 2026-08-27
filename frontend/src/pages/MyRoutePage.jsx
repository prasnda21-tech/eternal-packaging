import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Navigation, RefreshCw } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDuration, formatKm, openGoogleMapsNavigation, remainingStops } from "../lib/deliveryRoute";
import ConfirmActionModal from "../components/ConfirmActionModal";

export default function MyRoutePage() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [startFor, setStartFor] = useState(null);

  const load = useCallback(async () => {
    setBusy(true); setErr("");
    try {
      setData(await api.myDeliveryRoutes());
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat Rute Saya");
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const me = data?.me || { name: user?.name };
  const routes = data?.routes || [];
  const batches = data?.published_batches || [];

  const start = async () => {
    if (!startFor) return;
    setBusy(true); setErr("");
    try {
      await api.startRoutePlan(startFor.plan_id);
      setStartFor(null);
      await load();
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal mulai rute");
    } finally { setBusy(false); }
  };

  const navigateStop = (stop) => {
    openGoogleMapsNavigation(stop);
  };

  return (
    <div className="ship-status-page" data-testid="my-route-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <p className="eyebrow">Produksi</p>
          <h1>Rute Saya</h1>
        </header>
        <div className="route-my-header" data-testid="my-route-header">
          <div><span>Nama Driver</span><strong data-testid="my-route-driver-name">{me.name || user?.name || "—"}</strong></div>
        </div>
        {err && <div className="error">{err}</div>}
        <button type="button" className="ship-icon-btn" onClick={load} disabled={busy} data-testid="my-route-refresh"><RefreshCw size={14} /></button>
      </div>

      {routes.map((r) => {
        const rem = remainingStops(r.stops);
        return (
          <section key={r.assignment_id} className="route-card" data-testid={`my-route-${r.assignment_id}`}>
            <header className="route-my-header">
              <div><span>Nama Driver</span><strong>{r.driver_name}</strong></div>
              <div><span>Kendaraan</span><strong>{r.vehicle_name || "—"}</strong></div>
              <div><span>Plat</span><strong data-testid="my-route-plate">{r.vehicle_plate || "—"}</strong></div>
              <div><span>Tanggal</span><strong>{r.delivery_date}</strong></div>
              <div><span>Jumlah Stop</span><strong>{r.stop_count}</strong></div>
              <div><span>Sisa Stop</span><strong data-testid="my-route-remaining">{r.remaining_count ?? rem.length}</strong></div>
              <div><span>Estimasi</span><strong>{formatKm(r.distance_meters)} · {formatDuration(r.duration_seconds)}</strong></div>
            </header>
            {r.status !== "berjalan" && r.status !== "selesai" && (
              <button type="button" className="primary" onClick={() => setStartFor(r)} data-testid="my-route-start">MULAI RUTE</button>
            )}
            <ol className="route-stop-list" data-testid="my-route-stops">
              {(r.stops || []).map((s) => (
                <li key={s.stop_id} data-testid={`my-route-stop-${s.stop_id}`}>
                  <div>
                    <strong>{s.sequence}. {s.customer}</strong>
                    <p className="muted small">{s.address}</p>
                    <p className="muted small">No PO {s.order_number} · Qty {s.qty}{s.outlet_closing_time ? ` · Jam Tutup ${s.outlet_closing_time}` : ""} · {s.priority_label || s.priority} · {s.status}</p>
                  </div>
                  <div className="route-stop-actions">
                    <button
                      type="button"
                      className="ghost sm"
                      onClick={() => navigateStop(s)}
                      disabled={!s.place_id && !s.google_place_id && s.lat == null && !s.address}
                      data-testid={`my-route-nav-${s.stop_id}`}
                    >
                      <Navigation size={14} /> Navigasi
                    </button>
                    {s.order_id && (
                      <Link className="primary sm" to={`/po/${s.order_id}/kerja?divisi=terkirim`} data-testid={`my-route-proof-${s.stop_id}`}>
                        Bukti Kirim
                      </Link>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </section>
        );
      })}

      {!routes.length && (
        <p className="muted" data-testid="my-route-empty">Belum ada rute resmi. Konfirmasi jadwal diperlukan. Batch ter-publish bisa diisi kendaraan di bawah.</p>
      )}

      {!!batches.length && (
        <section className="route-card" data-testid="my-route-published">
          <h2>Batch dipublish</h2>
          <ul>
            {batches.map((b) => (
              <li key={b.id}>
                <Link to={`/produksi/jadwal-pengiriman/batch/${b.id}`}>{b.delivery_date} · {b.stop_count} stop</Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <ConfirmActionModal
        open={!!startFor}
        title="MULAI RUTE"
        stage="Pengiriman"
        name={me.name || user?.name}
        extra={<p>Mulai rute tidak mengaktifkan GPS. Status menjadi BERJALAN.</p>}
        confirmLabel="Mulai Rute"
        busy={busy}
        error={err}
        onCancel={() => setStartFor(null)}
        onConfirm={start}
      />
    </div>
  );
}
