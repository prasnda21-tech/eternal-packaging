import { useEffect, useState } from "react";
import { Package } from "lucide-react";
import { formatDate, formatDisplayDateTime } from "../lib/constants";
import { packingLinesForOrder, packingSubmitEnabled } from "../lib/packingCard";
import { entriesForLine, newActionId } from "../lib/workflowEntries";
import { useAuth } from "../lib/auth";
import { MetricCell, MetricRow, QtyOpsHead, statusClassFor } from "./qtyUi";
import AppendQtyEntries from "./AppendQtyEntries";
import ConfirmActionModal from "./ConfirmActionModal";

function fmt(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function emptyDraft(packingLines) {
  const map = {};
  for (const row of packingLines) map[row.line_idx] = "";
  return map;
}

// Packing = line Cetak saja (setelah selesai operator). Non-cetak skip Packing.
// Input & persist PER LINE. Total PO hanya ringkasan (SUM). Append-only increments.
export default function PackingPanel({ order, canManage, busy, onSaveQty, footerActions = null }) {
  const { user } = useAuth();
  const pi = order.packing_info || {};
  const packingLines = packingLinesForOrder(order);
  const events = pi.events || [];
  const [draft, setDraft] = useState(() => emptyDraft(packingLines));
  const [notes, setNotes] = useState(pi.notes ?? "");
  const [confirm, setConfirm] = useState(null);
  const [localBusy, setLocalBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    setDraft(emptyDraft(packingLinesForOrder(order)));
    setNotes((order.packing_info || {}).notes ?? "");
    setErr("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order?.id, pi.updated_at, packingLines.length]);

  const hasLinesStored = Array.isArray(pi.lines) && pi.lines.length > 0;
  const packedFromLines = packingLines.reduce((s, r) => s + Number(r.packed || 0), 0);
  const packedSaved = hasLinesStored
    ? packedFromLines
    : Number(pi.qty_packed || 0);
  const hasQty = packedSaved > 0;
  const semantic = order.packing_status_semantic || "Belum";
  const fullyPacked = semantic === "Sudah";
  const requiredTotal = packingLines.reduce((s, r) => s + Number(r.qty_po || 0), 0)
    || (typeof order.print_qty_total === "number" ? order.print_qty_total : 0);
  const available = typeof order.available_for_packing === "number"
    ? order.available_for_packing
    : packingLines.reduce((s, r) => s + Number(r.available || 0), 0);
  const printCompleted = Number(order.print_completed_qty || 0);
  const printAvailable = typeof order.print_available_for_packing === "number"
    ? order.print_available_for_packing
    : printCompleted;
  const helperPrepared = order.helper_prepared_print_qty;
  const shortageSaved = Math.max(requiredTotal - packedSaved, 0);
  const remainingAvailable = Math.max(available - packedSaved, 0);
  const canSubmitQty = packingSubmitEnabled(canManage, available);
  const saving = busy || localBusy;
  const legacyTotalOnly = !hasLinesStored && Number(pi.qty_packed || 0) > 0 && (order.line_items || []).length > 1;

  const openConfirm = (row) => {
    const qty = Math.max(0, parseInt(draft[row.line_idx], 10) || 0);
    if (!qty) return;
    setErr("");
    setConfirm({
      row,
      qty,
      actionId: newActionId(),
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };

  const execute = async () => {
    if (!confirm || saving) return;
    setLocalBusy(true);
    setErr("");
    try {
      await onSaveQty([{
        line_idx: confirm.row.line_idx,
        qty: confirm.qty,
        action_id: confirm.actionId,
      }], notes || "");
      setDraft((prev) => ({ ...prev, [confirm.row.line_idx]: "" }));
      setConfirm(null);
    } catch (e) {
      const d = e?.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal menyimpan Qty Packing"));
    } finally {
      setLocalBusy(false);
    }
  };

  const packingStatus = fullyPacked ? "DONE" : hasQty ? "IN PROGRESS" : "READY";
  const packingStatusLabel = packingStatus === "DONE"
    ? "Selesai"
    : packingStatus === "IN PROGRESS"
      ? "Berjalan"
      : "Siap";

  // Full / all-line non-cetak: jangan tampilkan Packing sama sekali.
  if ((order.line_items || []).length > 0 && packingLines.length === 0) {
    return null;
  }

  return (
    <section className="panel packing-panel" data-testid="packing-panel">
      <QtyOpsHead icon={Package} title="Packing">
        <span className={`qty-status ${statusClassFor(packingStatus)}`} data-testid="packing-semantic-badge">
          {packingStatusLabel}
        </span>
      </QtyOpsHead>

      <MetricRow testid="packing-summary">
        <MetricCell label="Target" value={requiredTotal} testid="packing-target-badge" />
        <MetricCell label="Tersedia" value={available} testid="packing-available-badge" />
        <MetricCell label="Selesai" value={packedSaved} testid="packing-cumulative-packed" />
        <MetricCell label="Sisa" value={remainingAvailable} testid="packing-remaining-available" />
      </MetricRow>

      <div className="qty-ops-body">
      <p className="muted small" data-testid="packing-availability-note" style={{ marginTop: 0 }}>
        Qty Packing diisi per barang Cetak. Non-cetak tidak perlu Packing (langsung Loading).
        Cetak siap packing:{" "}
        <b data-testid="packing-print-available">{fmt(printAvailable)}</b>
        {" "}(selesai operator {fmt(printCompleted)}
        {helperPrepared !== null && helperPrepared !== undefined
          ? `, cap helper ${fmt(helperPrepared)}`
          : ""}
        ).
      </p>

      {legacyTotalOnly && (
        <p className="muted small" data-testid="packing-legacy-total-note" style={{ color: "#8a5a12" }}>
          PO ini masih memakai Qty Packing total lama ({fmt(pi.qty_packed)} pcs). Tracking per barang dimulai dari 0 sampai disimpan per line.
        </p>
      )}
      </div>

      {packingLines.length > 0 && (
        <div className="packing-lines-wrap">
          <table className="packing-lines-table" data-testid="packing-lines-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Proses</th>
                <th className="num">Target</th>
                <th className="num">Disiapkan</th>
                <th className="num">Tercetak</th>
                <th className="num">Belum tercetak</th>
                <th className="num">Tersedia</th>
                <th className="num">Dipacking</th>
                <th className="num">Sisa</th>
                <th className="num">Input</th>
              </tr>
            </thead>
            <tbody>
              {packingLines.map((row, idx) => {
                const isPrint = !!row.is_print;
                return (
                  <tr key={row.line_idx ?? idx} data-testid={`packing-line-row-${idx}`}>
                    <td>
                      <div><strong>{row.name}</strong></div>
                      {row.color && <div className="muted small">{row.color}</div>}
                    </td>
                    <td>
                      <span
                        className={`pack-badge ${isPrint ? "pack-badge-print" : "pack-badge-nonprint"}`}
                        data-testid={`packing-line-process-${idx}`}
                      >
                        {isPrint ? "Cetak" : "Non-Cetak"}
                      </span>
                    </td>
                    <td className="num mono" data-testid={`packing-line-qty-${idx}`}>{fmt(row.qty_po)}</td>
                    <td className="num mono" data-testid={`packing-line-prepared-${idx}`}>
                      {isPrint
                        ? (row.helper_prepared == null ? "—" : fmt(row.helper_prepared))
                        : "—"}
                    </td>
                    <td className="num mono" data-testid={`packing-line-printed-${idx}`}>
                      {isPrint ? fmt(row.print_completed) : "—"}
                    </td>
                    <td className="num mono" data-testid={`packing-line-unprinted-${idx}`}>
                      {isPrint ? fmt(row.print_remaining) : "—"}
                    </td>
                    <td className="num mono pack-cell-ready" data-testid={`packing-line-available-${idx}`}>{fmt(row.available)}</td>
                    <td className="num mono pack-cell-done" data-testid={`packing-line-packed-${idx}`}>{fmt(row.packed)}</td>
                    <td className="num mono pack-cell-rest" data-testid={`packing-line-remaining-${idx}`}>{fmt(row.remaining)}</td>
                    <td className="num packing-line-input-cell">
                      {fullyPacked || !canManage ? (
                        <span className="mono">{fmt(row.packed)}</span>
                      ) : (
                        <AppendQtyEntries
                          entries={entriesForLine(events, row.line_idx)}
                          snapshotQty={row.packed}
                          snapshotAt={pi.updated_at}
                          snapshotName={pi.updated_by_name}
                          draft={draft[row.line_idx] ?? ""}
                          onDraftChange={(v) => setDraft((prev) => ({ ...prev, [row.line_idx]: v }))}
                          onConfirmClick={() => openConfirm(row)}
                          canEdit={canSubmitQty && Number(row.remaining || 0) > 0}
                          busy={saving}
                          remaining={row.remaining}
                          testid={`packing-append-${row.line_idx}`}
                          inputTestid={`packing-qty-input-${row.line_idx}`}
                          confirmTestid={row.line_idx === 0 ? "save-packing-qty-button" : `packing-confirm-${row.line_idx}`}
                        />
                      )}
                    </td>
                  </tr>
                );
              })}
              <tr className="packing-lines-total">
                <td colSpan={2}>Total</td>
                <td className="num mono" data-testid="packing-target-total">{fmt(requiredTotal)}</td>
                <td className="num mono">
                  {helperPrepared == null ? "—" : fmt(helperPrepared)}
                </td>
                <td className="num mono">{fmt(printCompleted)}</td>
                <td className="num mono">
                  {fmt(packingLines.reduce((s, r) => s + Number(r.print_remaining || 0), 0))}
                </td>
                <td className="num mono">{fmt(available)}</td>
                <td className="num mono">{fmt(packedSaved)}</td>
                <td className="num mono">{fmt(remainingAvailable)}</td>
                <td className="num mono" data-testid="packing-draft-total">{fmt(packingLines.reduce((s, r) => s + Number(draft[r.line_idx] || 0), 0))}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {hasQty && (
        <p className="muted small" data-testid="packing-progress-line" style={{ marginTop: 8 }}>
          Qty Packing tersimpan: <b data-testid="packing-saved-qty">{fmt(packedSaved)}</b>
          {" "}/ {fmt(requiredTotal)}
          {shortageSaved > 0 && (
            <span style={{ color: "#8f1f1f", marginLeft: 6 }} data-testid="packing-saved-shortage">
              — kurang {fmt(shortageSaved)}
            </span>
          )}
          {shortageSaved === 0 && (
            <span style={{ color: "#2b7a2b", marginLeft: 6 }} data-testid="packing-saved-complete">— lengkap</span>
          )}
        </p>
      )}

      {available <= 0 && !fullyPacked && (
        <p className="muted small" data-testid="packing-no-available-note">
          Belum ada qty yang tersedia untuk Packing. Qty Tidak Cetak dan qty Cetak yang sudah selesai operator akan muncul di sini.
        </p>
      )}

      {fullyPacked ? (
        <p className="muted small" data-testid="packing-done-note">
          Packing sudah lengkap — {hasQty ? `${fmt(packedSaved)} pcs` : ""}{pi.updated_by_name ? ` · oleh ${pi.updated_by_name}` : ""}{pi.updated_at ? ` · ${formatDate(pi.updated_at)}` : ""}.
        </p>
      ) : !canManage ? (
        <p className="muted small" data-testid="packing-readonly-note">
          Anda tidak memiliki permission <code>packing.update</code> untuk mengisi Qty Packing.
        </p>
      ) : (
        <div className="packing-form" data-testid="packing-form">
          <label>
            <span className="lbl">Catatan (opsional)</span>
            <input
              className="packing-notes-input"
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={saving || !canSubmitQty}
              data-testid="packing-notes-input"
            />
          </label>
          {pi.updated_at && (
            <p className="muted small" data-testid="packing-last-update">
              Terakhir diupdate {formatDate(pi.updated_at)}
              {pi.updated_by_name ? ` oleh ${pi.updated_by_name}` : ""}
              {hasQty ? ` — Qty tersimpan: ${fmt(packedSaved)} pcs` : ""}
            </p>
          )}
          {err && <p className="error" data-testid="packing-error">{err}</p>}
        </div>
      )}
      {footerActions}
      <ConfirmActionModal
        open={!!confirm}
        stage="Packing"
        qty={confirm?.qty}
        name={user?.name || user?.email}
        time={confirm?.time}
        extra={confirm?.row?.name ? <p className="muted small">{confirm.row.name}</p> : null}
        busy={saving}
        error={err}
        onCancel={() => { if (!saving) setConfirm(null); }}
        onConfirm={execute}
      />
    </section>
  );
}
