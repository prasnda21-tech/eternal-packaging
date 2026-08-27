import { Truck } from "lucide-react";
import { formatDate } from "../lib/constants";
import { entriesForLine } from "../lib/workflowEntries";
import { loadingStatusText, shipmentLinesForOrder } from "../lib/shipmentLines";
import { MetricCell, MetricRow, QtyOpsHead, statusClassFor } from "./qtyUi";

function fmt(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function hasLegacyLoading(order) {
  const lines = order?.loading_info?.lines || [];
  const events = order?.loading_info?.events || [];
  const lc = order?.loading_checklist || {};
  const loaded = lines.some((l) => Number(l.loaded_qty || l.loaded || 0) > 0);
  return loaded || events.length > 0 || lc.items_loaded || lc.vehicle_plate || lc.driver_name || lc.seal_ok;
}

export default function LoadingPanel({ order }) {
  if (!hasLegacyLoading(order)) return null;

  const lc = order?.loading_checklist || {};
  const rows = shipmentLinesForOrder(order);
  const loadingEvents = order?.loading_info?.events || [];
  const packedTotal = rows.reduce((s, r) => s + Number(r.packed || 0), 0);
  const loadedTotal = rows.reduce((s, r) => s + Number(r.loaded || 0), 0);

  return (
    <section className="panel packing-panel" data-testid="loading-panel">
      <QtyOpsHead icon={Truck} title="Riwayat Loading">
        <span className={`qty-status ${statusClassFor("DONE")}`} data-testid="loading-status-badge">
          Legacy
        </span>
      </QtyOpsHead>
      <p className="muted small" data-testid="loading-legacy-note" style={{ padding: "8px 16px 0" }}>
        Loading bukan lagi workflow aktif. Data berikut hanya riwayat.
      </p>
      <MetricRow testid="loading-summary">
        <MetricCell label="Packed" value={packedTotal} testid="loading-packed-total" />
        <MetricCell label="Sudah Loading" value={loadedTotal} testid="loading-loaded-total" />
      </MetricRow>
      {rows.length > 0 && (
        <div className="packing-lines-wrap">
          <table className="packing-lines-table" data-testid="loading-lines-table">
            <thead>
              <tr>
                <th>Barang / Item</th>
                <th className="num">Qty Packing</th>
                <th className="num">Qty SJ</th>
                <th className="num">Sudah Loading</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => {
                const loaded = Number(row.loaded || 0);
                const hist = entriesForLine(loadingEvents, row.line_idx);
                return (
                  <tr key={row.line_idx ?? idx} data-testid={`loading-line-row-${idx}`}>
                    <td>
                      <div><strong>{row.name}</strong></div>
                      {row.color && <div className="muted small">{row.color}</div>}
                    </td>
                    <td className="num mono" data-testid={`loading-line-packed-${idx}`}>{fmt(row.packed)}</td>
                    <td className="num mono" data-testid={`loading-line-shipped-${idx}`}>{fmt(row.shipped)}</td>
                    <td className="num mono pack-cell-done" data-testid={`loading-line-loaded-${idx}`}>
                      <span data-testid={`loading-line-status-${idx}`}>{loadingStatusText(loaded)}</span>
                      {hist.length > 0 && (
                        <div className="muted small">{hist.length} entri riwayat</div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {(lc.vehicle_plate || lc.driver_name || lc.notes) && (
        <div className="prep-grid packing-form" data-testid="loading-form">
          {lc.vehicle_plate && <p className="muted small">Plat kendaraan: {lc.vehicle_plate}</p>}
          {lc.driver_name && <p className="muted small">Nama pengantar: {lc.driver_name}</p>}
          {lc.notes && <p className="muted small">Catatan: {lc.notes}</p>}
        </div>
      )}
      {lc.updated_at && (
        <p className="muted small" data-testid="loading-updated">
          Update terakhir: {lc.updated_by_name || lc.updated_by} · {formatDate(lc.updated_at)}
        </p>
      )}
    </section>
  );
}
