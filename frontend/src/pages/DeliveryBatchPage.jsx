import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatKm, formatDuration } from "../lib/deliveryRoute";
import RouteMap from "../components/RouteMap";
import ConfirmActionModal from "../components/ConfirmActionModal";

export default function DeliveryBatchPage() {
  const { batchId } = useParams();
  const { hasPerm, user } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [vehicles, setVehicles] = useState([]);
  const [vehicleId, setVehicleId] = useState("");
  const [newVeh, setNewVeh] = useState({ name: "", plate: "", capacity_dus: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [summary, setSummary] = useState(null);

  const canOptimize = hasPerm("delivery.route.optimize");
  const canEdit = hasPerm("delivery.route.edit");
  const canConfirm = hasPerm("delivery.route.confirm");
  const canReady = hasPerm("delivery.update") || hasPerm("delivery.vehicle.manage");
  const canPublish = hasPerm("delivery.schedule.publish");

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const [b, v] = await Promise.all([api.getDeliveryBatch(batchId), api.deliveryVehicles().catch(() => ({ vehicles: [] }))]);
      setData(b);
      setVehicles(v.vehicles || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat batch"));
    } finally {
      setBusy(false);
    }
  }, [batchId]);

  useEffect(() => { load(); }, [load]);

  const batch = data?.batch;
  const plan = data?.plan;
  const depot = data?.depot;

  const publish = async () => {
    setBusy(true); setErr("");
    try {
      await api.publishDeliveryBatch(batchId);
      await load();
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal publish");
    } finally { setBusy(false); }
  };

  const ready = async () => {
    setBusy(true); setErr("");
    try {
      let vid = vehicleId;
      const payload = {};
      if (!vid && (newVeh.plate || newVeh.name)) {
        const created = await api.createDeliveryVehicle({
          name: newVeh.name,
          plate: newVeh.plate,
          capacity_dus: newVeh.capacity_dus ? Number(newVeh.capacity_dus) : null,
        });
        vid = created.vehicle?.id;
        payload.vehicle = created.vehicle;
      } else {
        payload.vehicle_id = vid;
      }
      await api.deliveryBatchReady(batchId, payload);
      await load();
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal menyimpan kendaraan");
    } finally { setBusy(false); }
  };

  const optimize = async () => {
    setBusy(true); setErr("");
    try {
      await api.optimizeDeliveryBatch(batchId);
      await load();
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal optimize rute");
    } finally { setBusy(false); }
  };

  const moveStop = async (stopId, assignmentId) => {
    if (!plan?.id || !canEdit) return;
    setBusy(true); setErr("");
    try {
      await api.patchRoutePlan(plan.id, { moves: [{ stop_id: stopId, assignment_id: assignmentId || null }] });
      await load();
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal mengubah draf");
    } finally { setBusy(false); }
  };

  const openConfirm = async () => {
    try {
      const d = await api.confirmRoutePreview(plan.id);
      setSummary(d.summary);
      setConfirmOpen(true);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat ringkasan");
    }
  };

  const doConfirm = async () => {
    setBusy(true); setErr("");
    try {
      await api.confirmRoutePlan(plan.id);
      setConfirmOpen(false);
      navigate("/produksi/rute-saya");
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal konfirmasi jadwal");
    } finally { setBusy(false); }
  };

  if (!batch && !err) return <div className="loading">Memuat...</div>;

  return (
    <div className="ship-status-page" data-testid="delivery-batch-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <p className="eyebrow">Produksi · Jadwal Pengiriman</p>
          <h1>Batch {batch?.delivery_date || ""}</h1>
          <p className="subhead">Status: {batch?.status} · {batch?.stop_count || 0} stop</p>
        </header>
        {err && <div className="error" data-testid="dbatch-error">{err}</div>}
        <div className="ship-status-toolbar">
          <button type="button" className="ship-icon-btn" onClick={load} disabled={busy}><RefreshCw size={14} /></button>
          {canPublish && batch?.status === "draft" && (
            <button type="button" className="primary sm" onClick={publish} disabled={busy} data-testid="dbatch-publish">Publish ke Driver</button>
          )}
          {canOptimize && (
            <button type="button" className="primary sm" onClick={optimize} disabled={busy} data-testid="dbatch-optimize">OPTIMIZE RUTE</button>
          )}
          {canConfirm && plan?.status === "draft" && (
            <button type="button" className="primary sm" onClick={openConfirm} disabled={busy} data-testid="dbatch-confirm">KONFIRMASI JADWAL</button>
          )}
        </div>
      </div>

      <section className="route-card" data-testid="dbatch-driver-vehicle">
        <h2>Driver &amp; Kendaraan</h2>
        <p data-testid="dbatch-driver-name">Nama Driver: <strong>{user?.name}</strong> <span className="muted small">(otomatis dari akun login)</span></p>
        {canReady && batch?.status === "published" && (
          <div className="route-vehicle-form">
            <label>Kendaraan
              <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)} data-testid="dbatch-vehicle-select">
                <option value="">— pilih —</option>
                {vehicles.map((v) => (
                  <option key={v.id} value={v.id}>{v.name} · {v.plate}</option>
                ))}
              </select>
            </label>
            <label>Nama kendaraan baru
              <input value={newVeh.name} onChange={(e) => setNewVeh({ ...newVeh, name: e.target.value })} data-testid="dbatch-vehicle-name" />
            </label>
            <label>Plat
              <input value={newVeh.plate} onChange={(e) => setNewVeh({ ...newVeh, plate: e.target.value })} data-testid="dbatch-vehicle-plate" />
            </label>
            <label>Kapasitas dus
              <input value={newVeh.capacity_dus} onChange={(e) => setNewVeh({ ...newVeh, capacity_dus: e.target.value })} data-testid="dbatch-vehicle-cap" />
            </label>
            <button type="button" className="primary sm" onClick={ready} disabled={busy} data-testid="dbatch-ready">Siap Berangkat</button>
          </div>
        )}
        <ul data-testid="dbatch-ready-list">
          {(batch?.driver_readiness || []).map((r) => (
            <li key={r.driver_user_id}>{r.driver_name} — {r.vehicle_plate || r.vehicle_name}</li>
          ))}
        </ul>
      </section>

      {plan && (
        <section className="route-card" data-testid="dbatch-draft-plan">
          <h2>Rencana Rute · {plan.status_label || plan.status}</h2>
          <p className="muted small">Hasil optimize disimpan. Tidak dipanggil ulang saat buka halaman.</p>
          <RouteMap depot={depot || plan.depot} routes={plan.routes} unassigned={plan.unassigned} />
          {(plan.routes || []).map((r) => (
            <div key={r.assignment_id} className="route-assign" data-testid={`dbatch-route-${r.driver_user_id}`}>
              <h3>{r.driver_name} — {r.vehicle_plate}</h3>
              <p className="muted small">{formatKm(r.distance_meters)} · {formatDuration(r.duration_seconds)} · {r.total_qty} qty{r.total_dus != null ? ` · ${r.total_dus} dus` : ""}</p>
              <ol>
                <li>Gudang</li>
                {(r.stops || []).map((s) => (
                  <li key={s.stop_id} data-testid={`dbatch-stop-${s.stop_id}`}>
                    {s.sequence}. {s.customer} · {s.order_number} · {s.priority_label || s.priority}
                    {s.outlet_closing_time ? ` · Tutup ${s.outlet_closing_time}` : ""}
                    {canEdit && plan.status === "draft" && (
                      <button type="button" className="ghost sm" onClick={() => moveStop(s.stop_id, null)} data-testid={`dbatch-remove-${s.stop_id}`}>Lepas</button>
                    )}
                  </li>
                ))}
                <li>Gudang</li>
              </ol>
            </div>
          ))}
          {(plan.unassigned || []).length > 0 && (
            <div data-testid="dbatch-unassigned">
              <h3>Unassigned</h3>
              <ul>
                {(plan.unassigned || []).map((s) => (
                  <li key={s.stop_id}>{s.customer || s.stop_id}: {s.unassigned_reason || "Tidak di-assign"}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <ConfirmActionModal
        open={confirmOpen}
        title="KONFIRMASI JADWAL"
        stage="Jadwal Pengiriman"
        name={user?.name}
        extra={summary && (
          <dl className="confirm-action-dl" data-testid="confirm-schedule-summary">
            <div><dt>Tanggal Pengiriman</dt><dd>{summary.delivery_date}</dd></div>
            <div><dt>Jumlah Driver/Kendaraan</dt><dd>{summary.vehicle_count}</dd></div>
            <div><dt>Total Stop</dt><dd>{summary.stop_count}</dd></div>
            <div><dt>Total Qty</dt><dd>{summary.qty}</dd></div>
            {summary.dus != null && <div><dt>Total Dus</dt><dd>{summary.dus}</dd></div>}
            <div><dt>Estimasi Jarak</dt><dd>{formatKm(summary.distance_meters)}</dd></div>
            <div><dt>Estimasi Durasi</dt><dd>{formatDuration(summary.duration_seconds)}</dd></div>
          </dl>
        )}
        confirmLabel="Konfirmasi Jadwal"
        busy={busy}
        error={err}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={doConfirm}
      />
      <p className="muted small"><Link to="/produksi/jadwal-pengiriman">Kembali ke Jadwal Pengiriman</Link></p>
    </div>
  );
}
