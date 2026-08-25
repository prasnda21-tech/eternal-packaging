import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import api from "../lib/api";
import { clampPercent, formatShipDate, terkirimTone } from "../lib/shipmentStatus";

function ProgressCell({ pct, tone, testid, title }) {
  const p = clampPercent(pct);
  return (
    <div className={"ship-bar ship-bar-" + tone} data-testid={testid} title={title}>
      <div className="ship-bar-fill" style={{ width: p + "%" }} />
      <span className="ship-bar-label">{p}%</span>
    </div>
  );
}

export default function StatusPengiriman() {
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

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

  return (
    <div className="ship-status-page" data-testid="shipment-status-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <h1>Status Pengiriman Barang</h1>
        </header>
        <div className="ship-status-toolbar">
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
        {err && <div className="error" data-testid="ship-error">{err}</div>}
      </div>
      <div className="ship-status-grid" data-testid="ship-table">
        <table className="ship-table">
          <thead>
            <tr>
              <th>Pelanggan</th>
              <th>No Pesanan #</th>
              <th>Tanggal</th>
              <th>Tgl Pengiriman</th>
              <th className="num">Terkirim</th>
              <th className="num">Dapat Dikirim</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} data-testid={`ship-row-${r.order_number}`}>
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
            {!rows.length && !busy && (
              <tr><td colSpan={6} className="empty-state">Tidak ada data pengiriman.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
