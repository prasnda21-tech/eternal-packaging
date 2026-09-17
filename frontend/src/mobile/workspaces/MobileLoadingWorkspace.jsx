import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { newActionId } from "../../lib/workflowEntries";
import {
  loadingPoLines,
  sjFilesForLoading,
  nextActionableLoadingSjId,
  loadingVerificationStatus,
  sjLinesForDiscrepancy,
  selectedSjDisplayLines,
  totalSelectedSjQty,
  sharedSjDisplayUnit,
  discrepancyDiff,
  validateDiscrepancySelection,
  loadingDiscrepancies,
  LOADING_CONFIRM_MATCH_PERM,
  LOADING_REPORT_DISCREPANCY_PERM,
} from "../../lib/loadingConfirm";
import { SJ_VIEW_FILE_PERM, mobileSuratJalanViewerHref } from "../../lib/suratJalan";
import { loadingAvailability, loadingSkipsPacking, matchLoadingSjFile } from "../../lib/mobileWorkspaces";
import { useAuth } from "../../lib/auth";

function fmt(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function fmtQtyUnit(qty, unit) {
  return `${fmt(qty)} ${unit || "pcs"}`;
}

export default function MobileLoadingWorkspace({ order, canEdit, onUpdated, shipmentId }) {
  const { hasPerm } = useAuth();
  const [params, setParams] = useSearchParams();
  const sjFiles = sjFilesForLoading(order);
  const hinted = String(shipmentId || params.get("shipment") || "").trim();
  const resolvedId = hinted || nextActionableLoadingSjId(order, "");
  const activeSj = matchLoadingSjFile(sjFiles, resolvedId);
  const selectedSjKey = String(activeSj?.shipment_id || activeSj?.id || "").trim();
  const poLines = useMemo(() => loadingPoLines(order, activeSj?.id), [order, activeSj?.id]);
  const sjLines = useMemo(() => sjLinesForDiscrepancy(order, activeSj?.id), [order, activeSj?.id]);
  const displayLines = useMemo(
    () => selectedSjDisplayLines(order, selectedSjKey || activeSj?.id),
    [order, selectedSjKey, activeSj?.id],
  );
  const totalQtySj = totalSelectedSjQty(displayLines);
  const avail = loadingAvailability(order, poLines, sjFiles);
  const skipPacking = loadingSkipsPacking(poLines);
  const verification = loadingVerificationStatus(order, activeSj?.id);
  const openDisc = loadingDiscrepancies(order);

  const canConfirmMatch = canEdit && hasPerm(LOADING_CONFIRM_MATCH_PERM);
  const canReportDisc = canEdit && hasPerm(LOADING_REPORT_DISCREPANCY_PERM);
  const canViewSj = hasPerm(SJ_VIEW_FILE_PERM);

  const [mode, setMode] = useState("actions");
  const [selected, setSelected] = useState({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const isComplete = String(verification?.code || "").includes("complete");
  const verifyLabel = isComplete ? "Selesai" : "Belum";
  const loadingLabel = isComplete ? "Loading Selesai" : "Belum";

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

  const confirmMatch = async () => {
    if (!canConfirmMatch || busy || !activeSj?.id) return;
    setBusy(true);
    setErr("");
    try {
      const res = await api.confirmLoadingMatch(order.id, {
        surat_jalan_id: activeSj.id,
        action_id: newActionId(),
      });
      onUpdated?.(res.order || res);
      setMode("actions");
    } catch (e) {
      setErr(formatApiError(e, "Konfirmasi Loading gagal."));
    } finally {
      setBusy(false);
    }
  };

  const submitDiscrepancy = async () => {
    if (!canReportDisc || busy || !activeSj?.id) return;
    const check = validateDiscrepancySelection(selected, sjLines);
    if (!check.ok) {
      setErr(check.error);
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await api.reportLoadingDiscrepancy(order.id, {
        surat_jalan_id: activeSj.id,
        mismatches: check.mismatches,
        note: note.trim() || null,
        action_id: newActionId(),
      });
      onUpdated?.(res.order || res);
      setSelected({});
      setNote("");
      setMode("actions");
    } catch (e) {
      setErr(formatApiError(e, "Laporan selisih gagal."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="mobile-ws-loading" className="mop-ws-role mop-ws-role--loading">
      <div className="mop-ws-header"><h2>LOADING</h2></div>
      {sjFiles.length > 1 && !activeSj && (
        <label data-testid="mobile-loading-sj-picker" className="mop-card-meta" style={{ display: "grid", gap: 6, marginBottom: 10 }}>
          <span>Pilih Surat Jalan</span>
          <select
            defaultValue=""
            onChange={(e) => {
              const next = new URLSearchParams(params);
              const sid = String(e.target.value || "").trim();
              if (sid) next.set("shipment", sid);
              else next.delete("shipment");
              setParams(next);
            }}
          >
            <option value="">Pilih Surat Jalan</option>
            {sjFiles.map((f) => (
              <option key={f.id} value={f.shipment_id || f.id}>{f.sj_number || f.name}</option>
            ))}
          </select>
        </label>
      )}
      <div className="mop-loading-status" data-testid="mobile-loading-status-panel">
        <div data-testid="mobile-loading-verification">
          <span>Verifikasi Kesesuaian SJ</span>
          <strong data-testid="mobile-loading-verify-value">{verifyLabel}</strong>
        </div>
        <div data-testid="mobile-loading-status">
          <span>Status Loading</span>
          <strong data-testid="mobile-loading-status-value">{loadingLabel}</strong>
        </div>
      </div>
      {activeSj ? (
        <div className="mop-sj-lines" data-testid="mobile-loading-sj-lines">
          <h3>Barang Sesuai Surat Jalan</h3>
          <table className="mop-sj-lines-table">
            <thead>
              <tr>
                <th>Item Line</th>
                <th>Qty SJ</th>
              </tr>
            </thead>
            <tbody>
              {displayLines.map((ln, i) => (
                <tr key={ln.key} data-testid={`mobile-loading-sj-line-${i}`}>
                  <td data-testid={`mobile-loading-sj-line-name-${i}`}>{ln.name}</td>
                  <td data-testid={`mobile-loading-sj-line-qty-${i}`}>{fmtQtyUnit(ln.qty_sj, ln.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mop-sj-lines-total" data-testid="mobile-loading-sj-total">
            <span>Total Qty SJ</span>
            <strong>{fmtQtyUnit(totalQtySj, sharedSjDisplayUnit(displayLines))}</strong>
          </div>
        </div>
      ) : null}
      {avail.blocked && <div className="mop-error">{avail.reason}</div>}
      {!avail.blocked && skipPacking && Number(avail.packed || 0) === 0 && (
        <div className="mop-card-meta" data-testid="mobile-loading-nonprint-note">
          Item non-cetak — Packing dilewati
        </div>
      )}
      {verification?.reason && <div className="mop-error" data-testid="mobile-loading-reason">{verification.reason}</div>}
      {err && <div className="mop-error">{err}</div>}

      {activeSj && canViewSj && (
        <a
          className="mop-btn-ghost"
          style={{ display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 10 }}
          href={mobileSuratJalanViewerHref(order.id, activeSj.id, "loading")}
        >
          Buka Surat Jalan
        </a>
      )}

      {openDisc.length > 0 && (
        <div className="mop-error" data-testid="mobile-loading-open-disc" style={{ marginBottom: 10 }}>
          Tertahan — Qty Tidak Sesuai SJ
          {openDisc.map((d) => (
            <div key={d.id} style={{ marginTop: 6 }}>
              {(d.lines || []).map((ln, i) => (
                <div key={`${d.id}-${i}`}>
                  {ln.name}: SJ {fmt(ln.qty_sj)}, fisik {fmt(ln.qty_actual)}, {ln.classification}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {mode === "actions" && !isComplete && (canConfirmMatch || canReportDisc) && (
        <div data-testid="mobile-loading-actions" style={{ display: "grid", gap: 10, marginBottom: 12 }}>
          {canConfirmMatch && (
            <button
              type="button"
              className="mop-btn-primary"
              disabled={!canEdit || busy || !activeSj?.id || avail.blocked || String(verification?.code || "").includes("packing")}
              onClick={confirmMatch}
              data-testid="mobile-loading-confirm-match"
              style={{ minHeight: 48 }}
            >
              {busy ? "Memproses…" : "Semua Barang Sesuai SJ"}
            </button>
          )}
          {canReportDisc && (
            <button
              type="button"
              className="mop-btn-ghost"
              disabled={!canEdit || busy || !activeSj?.id}
              onClick={() => { setMode("discrepancy"); setErr(""); }}
              data-testid="mobile-loading-open-discrepancy"
              style={{ minHeight: 48 }}
            >
              Ada Selisih Qty
            </button>
          )}
        </div>
      )}

      {mode === "discrepancy" && canReportDisc && (
        <div data-testid="mobile-loading-discrepancy" style={{ display: "grid", gap: 12 }}>
          {sjLines.map((ln) => {
            const row = selected[ln.line_idx];
            const marked = !!row?.marked;
            const diff = marked ? discrepancyDiff(ln.qty_sj, row?.actual_qty) : null;
            return (
              <div
                className="mop-line-block"
                key={ln.line_idx}
                data-testid={`mobile-load-line-${ln.line_idx}`}
                style={{ padding: 12 }}
              >
                <strong>{ln.name}</strong>
                <p className="mop-card-meta">
                  <span>Qty SJ {fmt(ln.qty_sj)} {ln.unit || "pcs"}</span>
                </p>
                {!marked ? (
                  <button
                    type="button"
                    className="mop-btn-ghost"
                    style={{ minHeight: 44, width: "100%" }}
                    onClick={() => toggleMark(ln.line_idx)}
                    disabled={busy}
                    data-testid={`mobile-load-mark-${ln.line_idx}`}
                  >
                    Tandai Tidak Sesuai
                  </button>
                ) : (
                  <div style={{ display: "grid", gap: 8 }}>
                    <label style={{ display: "grid", gap: 4 }}>
                      <span>Qty Fisik Aktual ({ln.unit || "pcs"})</span>
                      <input
                        type="number"
                        inputMode="numeric"
                        pattern="[0-9]*"
                        min="0"
                        value={row?.actual_qty ?? ""}
                        onChange={(e) => setActual(ln.line_idx, e.target.value)}
                        disabled={busy}
                        data-testid={`mobile-load-actual-${ln.line_idx}`}
                        style={{ fontSize: 18, minHeight: 48, width: "100%", boxSizing: "border-box" }}
                      />
                    </label>
                    <div data-testid={`mobile-load-diff-${ln.line_idx}`}>
                      {diff?.label || ""}
                    </div>
                    <button
                      type="button"
                      className="mop-btn-ghost"
                      style={{ minHeight: 44 }}
                      onClick={() => toggleMark(ln.line_idx)}
                      disabled={busy}
                      data-testid={`mobile-load-unmark-${ln.line_idx}`}
                    >
                      Batal Tandai
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          <label style={{ display: "grid", gap: 4 }}>
            <span>Catatan (opsional)</span>
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={busy}
              data-testid="mobile-loading-disc-note"
              style={{ minHeight: 44 }}
            />
          </label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <button
              type="button"
              className="mop-btn-ghost"
              style={{ minHeight: 48 }}
              disabled={busy}
              onClick={() => { setMode("actions"); setSelected({}); setNote(""); setErr(""); }}
              data-testid="mobile-loading-disc-cancel"
            >
              Batal
            </button>
            <button
              type="button"
              className="mop-btn-primary"
              style={{ minHeight: 48 }}
              disabled={busy}
              onClick={submitDiscrepancy}
              data-testid="mobile-loading-disc-submit"
            >
              {busy ? "Mengirim…" : "Laporkan Selisih Qty"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
