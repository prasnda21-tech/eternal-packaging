import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import api from "../lib/api";
import { formatDate } from "../lib/constants";
import { useAuth } from "../lib/auth";
import RescheduleDeliveryModal from "../components/RescheduleDeliveryModal";
import PoIdentityBadges from "../components/PoIdentityBadges";

const EMPTY = { po: "", customer: "", status: "aktif" };

export default function DeliveryReschedulePage() {
  const { hasPerm } = useAuth();
  const canSetDate = hasPerm("delivery.reschedule.set_date");
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [filters, setFilters] = useState(EMPTY);
  const [setDateRow, setSetDateRow] = useState(null);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.deliveryReschedules();
      setRows(d.rows || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Jadwal Pengiriman Ulang"));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(() => {
    const po = (filters.po || "").trim().toLowerCase();
    const customer = (filters.customer || "").trim().toLowerCase();
    return (rows || []).filter((r) => {
      if (po && !String(r.order_number || "").toLowerCase().includes(po)) return false;
      if (customer && !String(r.customer_name || "").toLowerCase().includes(customer)) return false;
      if (filters.status === "aktif") {
        if (r.status === "pending") return !r.fulfills_reschedule_id;
        if (r.status === "scheduled") {
          const later = (rows || []).some((o) => o.order_id === r.order_id && o.created_at > r.created_at && o.status === "scheduled");
          return !later;
        }
        return false;
      }
      if (filters.status === "pending") return r.status === "pending";
      if (filters.status === "scheduled") return r.status === "scheduled";
      return true;
    });
  }, [rows, filters]);

  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="ship-status-page" data-testid="delivery-reschedule-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <p className="eyebrow">Produksi</p>
          <h1>Jadwal Pengiriman Ulang</h1>
        </header>
        <div className="ship-status-toolbar" data-testid="reschedule-filters">
          <label className="ship-filter-field">
            <span>No PO</span>
            <input type="text" value={filters.po} onChange={(e) => setFilter("po", e.target.value)} data-testid="dr-filter-po" />
          </label>
          <label className="ship-filter-field">
            <span>Customer</span>
            <input type="text" value={filters.customer} onChange={(e) => setFilter("customer", e.target.value)} data-testid="dr-filter-customer" />
          </label>
          <label className="ship-filter-field">
            <span>Status</span>
            <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)} data-testid="dr-filter-status">
              <option value="aktif">Aktif</option>
              <option value="pending">Menunggu Penjadwalan</option>
              <option value="scheduled">Terjadwal</option>
              <option value="semua">Riwayat</option>
            </select>
          </label>
          <button type="button" className="ghost sm" onClick={() => setFilters(EMPTY)} data-testid="dr-filter-reset">Reset</button>
          <button type="button" className="ship-icon-btn" onClick={load} disabled={busy} data-testid="dr-refresh">
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
        </div>
        {err && <div className="error">{err}</div>}
      </div>
      <div className="ship-status-grid">
        <table className="ship-table" data-testid="delivery-reschedule-table">
          <thead>
            <tr>
              <th>Tanggal Baru</th>
              <th>No PO</th>
              <th>Customer</th>
              <th>Tanggal Sebelumnya</th>
              <th>Alasan</th>
              <th>Diminta Oleh</th>
              <th>Dijadwalkan Oleh</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.reschedule_id} data-testid={`dr-row-${r.reschedule_id}`}>
                <td className="mono">{r.new_date ? formatDate(r.new_date, { dateOnly: true }) : "—"}</td>
                <td className="mono">
                  {r.order_id ? <Link to={`/po/${r.order_id}`}>{r.order_number}</Link> : r.order_number}{" "}
                  <PoIdentityBadges order={r} testIdPrefix={`dr-po-${r.order_number}`} />
                </td>
                <td>{r.customer_name || "-"}</td>
                <td className="mono">{r.previous_date ? formatDate(r.previous_date, { dateOnly: true }) : "—"}</td>
                <td>{r.reason || "—"}</td>
                <td>{r.requested_by_name || "—"}</td>
                <td>{r.scheduled_by_name || "—"}</td>
                <td>{r.status === "pending" ? "Menunggu Penjadwalan" : "Terjadwal"}</td>
                <td>
                  {canSetDate && r.status === "pending" && (
                    <button type="button" className="primary sm" onClick={() => setSetDateRow(r)} data-testid={`dr-set-date-${r.reschedule_id}`}>
                      Atur Jadwal
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!visible.length && !busy && (
              <tr><td colSpan={9} className="empty-state">Tidak ada penjadwalan ulang.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <RescheduleDeliveryModal
        open={!!setDateRow}
        order={setDateRow ? { id: setDateRow.order_id, po_number: setDateRow.order_number, customer_name: setDateRow.customer_name } : null}
        canSetDate={canSetDate}
        currentDate={setDateRow?.previous_date || setDateRow?.effective_delivery_date}
        initialReason={setDateRow?.reason || ""}
        confirmMode="set-date"
        rescheduleId={setDateRow?.reschedule_id}
        onCancel={() => setSetDateRow(null)}
        onDone={() => { setSetDateRow(null); load(); }}
      />
    </div>
  );
}
