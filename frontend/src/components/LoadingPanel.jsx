import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2, Truck } from "lucide-react";
import { formatDate, formatDisplayDateTime } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { entriesForLine, newActionId } from "../lib/workflowEntries";
import { loadingStatusText, shipmentLinesForOrder } from "../lib/shipmentLines";
import { MetricCell, MetricRow, QtyOpsHead, statusClassFor } from "./qtyUi";
import ConfirmActionModal from "./ConfirmActionModal";
import SuratJalanViewButton from "./SuratJalanViewButton";
import api from "../lib/api";
import {
  emptyLoadingLine,
  loadingConfirmations,
  loadingLineLabel,
  loadingPoLines,
  sjFilesForLoading,
  validateLoadingDraftLines,
  canDeleteLoadingConfirmation,
} from "../lib/loadingConfirm";

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

export default function LoadingPanel({ order, canManage = false, onUpdated }) {
  const { user, hasPerm } = useAuth();
  const files = sjFilesForLoading(order);
  const [sjId, setSjId] = useState(files[0]?.id || "");
  const [rows, setRows] = useState([emptyLoadingLine()]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const fileIdsKey = files.map((f) => f.id).join("|");
  useEffect(() => {
    if (!files.some((f) => f.id === sjId)) {
      setSjId(files[0]?.id || "");
    }
  }, [fileIdsKey, sjId, files]);

  const activeSj = files.find((f) => f.id === sjId) || files[0] || null;
  const poLines = useMemo(() => loadingPoLines(order, activeSj?.id), [order, activeSj?.id]);
  const confirmations = loadingConfirmations(order);
  const canDelete = hasPerm("loading.delete");

  const hasSj = files.length > 0;
  const totalSjQty = poLines.reduce((s, r) => s + Number(r.qty_sj || 0), 0);
  const totalLoaded = poLines.reduce((s, r) => s + Number(r.loaded || 0), 0);
  const remaining = poLines.reduce((s, r) => s + Number(r.available || 0), 0);
  const physicalReady = remaining > 0;
  const waitingPhysical = hasSj && !physicalReady && confirmations.length === 0 && !hasLegacyLoading(order);

  const setRow = (i, patch) => {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };
  const addRow = () => setRows((prev) => [...prev, emptyLoadingLine()]);
  const removeRow = (i) => setRows((prev) => (prev.length <= 1 ? [emptyLoadingLine()] : prev.filter((_, idx) => idx !== i)));

  const openConfirm = () => {
    if (!canManage || busy) return;
    if (!physicalReady) {
      const totalPhysicalAvailable = poLines.reduce((s, r) => s + Number(r.physical_available || 0), 0);
      if (totalPhysicalAvailable <= 0) {
        setErr("Loading tidak dapat dilakukan karena belum ada qty Packing.");
      } else {
        setErr("Qty Loading belum bisa diisi. Seluruh kuantitas Surat Jalan untuk item ini sudah selesai di-loading.");
      }
      return;
    }
    if (!activeSj?.id) {
      setErr("Pilih Surat Jalan yang dikonfirmasi.");
      return;
    }
    const check = validateLoadingDraftLines(rows, poLines);
    if (!check.ok) {
      setErr(check.error);
      return;
    }
    setErr("");
    setConfirm({
      sj: activeSj,
      lines: check.lines,
      time: formatDisplayDateTime(new Date().toISOString()),
      actionId: newActionId(),
    });
  };

  const execute = async () => {
    if (!confirm || busy) return;
    setBusy(true);
    setErr("");
    try {
      const result = await api.confirmLoading(order.id, {
        surat_jalan_id: confirm.sj.id,
        lines: confirm.lines,
        action_id: confirm.actionId,
      });
      onUpdated?.(result?.order || result);
      setConfirm(null);
      setRows([emptyLoadingLine()]);
    } catch (e) {
      const d = e?.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Konfirmasi Loading gagal."));
    } finally {
      setBusy(false);
    }
  };

  const executeDelete = async () => {
    if (!deleteTarget || busy) return;
    setBusy(true);
    setErr("");
    try {
      const result = await api.deleteLoadingConfirm(order.id, deleteTarget.id);
      onUpdated?.(result?.order || result);
      setDeleteTarget(null);
    } catch (e) {
      const d = e?.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Hapus Loading gagal."));
      setDeleteTarget(null);
    } finally {
      setBusy(false);
    }
  };

  const summaryLines = useMemo(() => {
    if (!confirm) return [];
    return confirm.lines.map((ln) => ({
      ...ln,
      label: loadingLineLabel(ln, order),
    }));
  }, [confirm, order]);

  const showActive = hasSj || canManage;
  if (!showActive && !hasLegacyLoading(order) && confirmations.length === 0) return null;

  const rowsSnap = shipmentLinesForOrder(order);
  const loadingEvents = order?.loading_info?.events || [];
  const lc = order?.loading_checklist || {};

  return (
    <section className="panel packing-panel" data-testid="loading-panel">
      <QtyOpsHead icon={Truck} title="LOADING">
        <span className={`qty-status ${statusClassFor(remaining <= 0 && (confirmations.length || hasLegacyLoading(order)) ? "DONE" : "READY")}`} data-testid="loading-status-badge">
          {confirmations.length ? "Konfirmasi SJ" : (hasSj ? "Menunggu konfirmasi" : "Legacy")}
        </span>
      </QtyOpsHead>

      <div className="qty-ops-body" style={{ display: "grid", gap: 10, padding: "8px 16px 16px" }}>
        <p className="muted small" data-testid="loading-purpose-note">
          Konfirmasi Item + Qty manual berdasarkan file Surat Jalan.
        </p>
        <div><strong>No PO</strong> {order?.po_number || "—"}</div>
        <div><strong>Customer</strong> {order?.customer_name || order?.customer || "—"}</div>

        {hasSj ? (
          <label data-testid="loading-sj-select-wrap">
            <span>Surat Jalan</span>
            <select
              value={activeSj?.id || ""}
              onChange={(e) => setSjId(e.target.value)}
              data-testid="loading-sj-select"
            >
              {files.map((f) => (
                <option key={f.id} value={f.id}>{f.sj_number || f.name}</option>
              ))}
            </select>
          </label>
        ) : (
          <p className="muted small" data-testid="loading-no-sj">Belum ada file Surat Jalan.</p>
        )}

        {hasSj && activeSj && (
          <div
            data-testid="loading-sj-file-row"
            style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}
          >
            <span data-testid="loading-sj-number">
              <strong>Surat Jalan:</strong> {activeSj.sj_number || activeSj.name}
            </span>
            {activeSj.has_file !== false && (activeSj.file_id || activeSj.id) ? (
              <SuratJalanViewButton
                order={order}
                entries={[{
                  id: activeSj.file_id || activeSj.id,
                  file_id: activeSj.file_id || activeSj.id,
                  name: activeSj.name,
                  source: activeSj.source,
                }]}
                label="Lihat Surat Jalan"
                testid="loading-sj-lihat"
              />
            ) : null}
          </div>
        )}

        {hasSj && activeSj && totalSjQty > 0 && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
              gap: 8,
              background: "#f8fafc",
              padding: "10px 12px",
              borderRadius: 6,
              fontSize: "0.85rem",
              border: "1px solid #e2e8f0",
            }}
            data-testid="loading-sj-progress-summary"
          >
            <div>
              <span style={{ color: "#64748b", fontSize: "0.75rem", display: "block" }}>TOTAL QTY SJ</span>
              <strong data-testid="loading-total-sj-qty">{fmt(totalSjQty)} pcs</strong>
            </div>
            <div>
              <span style={{ color: "#64748b", fontSize: "0.75rem", display: "block" }}>SUDAH LOADING</span>
              <strong style={{ color: "#0284c7" }} data-testid="loading-cumulative-loaded-qty">{fmt(totalLoaded)} pcs</strong>
            </div>
            <div>
              <span style={{ color: "#64748b", fontSize: "0.75rem", display: "block" }}>AVAILABLE / SISA</span>
              <strong style={{ color: remaining === 0 ? "#16a34a" : "#ea580c" }} data-testid="loading-available-qty">
                {fmt(remaining)} pcs
              </strong>
            </div>
          </div>
        )}

        {canManage && hasSj && (
          <div className="sj-item-block" data-testid="loading-item-block">
            <h4 className="sj-item-heading">KONFIRMASI ITEM & QTY</h4>
            {waitingPhysical && (
              <p className="muted small" data-testid="loading-physical-gate">
                Qty Loading dapat diisi setelah qty fisik (packing) siap.
              </p>
            )}
            {rows.map((row, i) => (
              <div key={i} className="sj-item-row" data-testid={`loading-item-row-${i}`}>
                <label>
                  <span>Item</span>
                  <select
                    value={row.line_idx}
                    onChange={(e) => setRow(i, { line_idx: e.target.value })}
                    disabled={!physicalReady}
                    data-testid={`loading-item-select-${i}`}
                  >
                    <option value="">Pilih item</option>
                    {poLines.map((ln) => (
                      <option key={ln.line_idx} value={ln.line_idx}>
                        {ln.label} (sisa {fmt(ln.available)})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Qty</span>
                  <input
                    type="number"
                    min="1"
                    inputMode="numeric"
                    className={`manual-qty-input is-manual${!physicalReady ? " is-disabled" : ""}`}
                    value={row.qty}
                    onChange={(e) => setRow(i, { qty: e.target.value })}
                    disabled={!physicalReady}
                    data-testid={`loading-qty-input-${i}`}
                  />
                </label>
                <button
                  type="button"
                  className="ghost sm"
                  onClick={() => removeRow(i)}
                  disabled={rows.length <= 1}
                  data-testid={`loading-item-remove-${i}`}
                  aria-label="Hapus item"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
            <button type="button" className="ghost sm" onClick={addRow} data-testid="loading-item-add">
              <Plus size={14} /> Tambah Item
            </button>
            <button
              type="button"
              className="primary"
              onClick={openConfirm}
              disabled={busy || !physicalReady}
              data-testid="loading-confirm-button"
            >
              KONFIRMASI LOADING
            </button>
          </div>
        )}

        {err && <div className="error" data-testid="loading-error">{err}</div>}

        {confirmations.length > 0 && (
          <ul className="sj-file-list" data-testid="loading-confirm-history" style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
            {confirmations.map((c) => {
              const deletable = canDelete && canDeleteLoadingConfirmation(order, c);
              return (
                <li key={c.id} data-testid={`loading-confirm-${c.id}`}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start" }}>
                    <div>
                      <strong>{c.surat_jalan_name || "Surat Jalan"}</strong>
                      {(c.lines || []).map((ln, i) => (
                        <div key={`${c.id}-${i}`} className="muted small">
                          {loadingLineLabel(ln, order)} — Qty {fmt(ln.qty)}
                        </div>
                      ))}
                      <span className="muted small">
                        {c.confirmed_by_name || c.confirmed_by}
                        {c.confirmed_at ? ` · ${formatDate(c.confirmed_at)}` : ""}
                      </span>
                    </div>
                    {deletable && (
                      <button
                        type="button"
                        className="ghost sm"
                        disabled={busy}
                        onClick={() => setDeleteTarget(c)}
                        data-testid={`loading-delete-${c.id}`}
                      >
                        Hapus
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {hasLegacyLoading(order) && (
          <>
            <p className="muted small" data-testid="loading-legacy-note">Riwayat Loading lama (tanpa referensi Surat Jalan).</p>
            <MetricRow testid="loading-summary">
              <MetricCell label="Sudah Loading" value={rowsSnap.reduce((s, r) => s + Number(r.loaded || 0), 0)} testid="loading-loaded-total" />
            </MetricRow>
            {rowsSnap.length > 0 && (
              <div className="packing-lines-wrap">
                <table className="packing-lines-table" data-testid="loading-lines-table">
                  <thead>
                    <tr>
                      <th>Barang / Item</th>
                      <th className="num">Sudah Loading</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rowsSnap.map((row, idx) => {
                      const loaded = Number(row.loaded || 0);
                      const hist = entriesForLine(loadingEvents, row.line_idx);
                      return (
                        <tr key={row.line_idx ?? idx} data-testid={`loading-line-row-${idx}`}>
                          <td><strong>{row.name}</strong></td>
                          <td className="num mono pack-cell-done" data-testid={`loading-line-loaded-${idx}`}>
                            <span data-testid={`loading-line-status-${idx}`}>{loadingStatusText(loaded)}</span>
                            {hist.length > 0 && <div className="muted small">{hist.length} entri riwayat</div>}
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
          </>
        )}
      </div>

      <ConfirmActionModal
        open={!!confirm}
        title="KONFIRMASI LOADING"
        stage="Loading"
        poNumber={order.po_number}
        customer={order.customer_name || order.customer}
        action={confirm ? `Surat Jalan: ${confirm.sj.name}` : "Konfirmasi Loading"}
        name={user?.name || user?.email}
        time={confirm?.time}
        busy={busy}
        error={err}
        confirmLabel="Konfirmasi"
        extra={summaryLines.length ? (
          <ul data-testid="loading-confirm-items">
            {summaryLines.map((ln) => (
              <li key={`${ln.line_idx}-${ln.qty}`}>{ln.label} — Qty {fmt(ln.qty)}</li>
            ))}
          </ul>
        ) : null}
        onCancel={() => { if (!busy) { setConfirm(null); setErr(""); } }}
        onConfirm={execute}
      />

      <ConfirmActionModal
        open={!!deleteTarget}
        title="HAPUS LOADING"
        stage="Loading"
        poNumber={order.po_number}
        customer={order.customer_name || order.customer}
        action={deleteTarget ? `Hapus konfirmasi ${deleteTarget.id}` : "Hapus data Loading"}
        name={user?.name || user?.email}
        time={formatDisplayDateTime(new Date().toISOString())}
        busy={busy}
        error={err}
        confirmLabel="Hapus"
        extra={<p data-testid="loading-delete-confirm-text">Hapus data Loading ini?</p>}
        onCancel={() => { if (!busy) { setDeleteTarget(null); setErr(""); } }}
        onConfirm={executeDelete}
      />
    </section>
  );
}
