import { useState } from "react";
import { Printer, X } from "lucide-react";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";

export function getRouteButtonClass(currentProcess, buttonType) {
  const p = (currentProcess || "").toLowerCase();
  return p === buttonType ? "primary sm" : "ghost sm";
}

export function shouldDispatchRouteUpdate(currentProcess, targetValue) {
  const current = (currentProcess || "").toLowerCase();
  return current !== targetValue;
}

/** Compact Cetak / Tidak Cetak controls for one line_idx (shared by table + panel). */
export function LineRouteControls({ productionProcess, idx, canEdit, busy, onSelect }) {
  return (
    <div className="li-route-controls" data-testid={`line-route-controls-${idx}`}>
      <button
        type="button"
        className={getRouteButtonClass(productionProcess, "print")}
        onClick={() => onSelect(idx, "print")}
        disabled={!canEdit || busy}
        data-testid={`route-print-${idx}`}
      >
        Cetak
      </button>
      <button
        type="button"
        className={getRouteButtonClass(productionProcess, "non_print")}
        onClick={() => onSelect(idx, "non_print")}
        disabled={!canEdit || busy}
        data-testid={`route-nonprint-${idx}`}
      >
        Tidak Cetak
      </button>
    </div>
  );
}

/**
 * Connected per-line route editor for the dense Line Item table.
 * Same API + no-op rules as ProductionRoutePanel — presentation location only.
 */
export function LineRouteEditor({ order, lineIdx, canEdit, onUpdated }) {
  const li = (order?.line_items || [])[lineIdx];
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (!li) return null;

  const handleSelectRoute = async (idx, targetValue) => {
    if (!shouldDispatchRouteUpdate(li.production_process, targetValue)) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.setLineItemRoute(order.id, [{ line_idx: idx, production_process: targetValue }]);
      onUpdated?.(r.order);
    } catch (e) {
      setErr(formatApiError(e, "Gagal mengubah routing"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="li-route-editor">
      <LineRouteControls
        productionProcess={li.production_process}
        idx={lineIdx}
        canEdit={canEdit}
        busy={busy}
        onSelect={handleSelectRoute}
      />
      {err ? (
        <span className="li-route-err" data-testid={`route-error-${lineIdx}`}>
          <X size={11} /> {err}
        </span>
      ) : null}
    </div>
  );
}

/**
 * ProductionRoutePanel — Batch 3 · classification per line item Cetak/Non-Cetak.
 * Legacy line tanpa `production_process` → dianggap "print" default (backward
 * compatible). TIDAK mengubah quantity/name/color/Accurate data.
 *
 * `compact`: summary strip only — route buttons live in the Line Item table.
 */
export default function ProductionRoutePanel({ order, canEdit, onUpdated, compact = false }) {
  const lis = order?.line_items || [];
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // Draft di-derive dari server tiap render — set tunggal per klik.

  const setRoute = async (idx, value) => {
    setBusy(true); setErr("");
    try {
      const r = await api.setLineItemRoute(order.id, [{ line_idx: idx, production_process: value }]);
      onUpdated?.(r.order);
    } catch (e) {
      setErr(formatApiError(e, "Gagal mengubah routing"));
    } finally {
      setBusy(false);
    }
  };

  const handleSelectRoute = (idx, targetValue) => {
    const current = lis[idx]?.production_process;
    if (!shouldDispatchRouteUpdate(current, targetValue)) {
      return; // Safe no-op: already active, no clear/unset, no redundant API call
    }
    setRoute(idx, targetValue);
  };

  if (compact) {
    return (
      <section className="panel production-route-panel is-compact" data-testid="production-route-panel">
        <header className="panel-head">
          <h3><Printer size={14}/> Proses Produksi Per Line Item</h3>
          <span
            className="pill sm"
            data-testid="requires-printing-badge"
            style={{
              background: order.requires_printing ? "#e6ecf3" : "#efe6d3",
              color: order.requires_printing ? "#3a4b60" : "#7a6f52",
            }}
          >
            {order.requires_printing ? "Perlu Cetak" : "Semua Non-Cetak"}
          </span>
        </header>
        <p className="muted small production-route-compact-hint" data-testid="production-route-compact-hint">
          Atur <b>Cetak / Tidak Cetak</b> per baris di tabel Line Item.
          Line Cetak wajib Upload Sampel · Approval Sampel · Qty Cetak; Non-Cetak dapat lewati tahap tersebut.
        </p>
        {err && <p style={{ color: "#8f1f1f", marginTop: 4 }} data-testid="route-error"><X size={12}/> {err}</p>}
        {!canEdit && <p className="muted small" style={{ marginTop: 4 }}>Butuh permission <code>production.route.edit</code>.</p>}
      </section>
    );
  }

  return (
    <section className="panel" data-testid="production-route-panel">
      <header className="panel-head">
        <h3><Printer size={14}/> Proses Produksi Per Line Item</h3>
        <span
          className="pill sm"
          data-testid="requires-printing-badge"
          style={{
            background: order.requires_printing ? "#e6ecf3" : "#efe6d3",
            color: order.requires_printing ? "#3a4b60" : "#7a6f52",
          }}
        >
          {order.requires_printing ? "Perlu Cetak" : "Semua Non-Cetak"}
        </span>
      </header>
      <p className="muted small" style={{ marginTop: 0 }}>
        Line dengan proses <b>Cetak</b> wajib melalui Upload Sampel · Approval Sampel · Qty Cetak.
        Non-Cetak dapat lewati tahap tersebut. Legacy line tanpa klasifikasi diperlakukan sebagai Cetak.
      </p>
      <table className="prep-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }} data-testid="production-route-table">
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
            <th style={{ padding: "6px 8px" }}>Barang</th>
            <th className="num" style={{ padding: "6px 8px", textAlign: "right" }}>Qty</th>
            <th style={{ padding: "6px 8px" }}>Proses</th>
          </tr>
        </thead>
        <tbody>
          {lis.map((li, idx) => {
            return (
              <tr key={idx} data-testid={`route-row-${idx}`} style={{ borderBottom: "1px solid var(--line)" }}>
                <td style={{ padding: "8px" }}>
                  <div><strong>{li.name}</strong></div>
                  {li.color && <div className="muted small">{li.color}</div>}
                </td>
                <td className="num mono" style={{ padding: "8px", textAlign: "right" }}>{Number(li.quantity || 0).toLocaleString("id-ID")}</td>
                <td style={{ padding: "8px" }}>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button
                      type="button"
                      className={getRouteButtonClass(li.production_process, "print")}
                      onClick={() => handleSelectRoute(idx, "print")}
                      disabled={!canEdit || busy}
                      data-testid={`route-print-${idx}`}
                    >
                      Cetak
                    </button>
                    <button
                      type="button"
                      className={getRouteButtonClass(li.production_process, "non_print")}
                      onClick={() => handleSelectRoute(idx, "non_print")}
                      disabled={!canEdit || busy}
                      data-testid={`route-nonprint-${idx}`}
                    >
                      Tidak Cetak
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {err && <p style={{ color: "#8f1f1f", marginTop: 8 }} data-testid="route-error"><X size={12}/> {err}</p>}
      {!canEdit && <p className="muted small" style={{ marginTop: 8 }}>Butuh permission <code>production.route.edit</code>.</p>}
    </section>
  );
}
