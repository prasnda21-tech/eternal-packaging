import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MapPinned, Plus } from "lucide-react";
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

export default function OptimasiRute() {
  const { hasPerm } = useAuth();
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const canCreate = hasPerm(ROUTE_PERMS.create) || hasPerm(ROUTE_PERMS.manage);

  useEffect(() => {
    let cancelled = false;
    api.listIndependentRoutes()
      .then((r) => { if (!cancelled) setRows(r.routes || []); })
      .catch((e) => { if (!cancelled) setError(e?.response?.data?.detail || "Gagal memuat rute."); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="iro-page" data-testid="iro-list-page">
      <div className="iro-hero">
        <div>
          <p className="eyebrow">Perencana rute mandiri</p>
          <h1>Optimasi Rute</h1>
          <p className="subhead">Buat rencana rute, tentukan titik customer secara manual, lalu optimasi urutan dengan prioritas keras. Tidak terhubung ke Pesanan Penjualan atau Pengiriman.</p>
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
              <tr><td colSpan={11} className="iro-empty">Belum ada rute. Buat rencana baru tanpa butuh pengiriman aktif.</td></tr>
            ) : rows.map((row) => (
              <tr key={row.id}>
                <td><strong>{row.name}</strong></td>
                <td>{row.created_by?.name || "—"}</td>
                <td>{row.route_date || "—"}</td>
                <td className="num">{row.stop_count || 0}</td>
                <td>{startModeLabel(row.start_mode)}</td>
                <td>{endModeLabel(row.end_mode)}</td>
                <td>{strategyLabel(row.strategy)}</td>
                <td><span className={`iro-status is-${(row.status || "draft").toLowerCase()}`}>{statusLabel(row.status)}</span></td>
                <td>{row.last_optimized_at ? String(row.last_optimized_at).replace("T", " ").slice(0, 16) : "—"}</td>
                <td>{formatKm(row.estimated_distance_m)}</td>
                <td>
                    <Link className="iro-link" to={`/optimasi-rute/${row.id}`} data-testid={`iro-open-${row.id}`}>Buka</Link>
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
