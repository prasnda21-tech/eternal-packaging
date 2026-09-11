import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MapPinned, Plus, Trash2 } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  ROUTE_PERMS,
  endModeLabel,
  formatKm,
  startModeLabel,
  statusLabel,
  strategyLabel,
} from "../lib/independentRoute";
import { sourceTypeLabel } from "../lib/deliveryRouteBridge";
import { navigateToOptimasiRute, preferMobileOpsShell } from "../lib/mobileOpsShell";
import MobileTopBar from "../mobile/MobileTopBar";

export default function OptimasiRute() {
  const { hasPerm } = useAuth();
  const navigate = useNavigate();
  const inMobileShell = preferMobileOpsShell();
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const canCreate = hasPerm(ROUTE_PERMS.create) || hasPerm(ROUTE_PERMS.manage);
  const canDelete = hasPerm(ROUTE_PERMS.editOwn) || hasPerm(ROUTE_PERMS.manage);

  const load = () => api.listIndependentRoutes()
    .then((r) => setRows(r.routes || []))
    .catch((e) => setError(e?.response?.data?.detail || "Gagal memuat rute."));

  useEffect(() => {
    let cancelled = false;
    api.listIndependentRoutes()
      .then((r) => { if (!cancelled) setRows(r.routes || []); })
      .catch((e) => { if (!cancelled) setError(e?.response?.data?.detail || "Gagal memuat rute."); });
    return () => { cancelled = true; };
  }, []);

  const onDelete = async (row) => {
    if (!canDelete || !row?.id) return;
    const ok = window.confirm(
      "Hapus rute optimasi ini?\n\n"
      + "• Data optimasi rute akan dihapus\n"
      + "• Pengiriman TIDAK dihapus\n"
      + "• Surat Jalan TIDAK dihapus\n"
      + "• Packing / Loading / PO TIDAK diubah",
    );
    if (!ok) return;
    setBusyId(row.id);
    setError("");
    try {
      await api.deleteIndependentRoute(row.id);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || "Gagal menghapus rute.");
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className={`iro-page${inMobileShell ? " iro-page--mobile" : ""}`} data-testid="iro-list-page">
      {inMobileShell ? <MobileTopBar title="Optimasi Rute" /> : null}
      <div className="iro-hero">
        <div>
          <p className="eyebrow">Perencana rute</p>
          <h1>Optimasi Rute</h1>
          <p className="subhead">
            Buat rencana rute manual atau dari Jadwal Pengiriman, lalu optimasi urutan dengan prioritas keras.
            Rute terhubung ke Pengiriman hanya jika dibuat dari Jadwal Pengiriman.
          </p>
        </div>
        {canCreate && (
          <button type="button" className="iro-btn-primary" data-testid="iro-create-btn" onClick={() => navigate("/optimasi-rute/baru")}>
            <Plus size={16} /> Buat Rute
          </button>
        )}
      </div>

      {error ? <div className="iro-alert is-error">{error}</div> : null}

      <div className="table-wrap">
        <table className="ship-table iro-table" data-testid="iro-route-table">
          <thead>
            <tr>
              <th>Nama rute</th>
              <th>Sumber</th>
              <th>Pembuat</th>
              <th>Tanggal</th>
              <th>Stop</th>
              <th>Start</th>
              <th>End</th>
              <th>Strategi</th>
              <th>Status</th>
              <th>Terakhir optimasi</th>
              <th>Estimasi jarak</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={12} className="iro-empty">Belum ada rute. Buat rencana baru atau optimasi dari Jadwal Pengiriman.</td></tr>
            ) : rows.map((row) => (
              <tr key={row.id}>
                <td><strong>{row.name}</strong></td>
                <td>
                  <span className={`iro-source is-${String(row.source_type || "manual").toLowerCase()}`} data-testid={`iro-source-${row.id}`}>
                    {sourceTypeLabel(row.source_type)}
                  </span>
                </td>
                <td>{row.created_by?.name || "—"}</td>
                <td>{row.route_date || "—"}</td>
                <td className="num">{row.stop_count || 0}</td>
                <td>{startModeLabel(row.start_mode)}</td>
                <td>{endModeLabel(row.end_mode)}</td>
                <td>{strategyLabel(row.strategy)}</td>
                <td><span className={`iro-status is-${(row.status || "draft").toLowerCase()}`}>{statusLabel(row.status)}</span></td>
                <td>{row.last_optimized_at ? String(row.last_optimized_at).replace("T", " ").slice(0, 16) : "—"}</td>
                <td>{formatKm(row.estimated_distance_m)}</td>
                <td className="iro-row-actions">
                  <button
                    type="button"
                    className="iro-link"
                    data-testid={`iro-open-${row.id}`}
                    onClick={() => navigateToOptimasiRute(navigate, row.id)}
                  >
                    Buka
                  </button>
                  {canDelete && (
                    <button
                      type="button"
                      className="iro-linkish iro-danger"
                      data-testid={`iro-delete-${row.id}`}
                      disabled={busyId === row.id}
                      onClick={() => onDelete(row)}
                    >
                      <Trash2 size={14} /> Hapus Rute
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="iro-footnote"><MapPinned size={14} /> Jarak yang ditampilkan adalah estimasi antar titik, bukan jarak jalan aktual.</p>
    </div>
  );
}
