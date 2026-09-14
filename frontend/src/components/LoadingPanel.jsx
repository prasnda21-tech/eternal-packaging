import { useEffect, useMemo, useState } from "react";
import { Truck } from "lucide-react";
import { formatDate, formatDisplayDateTime } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { entriesForLine, newActionId } from "../lib/workflowEntries";
import { loadingStatusText, shipmentLinesForOrder } from "../lib/shipmentLines";
import { MetricCell, MetricRow, QtyOpsHead, statusClassFor } from "./qtyUi";
import ConfirmActionModal from "./ConfirmActionModal";
import SuratJalanViewButton from "./SuratJalanViewButton";
import api from "../lib/api";
import {
  loadingConfirmations,
  loadingLineLabel,
  loadingPoLines,
  sjFilesForLoading,
  canDeleteLoadingConfirmation,
  loadingVerificationStatus,
  sjLinesForDiscrepancy,
  discrepancyDiff,
  validateDiscrepancySelection,
  loadingDiscrepancies,
  loadingDiscrepancyHistory,
  LOADING_CONFIRM_MATCH_PERM,
  LOADING_REPORT_DISCREPANCY_PERM,
  LOADING_VIEW_DISCREPANCY_HISTORY_PERM,
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
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState("actions");
  const [selected, setSelected] = useState({});
  const [note, setNote] = useState("");
  const [confirmMatch, setConfirmMatch] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const canConfirmMatch = canManage && hasPerm(LOADING_CONFIRM_MATCH_PERM);
  const canReportDisc = canManage && hasPerm(LOADING_REPORT_DISCREPANCY_PERM);
  const canViewHistory = hasPerm(LOADING_VIEW_DISCREPANCY_HISTORY_PERM) || hasPerm("loading.update") || canManage;
  const canDelete = hasPerm("loading.delete");

  const fileIdsKey = files.map((f) => f.id).join("|");
  useEffect(() => {
    if (!files.some((f) => f.id === sjId)) setSjId(files[0]?.id || "");
  }, [fileIdsKey, sjId, files]);

  const activeSj = files.find((f) => f.id === sjId) || files[0] || null;
  const poLines = useMemo(() => loadingPoLines(order, activeSj?.id), [order, activeSj?.id]);
  const sjLines = useMemo(() => sjLinesForDiscrepancy(order, activeSj?.id), [order, activeSj?.id]);
  const confirmations = loadingConfirmations(order);
  const openDisc = loadingDiscrepancies(order).filter(
    (d) => !activeSj?.id || String(d.surat_jalan_id || "") === String(activeSj.id),
  );
  const discHistory = loadingDiscrepancyHistory(order);
  const verification = loadingVerificationStatus(order, activeSj?.id);

  const hasSj = files.length > 0;
  const totalSjQty = poLines.reduce((s, r) => s + Number(r.qty_sj || 0), 0);
  const totalLoaded = poLines.reduce((s, r) => s + Number(r.loaded || 0), 0);
  const remaining = poLines.reduce((s, r) => s + Number(r.available || 0), 0);
  const isComplete = String(verification?.code || "").includes("complete");

  const showActive = hasSj || canManage || canViewHistory;
  if (!showActive && !hasLegacyLoading(order) && confirmations.length === 0) return null;

  const rowsSnap = shipmentLinesForOrder(order);
  const loadingEvents = order?.loading_info?.events || [];
  const lc = order?.loading_checklist || {};

  const toggleMark = (lineIdx) => {
    setSelected((prev) => {
      const cur = prev[lineIdx];
      if (cur?.marked) {
        const next = { ...prev };
        delete next[lineIdx];
        return next;
      }
      return { ...prev, [lineIdx]: { marked: true, actual_qty: "" } };
    });
  };

  const setActual = (lineIdx, value) => {
    setSelected((prev) => ({
      ...prev,
      [lineIdx]: { ...(prev[lineIdx] || { marked: true }), marked: true, actual_qty: value },
    }));
  };

  const openMatchConfirm = () => {
    if (!canConfirmMatch || busy) return;
    if (!activeSj?.id) {
      setErr("Pilih Surat Jalan yang diverifikasi.");
      return;
    }
    if (String(verification?.code || "").includes("packing")) {
      if (!verification?.reason) {
        setErr("Packing belum mencukupi untuk konfirmasi Loading.");
      }
      return;
    }
    setErr("");
    setConfirmMatch({
      sj: activeSj,
      time: formatDisplayDateTime(new Date().toISOString()),
      actionId: newActionId(),
    });
  };

  const executeMatch = async () => {
    if (!confirmMatch || busy) return;
    setBusy(true);
    setErr("");
    try {
      const result = await api.confirmLoadingMatch(order.id, {
        surat_jalan_id: confirmMatch.sj.id,
        action_id: confirmMatch.actionId,
      });
      onUpdated?.(result?.order || result);
      setConfirmMatch(null);
      setMode("actions");
    } catch (e) {
      const d = e?.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Konfirmasi Loading gagal."));
    } finally {
      setBusy(false);
    }
  };

  const submitDiscrepancy = async () => {
    if (!canReportDisc || busy) return;
    if (!activeSj?.id) {
      setErr("Pilih Surat Jalan.");
      return;
    }
    const check = validateDiscrepancySelection(selected, sjLines);
    if (!check.ok) {
      setErr(check.error);
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const result = await api.reportLoadingDiscrepancy(order.id, {
        surat_jalan_id: activeSj.id,
        mismatches: check.mismatches,
        note: note.trim() || null,
        action_id: newActionId(),
      });
      onUpdated?.(result?.order || result);
      setSelected({});
      setNote("");
      setMode("actions");
    } catch (e) {
      const d = e?.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Laporan selisih gagal."));
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

  return (
    <section className="panel packing-panel" data-testid="loading-panel">
      <QtyOpsHead icon={Truck} title="LOADING">
        <span
          className={`qty-status ${statusClassFor(isComplete ? "DONE" : "READY")}`}
          data-testid="loading-status-badge"
        >
          {verification?.label || (hasSj ? "Menunggu Verifikasi" : "Legacy")}
        </span>
      </QtyOpsHead>

      <div className="qty-ops-body" style={{ display: "grid", gap: 10, padding: "8px 16px 16px" }}>
        <p className="muted small" data-testid="loading-purpose-note">
          Verifikasi Kesesuaian SJ — normal satu klik; input qty hanya jika ada selisih.
        </p>
        <div><strong>No PO</strong> {order?.po_number || "—"}</div>
        <div><strong>Customer</strong> {order?.customer_name || order?.customer || "—"}</div>

        {hasSj ? (
          <label data-testid="loading-sj-select-wrap">
            <span>Surat Jalan</span>
            <select value={activeSj?.id || ""} onChange={(e) => setSjId(e.target.value)} data-testid="loading-sj-select">
              {files.map((f) => (
                <option key={f.id} value={f.id}>{f.sj_number || f.name}</option>
              ))}
            </select>
          </label>
        ) : (
          <p className="muted small" data-testid="loading-no-sj">Belum ada file Surat Jalan.</p>
        )}

        {hasSj && activeSj && (
          <div data-testid="loading-sj-file-row" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
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

        {verification?.reason ? (
          <div className="error" data-testid="loading-verification-reason">{verification.reason}</div>
        ) : null}

        {openDisc.length > 0 ? (
          <div data-testid="loading-open-discrepancy" style={{ border: "1px solid #fecaca", background: "#fef2f2", borderRadius: 6, padding: 10 }}>
            <strong>Tertahan — Qty Tidak Sesuai SJ</strong>
            {openDisc.map((d) => (
              <div key={d.id} className="muted small" style={{ marginTop: 6 }}>
                {(d.lines || []).map((ln, i) => (
                  <div key={`${d.id}-${i}`}>
                    {ln.name} — SJ {fmt(ln.qty_sj)}, fisik {fmt(ln.qty_actual)}, {ln.classification} {fmt(Math.abs(ln.difference || 0))}
                  </div>
                ))}
                {d.notification_count != null ? (
                  <div data-testid="loading-crm-notified">
                    Notifikasi CRM: {d.notification_count > 0 ? "terkirim" : "tidak ada penerima"}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}

        {hasSj && mode === "actions" && (canConfirmMatch || canReportDisc) && !isComplete ? (
          <div className="sj-item-block" data-testid="loading-verify-actions" style={{ display: "grid", gap: 8 }}>
            <h4 className="sj-item-heading">VERIFIKASI KESESUAIAN SJ</h4>
            {canConfirmMatch ? (
              <button
                type="button"
                className="primary"
                onClick={openMatchConfirm}
                disabled={busy || !activeSj?.id}
                data-testid="loading-confirm-match-button"
              >
                Semua Barang Sesuai SJ
              </button>
            ) : null}
            {canReportDisc ? (
              <button
                type="button"
                className="ghost"
                onClick={() => { setMode("discrepancy"); setErr(""); }}
                disabled={busy || !activeSj?.id}
                data-testid="loading-open-discrepancy-button"
              >
                Ada Selisih Qty
              </button>
            ) : null}
          </div>
        ) : null}

        {hasSj && mode === "discrepancy" && canReportDisc ? (
          <div className="sj-item-block" data-testid="loading-discrepancy-editor" style={{ display: "grid", gap: 10 }}>
            <h4 className="sj-item-heading">LAPORKAN SELISIH QTY</h4>
            <p className="muted small">Centang hanya baris yang berbeda. Baris tidak dicentang dianggap sesuai Qty SJ.</p>
            <div style={{ overflowX: "auto" }}>
              <table className="packing-lines-table" data-testid="loading-discrepancy-table">
                <thead>
                  <tr>
                    <th>Selisih</th>
                    <th>Item</th>
                    <th className="num">Qty SJ</th>
                    <th className="num">Qty Fisik Aktual</th>
                    <th>Hasil</th>
                  </tr>
                </thead>
                <tbody>
                  {sjLines.map((ln) => {
                    const row = selected[ln.line_idx];
                    const marked = !!row?.marked;
                    const diff = marked ? discrepancyDiff(ln.qty_sj, row?.actual_qty) : null;
                    return (
                      <tr key={ln.line_idx} data-testid={`loading-disc-row-${ln.line_idx}`}>
                        <td>
                          <input
                            type="checkbox"
                            checked={marked}
                            onChange={() => toggleMark(ln.line_idx)}
                            data-testid={`loading-disc-mark-${ln.line_idx}`}
                          />
                        </td>
                        <td><strong>{ln.name}</strong></td>
                        <td className="num mono">{fmt(ln.qty_sj)}</td>
                        <td className="num">
                          {marked ? (
                            <input
                              type="number"
                              min="0"
                              inputMode="numeric"
                              className="manual-qty-input is-manual"
                              value={row?.actual_qty ?? ""}
                              onChange={(e) => setActual(ln.line_idx, e.target.value)}
                              data-testid={`loading-disc-actual-${ln.line_idx}`}
                            />
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td data-testid={`loading-disc-diff-${ln.line_idx}`}>
                          {marked && diff?.label ? diff.label : (marked ? "" : "Sesuai SJ")}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <label>
              <span>Catatan (opsional)</span>
              <input
                type="text"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                data-testid="loading-discrepancy-note"
              />
            </label>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                className="ghost"
                disabled={busy}
                onClick={() => { setMode("actions"); setSelected({}); setNote(""); setErr(""); }}
                data-testid="loading-discrepancy-cancel"
              >
                Batal
              </button>
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={submitDiscrepancy}
                data-testid="loading-discrepancy-submit"
              >
                {busy ? "Mengirim…" : "Laporkan Selisih Qty"}
              </button>
            </div>
          </div>
        ) : null}

        {err ? <div className="error" data-testid="loading-error">{err}</div> : null}

        {confirmations.length > 0 ? (
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
                        {c.source === "sj_match" ? " · Sesuai SJ" : ""}
                      </span>
                    </div>
                    {deletable ? (
                      <button
                        type="button"
                        className="ghost sm"
                        disabled={busy}
                        onClick={() => setDeleteTarget(c)}
                        data-testid={`loading-delete-${c.id}`}
                      >
                        Hapus
                      </button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}

        {canViewHistory && discHistory.length > 0 ? (
          <div data-testid="loading-discrepancy-history">
            <h4 className="sj-item-heading">RIWAYAT SELISIH LOADING</h4>
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
              {discHistory.map((d) => (
                <li key={d.id} className="muted small" data-testid={`loading-disc-hist-${d.id}`}>
                  {d.status || "open"} · {d.reported_by_name || d.reported_by}
                  {d.reported_at ? ` · ${formatDate(d.reported_at)}` : ""}
                  {(d.lines || []).map((ln, i) => (
                    <div key={`${d.id}-h-${i}`}>
                      {ln.name}: SJ {fmt(ln.qty_sj)} / fisik {fmt(ln.qty_actual)} ({ln.classification})
                    </div>
                  ))}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {hasLegacyLoading(order) ? (
          <>
            <p className="muted small" data-testid="loading-legacy-note">Riwayat Loading lama (tanpa referensi Surat Jalan).</p>
            <MetricRow testid="loading-summary">
              <MetricCell label="Sudah Loading" value={rowsSnap.reduce((s, r) => s + Number(r.loaded || 0), 0)} testid="loading-loaded-total" />
            </MetricRow>
            {rowsSnap.length > 0 ? (
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
                            {hist.length > 0 ? <div className="muted small">{hist.length} entri riwayat</div> : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : null}
            {(lc.vehicle_plate || lc.driver_name || lc.notes) ? (
              <div className="prep-grid packing-form" data-testid="loading-form">
                {lc.vehicle_plate ? <p className="muted small">Plat kendaraan: {lc.vehicle_plate}</p> : null}
                {lc.driver_name ? <p className="muted small">Nama pengantar: {lc.driver_name}</p> : null}
                {lc.notes ? <p className="muted small">Catatan: {lc.notes}</p> : null}
              </div>
            ) : null}
          </>
        ) : null}
      </div>

      <ConfirmActionModal
        open={!!confirmMatch}
        title="Semua Barang Sesuai SJ"
        stage="Loading"
        poNumber={order?.po_number}
        customer={order?.customer_name || order?.customer}
        action={confirmMatch ? `Sesuai SJ ${confirmMatch.sj.sj_number || confirmMatch.sj.name}` : "Sesuai SJ"}
        name={user?.name || user?.email}
        time={confirmMatch?.time}
        busy={busy}
        error={err}
        confirmLabel={busy ? "Memproses…" : "Konfirmasi Sesuai SJ"}
        onCancel={() => { if (!busy) setConfirmMatch(null); }}
        onConfirm={executeMatch}
        extra={(
          <>
            <p data-testid="loading-match-confirm-copy">
              Sistem membuat Loading dari qty Surat Jalan saat ini. Tidak perlu input qty manual.
            </p>
            <ul data-testid="loading-confirm-items">
              {sjLines.map((ln) => (
                <li key={ln.line_idx}>{ln.name} — Qty SJ {fmt(ln.qty_sj)}</li>
              ))}
            </ul>
          </>
        )}
      />

      <ConfirmActionModal
        open={!!deleteTarget}
        title="Hapus Konfirmasi Loading"
        stage="Loading"
        poNumber={order?.po_number}
        customer={order?.customer_name || order?.customer}
        action={deleteTarget ? `Hapus konfirmasi ${deleteTarget.id}` : "Hapus data Loading"}
        name={user?.name || user?.email}
        time={formatDisplayDateTime(new Date().toISOString())}
        busy={busy}
        error={err}
        confirmLabel="Hapus"
        onCancel={() => { if (!busy) setDeleteTarget(null); }}
        onConfirm={executeDelete}
      />
    </section>
  );
}
