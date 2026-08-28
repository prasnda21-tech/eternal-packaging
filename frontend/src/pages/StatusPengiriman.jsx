import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import api from "../lib/api";
import { clampPercent, formatShipDate, matchesShipFilters, terkirimTone } from "../lib/shipmentStatus";
import {
  headerCheckState,
  pruneSelection,
  selectAllVisible,
  toggleId,
  visibleRowIds,
} from "../lib/rowSelection";

function ProgressCell({ pct, tone, testid, title }) {
  const p = clampPercent(pct);
  return (
    <div className={"ship-bar ship-bar-" + tone} data-testid={testid} title={title}>
      <div className="ship-bar-fill" style={{ width: p + "%" }} />
      <span className="ship-bar-label">{p}%</span>
    </div>
  );
}

const EMPTY_FILTERS = { customer: "", po: "", orderDate: "", deliveryDate: "" };

export default function StatusPengiriman() {
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [selected, setSelected] = useState(new Set());
  const headerRef = useRef(null);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.shipmentStatus();
      setRows(d.rows || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Status Pengiriman"));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(
    () => (rows || []).filter((r) => matchesShipFilters(r, filters)),
    [rows, filters],
  );
  const visibleIds = useMemo(() => visibleRowIds(visible), [visible]);
  const visibleIdKey = visibleIds.join(",");

  useEffect(() => {
    setSelected((prev) => pruneSelection(prev, visibleIds));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleIdKey]);

  const headerState = headerCheckState(selected, visibleIds);
  useEffect(() => {
    if (headerRef.current) headerRef.current.indeterminate = headerState.indeterminate;
  }, [headerState.indeterminate]);

  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="ship-status-page" data-testid="shipment-status-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <h1>Status Pengiriman Barang</h1>
        </header>
        <div className="ship-status-toolbar" data-testid="ship-filter-bar">
          <label className="ship-filter-field">
            <span>Nama Customer</span>
            <input
              type="text"
              value={filters.customer}
              onChange={(e) => setFilter("customer", e.target.value)}
              placeholder="Cari customer"
              data-testid="ship-filter-customer"
            />
          </label>
          <label className="ship-filter-field">
            <span>No PO</span>
            <input
              type="text"
              value={filters.po}
              onChange={(e) => setFilter("po", e.target.value)}
              placeholder="Cari No PO"
              data-testid="ship-filter-po"
            />
          </label>
          <label className="ship-filter-field">
            <span>Tanggal Order</span>
            <input
              type="date"
              value={filters.orderDate}
              onChange={(e) => setFilter("orderDate", e.target.value)}
              data-testid="ship-filter-order-date"
            />
          </label>
          <label className="ship-filter-field">
            <span>Tanggal Pengiriman</span>
            <input
              type="date"
              value={filters.deliveryDate}
              onChange={(e) => setFilter("deliveryDate", e.target.value)}
              data-testid="ship-filter-delivery-date"
            />
          </label>
          <select disabled aria-label="Cabang" data-testid="ship-filter-cabang">
            <option>Semua Cabang</option>
          </select>
          <select disabled aria-label="Gudang" data-testid="ship-filter-gudang">
            <option>Semua Gudang</option>
          </select>
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" data-testid="ship-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
          <button type="button" className="ship-need-order" disabled title="Belum ada definisi Perlu Pesan di sistem" data-testid="ship-perlu-pesan">
            Perlu Pesan
          </button>
        </div>
        <p className="muted small" data-testid="ship-select-scope">
          Pilih Semua hanya untuk baris yang sedang tampil (filter aktif).
        </p>
        {selected.size > 0 && (
          <div className="bulk-bar" data-testid="ship-select-bar">
            <span data-testid="ship-selected-count"><b>{selected.size}</b> pengiriman dipilih</span>
            <button type="button" className="ghost sm" onClick={() => setSelected(new Set())} data-testid="ship-select-clear">
              Batal pilih
            </button>
          </div>
        )}
        {err && <div className="error" data-testid="ship-error">{err}</div>}
      </div>
      <div className="ship-status-grid" data-testid="ship-table">
        <table className="ship-table">
          <thead>
            <tr>
              <th className="ship-check">
                <input
                  ref={headerRef}
                  type="checkbox"
                  checked={headerState.checked}
                  disabled={!visibleIds.length}
                  onChange={() => setSelected(selectAllVisible(visibleIds, selected))}
                  aria-label="Pilih Semua (baris tampil)"
                  data-testid="ship-select-all"
                />
              </th>
              <th>Pelanggan</th>
              <th>No Pesanan #</th>
              <th>Tanggal</th>
              <th>Tgl Pengiriman</th>
              <th className="num">Terkirim</th>
              <th className="num">Dapat Dikirim</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.id} data-testid={`ship-row-${r.order_number}`} className={selected.has(r.id) ? "is-selected" : ""}>
                <td className="ship-check">
                  <input
                    type="checkbox"
                    checked={selected.has(r.id)}
                    onChange={() => setSelected(toggleId(selected, r.id))}
                    aria-label={`Pilih ${r.order_number}`}
                    data-testid={`ship-check-${r.order_number}`}
                  />
                </td>
                <td className="ship-cust">{r.customer_name || "-"}</td>
                <td className="mono">
                  {r.id ? (
                    <Link to={`/po/${r.id}`} data-testid={`ship-po-${r.order_number}`}>{r.order_number}</Link>
                  ) : (r.order_number || "-")}
                </td>
                <td className="mono">{formatShipDate(r.order_date)}</td>
                <td className="mono">{formatShipDate(r.delivery_date)}</td>
                <td>
                  <ProgressCell
                    pct={r.percent_delivered_display ?? r.percent_delivered}
                    tone={terkirimTone(r.percent_delivered_display ?? r.percent_delivered)}
                    testid={`ship-terkirim-${r.order_number}`}
                    title={`Terkirim ${r.qty_delivered ?? 0} / ${r.qty_total ?? 0}`}
                  />
                </td>
                <td>
                  <ProgressCell
                    pct={r.percent_ready_to_ship_display ?? r.percent_ready_to_ship}
                    tone="ready"
                    testid={`ship-dapat-${r.order_number}`}
                    title={`Dicetak operator ${r.qty_printed_operator ?? 0} / ${r.print_qty_total ?? 0}`}
                  />
                </td>
              </tr>
            ))}
            {!visible.length && !busy && (
              <tr><td colSpan={7} className="empty-state">Tidak ada data pengiriman.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
