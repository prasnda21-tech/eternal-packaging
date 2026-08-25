import { useEffect, useState } from "react";
import { Truck } from "lucide-react";
import api from "../lib/api";
import { formatDate, formatDisplayDateTime } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { entriesForLine, newActionId } from "../lib/workflowEntries";
import {
  canSubmitLoadingIncrement,
  loadingStatusText,
  parsePositiveQty,
  shipmentLinesForOrder,
} from "../lib/shipmentLines";
import { MetricCell, MetricRow, QtyOpsHead, statusClassFor } from "./qtyUi";
import AppendQtyEntries from "./AppendQtyEntries";
import ConfirmActionModal from "./ConfirmActionModal";

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
  const { user } = useAuth();
  const lc = order?.loading_checklist || {};
  const rows = shipmentLinesForOrder(order);
  const loadingEvents = order?.loading_info?.events || [];
  const [vehiclePlate, setVehiclePlate] = useState(lc.vehicle_plate || "");
  const [driverName, setDriverName] = useState(lc.driver_name || "");
  const [notes, setNotes] = useState(lc.notes || "");
  const [draft, setDraft] = useState(() => emptyDraft(rows));
  const [savingLine, setSavingLine] = useState(null);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(null);

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

  const openConfirm = (row) => {
    const avail = Number(row.available_loading || 0);
    const qty = parsePositiveQty(draft[row.line_idx]);
    if (!canSubmitLoadingIncrement(draft[row.line_idx], avail)) return;
    setErr("");
    setConfirm({
      row,
      qty,
      actionId: newActionId(),
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };

  const execute = async () => {
    if (!confirm || savingLine != null) return;
    setSavingLine(confirm.row.line_idx);
    setErr("");
    try {
      const r = await api.updateLoading(order.id, {
        vehicle_plate: vehiclePlate,
        driver_name: driverName,
        notes,
        lines: [{ line_idx: confirm.row.line_idx, qty: confirm.qty, action_id: confirm.actionId }],
      });
      onUpdated?.(r.order);
      setDraft((prev) => ({ ...prev, [confirm.row.line_idx]: "" }));
      setConfirm(null);
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
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, idx) => {
                    const avail = Number(row.available_loading || 0);
                    const loaded = Number(row.loaded || 0);
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
                        <td className="num packing-line-input-cell" colSpan={2}>
                          {avail <= 0 && entriesForLine(loadingEvents, row.line_idx).length === 0 && Number(loaded || 0) <= 0 ? (
                            <span className="mono muted" data-testid={`loading-qty-input-${row.line_idx}`}>{fmt(0)}</span>
                          ) : (
                            <AppendQtyEntries
                              entries={entriesForLine(loadingEvents, row.line_idx)}
                              snapshotQty={loaded}
                              snapshotAt={order?.loading_info?.updated_at}
                              snapshotName={order?.loading_info?.updated_by_name}
                              draft={draft[row.line_idx] ?? ""}
                              onDraftChange={(v) => setDraft((prev) => ({ ...prev, [row.line_idx]: v }))}
                              onConfirmClick={() => openConfirm(row)}
                              canEdit={canManage && avail > 0 && !busy && savingLine == null}
                              busy={savingLine === row.line_idx}
                              remaining={avail}
                              testid={`loading-append-${row.line_idx}`}
                              inputTestid={`loading-qty-input-${row.line_idx}`}
                              confirmTestid={`loading-save-button-${row.line_idx}`}
                            />
                          )}
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
                className="manual-input"
                value={vehiclePlate}
                onChange={(e) => setVehiclePlate(e.target.value)}
                disabled={!canManage || saving || busy}
                data-testid="loading-vehicle-input"
              />
            </label>
            <label>Nama pengantar
              <input
                type="text"
                className="manual-input"
                value={driverName}
                onChange={(e) => setDriverName(e.target.value)}
                disabled={!canManage || saving || busy}
                data-testid="loading-driver-input"
              />
            </label>
            <label className="span-2">Catatan
              <input
                type="text"
                className="manual-input"
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
      <ConfirmActionModal
        open={!!confirm}
        stage="Loading"
        qty={confirm?.qty}
        name={user?.name || user?.email}
        time={confirm?.time}
        extra={confirm?.row?.name ? <p className="muted small">{confirm.row.name}</p> : null}
        busy={savingLine != null}
        error={err}
        onCancel={() => { if (savingLine == null) setConfirm(null); }}
        onConfirm={execute}
      />
    </section>
  );
}
