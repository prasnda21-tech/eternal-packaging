import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { PRIORITY_OPTIONS, selectAllState, stopLocationOk, toggleSelectAll, outletAddressLabel } from "../lib/deliveryRoute";
import OutletLocationModal from "../components/OutletLocationModal";

export default function CreateDeliverySchedulePage() {
  const { hasPerm, user } = useAuth();
  const navigate = useNavigate();
  const canCreate = hasPerm("delivery.schedule.create");
  const canPublish = hasPerm("delivery.schedule.publish");
  const canSetLocation = hasPerm("delivery.location.manage") || hasPerm("delivery.schedule.create");
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState([]);
  const [edits, setEdits] = useState({});
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [locRow, setLocRow] = useState(null);
  const allRef = useRef(null);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.deliveryQueue();
      setRows(d.rows || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat antrian SIAP DIKIRIM"));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const allState = useMemo(() => selectAllState(rows, selected), [rows, selected]);
  useEffect(() => {
    if (allRef.current) allRef.current.indeterminate = allState.indeterminate;
  }, [allState.indeterminate]);

  const setEdit = (stopId, patch) => {
    setEdits((prev) => ({ ...prev, [stopId]: { ...(prev[stopId] || {}), ...patch } }));
  };

  const toggle = (row) => {
    if (row.occupied || !stopLocationOk(row)) return;
    setSelected((prev) => prev.includes(row.stop_id) ? prev.filter((id) => id !== row.stop_id) : [...prev, row.stop_id]);
  };

  const submit = async (publish) => {
    if (!canCreate) return;
    setBusy(true);
    setErr("");
    try {
      const stops = selected.map((id) => {
        const e = edits[id] || {};
        return { stop_id: id, priority: e.priority, outlet_closing_time: e.closing };
      });
      const created = await api.createDeliveryBatch({ delivery_date: date, stop_ids: selected, stops });
      const id = created.batch?.id;
      if (publish && canPublish && id) {
        await api.publishDeliveryBatch(id);
      }
      navigate(id ? `/produksi/jadwal-pengiriman/batch/${id}` : "/produksi/jadwal-pengiriman");
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal membuat jadwal"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ship-status-page" data-testid="create-delivery-schedule-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <p className="eyebrow">Produksi · Jadwal Pengiriman</p>
          <h1>Buat Jadwal Pengiriman</h1>
          <p className="subhead">Pilih stop SIAP DIKIRIM. Atur lokasi outlet sebelum optimize. Prioritas dan jam tutup terpisah dari koordinat.</p>
        </header>
        <div className="ship-status-toolbar">
          <label className="ship-filter-field">
            <span>Tanggal Pengiriman</span>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} data-testid="cds-date" />
          </label>
          <button type="button" className="ghost sm" onClick={() => setSelected(toggleSelectAll(rows, selected))} data-testid="cds-select-all">
            {allState.checked ? "Hapus Pilihan" : "Pilih Semua"}
          </button>
          <button type="button" className="ship-icon-btn" onClick={load} disabled={busy} data-testid="cds-refresh"><RefreshCw size={14} className={busy ? "spin" : ""} /></button>
          <span className="muted small">{selected.length} stop dipilih</span>
        </div>
        {err && <div className="error" data-testid="cds-error">{err}</div>}
      </div>
      <div className="ship-status-grid">
        <table className="ship-table" data-testid="cds-queue-table">
          <thead>
            <tr>
              <th>
                <input
                  ref={allRef}
                  type="checkbox"
                  checked={allState.checked}
                  onChange={() => setSelected(toggleSelectAll(rows, selected))}
                  data-testid="cds-check-all"
                  aria-label="Pilih semua"
                />
              </th>
              <th>No PO</th>
              <th>Customer</th>
              <th>Alamat / Outlet</th>
              <th>Qty</th>
              <th>Prioritas</th>
              <th>Jam Tutup</th>
              <th>Status Lokasi</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const e = edits[r.stop_id] || {};
              const locOk = stopLocationOk(r);
              const disabled = r.occupied || !locOk;
              return (
                <tr key={r.stop_id} data-testid={`cds-row-${r.stop_id}`} className={disabled ? "is-disabled" : ""}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.includes(r.stop_id)}
                      disabled={disabled}
                      onChange={() => toggle(r)}
                      data-testid={`cds-check-${r.stop_id}`}
                      title={r.exclude_reason || ""}
                    />
                  </td>
                  <td className="mono">{r.order_id ? <Link to={`/po/${r.order_id}`}>{r.order_number}</Link> : r.order_number}</td>
                  <td>{r.customer}</td>
                  <td data-testid={`cds-outlet-${r.stop_id}`}>{outletAddressLabel(r)}</td>
                  <td className="mono" data-testid={`cds-qty-${r.stop_id}`}>{r.qty ?? "—"}</td>
                  <td>
                    <select
                      value={e.priority || r.priority || "normal"}
                      onChange={(ev) => setEdit(r.stop_id, { priority: ev.target.value })}
                      data-testid={`cds-priority-${r.stop_id}`}
                    >
                      {PRIORITY_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                    </select>
                  </td>
                  <td>
                    <input
                      type="time"
                      value={e.closing || r.outlet_closing_time || ""}
                      onChange={(ev) => setEdit(r.stop_id, { closing: ev.target.value })}
                      data-testid={`cds-closing-${r.stop_id}`}
                      aria-label="Jam Tutup Outlet"
                    />
                  </td>
                  <td>
                    <span
                      className={locOk ? "route-loc-ok" : "route-loc-need"}
                      data-testid={`cds-loc-status-${r.stop_id}`}
                    >
                      {r.location_label || (locOk ? "Lokasi Siap" : "Belum Ada Lokasi")}
                    </span>
                    {!locOk && r.exclude_reason && (
                      <div className="muted small" data-testid={`cds-need-location-${r.stop_id}`}>{r.exclude_reason}</div>
                    )}
                  </td>
                  <td data-testid={`cds-status-${r.stop_id}`}>{r.status || (locOk ? "SIAP DIKIRIM" : "Belum Ada Lokasi")}</td>
                  <td>
                    {canSetLocation && (
                      <button
                        type="button"
                        className="ghost sm"
                        onClick={() => setLocRow(r)}
                        data-testid={`cds-set-location-${r.stop_id}`}
                      >
                        {locOk ? "Edit Lokasi" : "Atur Lokasi"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {!rows.length && !busy && (
              <tr><td colSpan={10} className="empty-state">Tidak ada pengiriman SIAP DIKIRIM.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="route-page-actions">
        <Link className="ghost" to="/produksi/jadwal-pengiriman">Batal</Link>
        <button type="button" className="primary" disabled={!canCreate || !selected.length || busy} onClick={() => submit(false)} data-testid="cds-save">
          Simpan Draf
        </button>
        <button type="button" className="primary" disabled={!canPublish || !selected.length || busy} onClick={() => submit(true)} data-testid="cds-publish">
          Publish ke Driver
        </button>
        <span className="muted small" data-testid="cds-actor">Penjadwal: {user?.name}</span>
      </div>
      <OutletLocationModal
        open={!!locRow}
        row={locRow}
        canEdit={canSetLocation}
        onClose={() => setLocRow(null)}
        onSaved={() => load()}
      />
    </div>
  );
}
