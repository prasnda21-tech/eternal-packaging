import { useEffect, useState } from "react";
import { Truck, Check } from "lucide-react";
import api from "../lib/api";
import { formatDate } from "../lib/constants";
import {
  canSubmitLoadingIncrement,
  loadingStatusText,
  parsePositiveQty,
  shipmentLinesForOrder,
} from "../lib/shipmentLines";
import { MetricCell, MetricRow, QuantityInput, QtyOpsHead, statusClassFor } from "./qtyUi";

function fmt(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function emptyDraft(rows) {
  const map = {};
  for (const row of rows) map[row.line_idx] = "";
  return map;
}

function panelStatus(rows, sjReady) {
  if (!sjReady) return { kind: "IDLE", label: "Menunggu Surat Jalan" };
  const loaded = rows.reduce((s, r) => s + Number(r.loaded || 0), 0);
  const cap = rows.reduce((s, r) => s + Number(r.loading_cap || 0), 0);
  if (loaded <= 0) return { kind: "READY", label: "Belum Loading" };
  if (cap > 0 && loaded >= cap) return { kind: "DONE", label: fmt(loaded) };
  return { kind: "SAVED", label: fmt(loaded) };
}

export default function LoadingPanel({ order, canManage, busy, onUpdated, footerActions = null }) {
  const lc = order?.loading_checklist || {};
  const rows = shipmentLinesForOrder(order);
  const [vehiclePlate, setVehiclePlate] = useState(lc.vehicle_plate || "");
  const [driverName, setDriverName] = useState(lc.driver_name || "");
  const [notes, setNotes] = useState(lc.notes || "");
  const [draft, setDraft] = useState(() => emptyDraft(rows));
  const [savingLine, setSavingLine] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    const c = order?.loading_checklist || {};
    setVehiclePlate(c.vehicle_plate || "");
    setDriverName(c.driver_name || "");
    setNotes(c.notes || "");
    setDraft(emptyDraft(shipmentLinesForOrder(order)));
    setErr("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order?.id, order?.loading_info?.updated_at, order?.loading_qty_status]);

  const sjReady = !!order?.surat_jalan_created;
  const packedTotal = rows.reduce((s, r) => s + Number(r.packed || 0), 0);
  const loadedTotal = rows.reduce((s, r) => s + Number(r.loaded || 0), 0);
  const availTotal = rows.reduce((s, r) => s + Number(r.available_loading || 0), 0);
  const badge = panelStatus(rows, sjReady);
  const saving = savingLine != null;

  const saveLine = async (row) => {
    const avail = Number(row.available_loading || 0);
    const qty = parsePositiveQty(draft[row.line_idx]);
    if (!canSubmitLoadingIncrement(draft[row.line_idx], avail)) return;
    setSavingLine(row.line_idx);
    setErr("");
    try {
      const r = await api.updateLoading(order.id, {
        vehicle_plate: vehiclePlate,
        driver_name: driverName,
        notes,
        lines: [{ line_idx: row.line_idx, qty }],
      });
      onUpdated?.(r.order);
      setDraft((prev) => ({ ...prev, [row.line_idx]: "" }));
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal menyimpan qty Loading"));
    } finally {
      setSavingLine(null);
    }
  };

  return (
    <section className="panel packing-panel" data-testid="loading-panel">
      <QtyOpsHead icon={Truck} title="Loading">
        <span className={`qty-status ${statusClassFor(badge.kind)}`} data-testid="loading-status-badge">
          {badge.label}
        </span>
      </QtyOpsHead>

      {!sjReady ? (
        <p className="muted small" data-testid="loading-wait-sj-note" style={{ padding: "8px 16px 0" }}>
          Qty Loading dapat diisi setelah Surat Jalan dibuat.
        </p>
      ) : (
        <>
          <MetricRow testid="loading-summary">
            <MetricCell label="Packed" value={packedTotal} testid="loading-packed-total" />
            <MetricCell label="Sudah Loading" value={loadedTotal} testid="loading-loaded-total" />
            <MetricCell label="Tersedia" value={availTotal} testid="loading-available-total" />
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
                    <th className="num">Tersedia</th>
                    <th className="num">Input Qty Loading</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, idx) => {
                    const avail = Number(row.available_loading || 0);
                    const loaded = Number(row.loaded || 0);
                    const enabled = canManage && avail > 0 && !busy && savingLine == null;
                    const canSave = enabled && canSubmitLoadingIncrement(draft[row.line_idx], avail);
                    return (
                      <tr key={row.line_idx ?? idx} data-testid={`loading-line-row-${idx}`}>
                        <td>
                          <div><strong>{row.name}</strong></div>
                          {row.color && <div className="muted small">{row.color}</div>}
                          {row.mismatch && (
                            <div className="muted small" style={{ color: "#8f1f1f" }} data-testid={`loading-line-mismatch-${idx}`}>
                              SJ Accurate &gt; packing — loading dibatasi packing
                            </div>
                          )}
                        </td>
                        <td className="num mono" data-testid={`loading-line-packed-${idx}`}>{fmt(row.packed)}</td>
                        <td className="num mono" data-testid={`loading-line-shipped-${idx}`}>{fmt(row.shipped)}</td>
                        <td className="num mono pack-cell-done" data-testid={`loading-line-loaded-${idx}`}>
                          <span data-testid={`loading-line-status-${idx}`}>{loadingStatusText(loaded)}</span>
                        </td>
                        <td className="num mono pack-cell-ready" data-testid={`loading-line-available-${idx}`}>{fmt(avail)}</td>
                        <td className="num packing-line-input-cell">
                          {avail <= 0 ? (
                            <span className="mono muted" data-testid={`loading-qty-input-${row.line_idx}`}>{fmt(0)}</span>
                          ) : (
                            <QuantityInput
                              label=""
                              className="is-compact"
                              min="1"
                              max={avail}
                              value={draft[row.line_idx] ?? ""}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [row.line_idx]: e.target.value }))}
                              disabled={!enabled}
                              testid={`loading-qty-input-${row.line_idx}`}
                              aria-label={`Qty Loading ${row.name}`}
                            />
                          )}
                        </td>
                        <td className="packing-line-save-cell">
                          {canManage && avail > 0 ? (
                            <button
                              type="button"
                              className="primary sm"
                              onClick={() => saveLine(row)}
                              disabled={!canSave || savingLine === row.line_idx}
                              data-testid={`loading-save-button-${row.line_idx}`}
                            >
                              <Check size={13} /> Simpan
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div className="prep-grid packing-form" data-testid="loading-form">
            <label>Plat kendaraan
              <input
                type="text"
                value={vehiclePlate}
                onChange={(e) => setVehiclePlate(e.target.value)}
                disabled={!canManage || saving || busy}
                data-testid="loading-vehicle-input"
              />
            </label>
            <label>Nama pengantar
              <input
                type="text"
                value={driverName}
                onChange={(e) => setDriverName(e.target.value)}
                disabled={!canManage || saving || busy}
                data-testid="loading-driver-input"
              />
            </label>
            <label className="span-2">Catatan
              <input
                type="text"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                disabled={!canManage || saving || busy}
                data-testid="loading-notes-input"
              />
            </label>
          </div>
          {lc.updated_at && (
            <p className="muted small" data-testid="loading-updated">
              Update terakhir: {lc.updated_by_name || lc.updated_by} · {formatDate(lc.updated_at)}
            </p>
          )}
          {err && <div className="error" data-testid="loading-error">{err}</div>}
          {!canManage && (
            <p className="muted small" data-testid="loading-readonly-note">
              Butuh permission <code>loading.update</code> untuk mengisi qty Loading.
            </p>
          )}
        </>
      )}
      {footerActions}
    </section>
  );
}
