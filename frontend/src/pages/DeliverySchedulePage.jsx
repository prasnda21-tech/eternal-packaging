import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { DELIVERY_SCHEDULE_STATUS_OPTIONS, EMPTY_DELIVERY_FILTERS, matchesDeliveryScheduleFilters } from "../lib/deliverySchedule";

export default function DeliverySchedulePage() {
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("delivery.schedule.create");
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [filters, setFilters] = useState(EMPTY_DELIVERY_FILTERS);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.deliverySchedule();
      setRows(d.rows || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Jadwal Pengiriman"));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(
    () => (rows || []).filter((r) => matchesDeliveryScheduleFilters(r, filters)),
    [rows, filters],
  );

  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));
  const reset = () => setFilters(EMPTY_DELIVERY_FILTERS);

  return (
    <div className="ship-status-page" data-testid="delivery-schedule-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <p className="eyebrow">Produksi</p>
          <h1>Jadwal Pengiriman</h1>
        </header>
        {canCreate && (
          <div className="route-page-actions" style={{ marginBottom: 8 }}>
            <Link className="primary" to="/produksi/jadwal-pengiriman/buat" data-testid="ds-create-schedule">
              Buat Jadwal Pengiriman
            </Link>
          </div>
        )}
        <div className="ship-status-toolbar" data-testid="delivery-schedule-filters">
          <label className="ship-filter-field">
            <span>Tanggal Mulai</span>
            <input type="date" value={filters.dateFrom} onChange={(e) => setFilter("dateFrom", e.target.value)} data-testid="ds-filter-from" />
          </label>
          <label className="ship-filter-field">
            <span>Tanggal Akhir</span>
            <input type="date" value={filters.dateTo} onChange={(e) => setFilter("dateTo", e.target.value)} data-testid="ds-filter-to" />
          </label>
          <label className="ship-filter-field">
            <span>No PO</span>
            <input type="text" value={filters.po} onChange={(e) => setFilter("po", e.target.value)} placeholder="Cari No PO" data-testid="ds-filter-po" />
          </label>
          <label className="ship-filter-field">
            <span>Customer</span>
            <input type="text" value={filters.customer} onChange={(e) => setFilter("customer", e.target.value)} placeholder="Cari customer" data-testid="ds-filter-customer" />
          </label>
          <label className="ship-filter-field">
            <span>Status</span>
            <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)} data-testid="ds-filter-status">
              {DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
          </label>
          <button type="button" className="ghost sm" onClick={reset} data-testid="ds-filter-reset">Reset</button>
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" data-testid="ds-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
        </div>
        {err && <div className="error" data-testid="ds-error">{err}</div>}
      </div>
      <div className="ship-status-grid">
        <table className="ship-table" data-testid="delivery-schedule-table">
          <thead>
            <tr>
              <th>Tanggal Pengiriman</th>
              <th>No PO</th>
              <th>Customer</th>
              <th>Shipment / Item</th>
              <th>Deadline</th>
              <th>Jadwal Manual</th>
              <th>Status</th>
              <th>Driver</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.id} data-testid={`ds-row-${r.order_number}`}>
                <td className="mono">{formatDate(r.effective_delivery_date, { dateOnly: true })}</td>
                <td className="mono">
                  {r.id ? <Link to={`/po/${r.id}`}>{r.order_number}</Link> : (r.order_number || "-")}
                </td>
                <td>{r.customer_name || "-"}</td>
                <td>{r.item_label || "-"}</td>
                <td className="mono" data-testid={`ds-deadline-${r.order_number}`}>{formatDate(r.deadline, { dateOnly: true })}</td>
                <td className="mono" data-testid={`ds-manual-${r.order_number}`}>{r.jadwal_manual ? formatDate(r.jadwal_manual, { dateOnly: true }) : "—"}</td>
                <td data-testid={`ds-status-${r.order_number}`}>{r.status_label}</td>
                <td>{r.driver_name || "—"}</td>
                <td>
                  {r.id && <Link className="ghost sm" to={`/po/${r.id}/kerja?divisi=terkirim`}>Kerjakan</Link>}
                </td>
              </tr>
            ))}
            {!visible.length && !busy && (
              <tr><td colSpan={9} className="empty-state">Tidak ada jadwal pengiriman.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
