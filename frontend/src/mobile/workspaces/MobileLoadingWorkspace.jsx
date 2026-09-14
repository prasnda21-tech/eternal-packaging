import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { newActionId } from "../../lib/workflowEntries";
import {
  loadingPoLines,
  sjFilesForLoading,
  loadingVerificationStatus,
  sjLinesForDiscrepancy,
  discrepancyDiff,
  validateDiscrepancySelection,
  loadingDiscrepancies,
  LOADING_CONFIRM_MATCH_PERM,
  LOADING_REPORT_DISCREPANCY_PERM,
} from "../../lib/loadingConfirm";
import { SJ_VIEW_FILE_PERM, suratJalanFileUrl } from "../../lib/suratJalan";
import { loadingAvailability, loadingSkipsPacking, matchLoadingSjFile, mobileWorkStatus } from "../../lib/mobileWorkspaces";
import { useAuth } from "../../lib/auth";
import MobileMetricStrip from "../components/MobileMetricStrip";
import MobileStatusBadge from "../components/MobileStatusBadge";

function fmt(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

export default function MobileLoadingWorkspace({ order, canEdit, onUpdated }) {
  const { hasPerm } = useAuth();
  const [params] = useSearchParams();
  const sjFiles = sjFilesForLoading(order);
  const activeSj = matchLoadingSjFile(sjFiles, params.get("shipment"));
  const poLines = useMemo(() => loadingPoLines(order, activeSj?.id), [order, activeSj?.id]);
  const sjLines = useMemo(() => sjLinesForDiscrepancy(order, activeSj?.id), [order, activeSj?.id]);
  const avail = loadingAvailability(order, poLines, sjFiles);
  const skipPacking = loadingSkipsPacking(poLines);
  const statusTarget = avail.packed > 0 ? avail.packed : (avail.loaded + avail.remaining);
  const status = mobileWorkStatus(avail.loaded, statusTarget);
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
    <section data-testid="mobile-ws-loading">
      <div className="mop-ws-header"><h2>LOADING</h2></div>
      <p className="mop-card-meta" data-testid="mobile-loading-subtitle">Verifikasi Kesesuaian SJ</p>
      <MobileStatusBadge status={status} />
      <div className="mop-card-meta" data-testid="mobile-loading-verification">
        {verification?.label || "Menunggu Verifikasi"}
      </div>
      <MobileMetricStrip items={[
        { label: "Packed Qty", value: avail.packed.toLocaleString("id-ID") },
        { label: "Loaded Qty", value: avail.loaded.toLocaleString("id-ID") },
        { label: "Remaining Qty", value: avail.remaining.toLocaleString("id-ID") },
      ]} />
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
          href={suratJalanFileUrl(order.id, activeSj.id)}
          target="_blank"
          rel="noreferrer"
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
