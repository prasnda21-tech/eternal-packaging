import { useEffect, useState } from "react";
import { Package, CheckCircle2 } from "lucide-react";
import { formatDate } from "../lib/constants";
import { packingLinesForOrder, packingSubmitEnabled } from "../lib/packingCard";
import { MetricCell, MetricRow, QuantityInput, QtyOpsHead, statusClassFor } from "./qtyUi";

function fmt(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function initialDraft(packingLines) {
  const map = {};
  for (const row of packingLines) {
    map[row.line_idx] = Number(row.packed || 0);
  }
  return map;
}

// Packing = print (setelah selesai operator) + non_print (langsung tersedia).
// Input & persist PER LINE. Total PO hanya ringkasan (SUM).
export default function PackingPanel({ order, canManage, busy, onSaveQty, onComplete, footerActions = null }) {
  const pi = order.packing_info || {};
  const packingLines = packingLinesForOrder(order);
  const [draft, setDraft] = useState(() => initialDraft(packingLines));
  const [notes, setNotes] = useState(pi.notes ?? "");

  useEffect(() => {
    setDraft(initialDraft(packingLinesForOrder(order)));
    setNotes((order.packing_info || {}).notes ?? "");
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
  const requiredTotal = typeof order.packing_required_total === "number"
    ? order.packing_required_total
    : packingLines.reduce((s, r) => s + Number(r.qty_po || 0), 0);
  const available = typeof order.available_for_packing === "number"
    ? order.available_for_packing
    : packingLines.reduce((s, r) => s + Number(r.available || 0), 0);
  const printCompleted = Number(order.print_completed_qty || 0);
  const printAvailable = typeof order.print_available_for_packing === "number"
    ? order.print_available_for_packing
    : printCompleted;
  const helperPrepared = order.helper_prepared_print_qty;
  const nonPrintTotal = Number(order.non_print_qty_total || 0);
  const shortageSaved = Math.max(requiredTotal - packedSaved, 0);
  const canProceedPartial = hasQty && packedSaved > 0;
  const isComplete = hasQty && packedSaved >= requiredTotal;
  const remainingAvailable = Math.max(available - packedSaved, 0);
  const canSubmitQty = packingSubmitEnabled(canManage, available);
  const draftTotal = packingLines.reduce((s, r) => s + Number(draft[r.line_idx] || 0), 0);
  const dirty = packingLines.some((r) => Number(draft[r.line_idx] ?? r.packed) !== Number(r.packed || 0));
  const canSave = canSubmitQty && !busy && draftTotal > 0 && (dirty || !hasLinesStored);
  const legacyTotalOnly = !hasLinesStored && Number(pi.qty_packed || 0) > 0 && (order.line_items || []).length > 1;

  const setLineQty = (lineIdx, value) => {
    setDraft((prev) => ({ ...prev, [lineIdx]: value }));
  };

  const handleSave = async () => {
    const lines = packingLines.map((row) => ({
      line_idx: row.line_idx,
      packed_qty: Math.max(0, parseInt(draft[row.line_idx], 10) || 0),
    }));
    if (!lines.some((l) => l.packed_qty > 0)) return;
    await onSaveQty(lines, notes || "");
  };

  const packingStatus = fullyPacked ? "DONE" : hasQty ? "IN PROGRESS" : "READY";

  return (
    <section className="panel packing-panel" data-testid="packing-panel">
      <QtyOpsHead icon={Package} title="Packing">
        <span className={`qty-status ${statusClassFor(packingStatus)}`} data-testid="packing-semantic-badge">
          {packingStatus}
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
        Qty Packing diisi per barang. Total di atas hanya penjumlahan. Non-cetak: <b>{fmt(nonPrintTotal)}</b> (langsung) · Cetak siap packing:{" "}
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
                <th>Process</th>
                <th className="num">Target</th>
                <th className="num">Prepared</th>
                <th className="num">Printed</th>
                <th className="num">Unprinted</th>
                <th className="num">Available</th>
                <th className="num">Packed</th>
                <th className="num">Remain</th>
                <th className="num">Input</th>
              </tr>
            </thead>
            <tbody>
              {packingLines.map((row, idx) => {
                const isPrint = !!row.is_print;
                const inputVal = draft[row.line_idx] ?? row.packed ?? 0;
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
                        <QuantityInput
                          label=""
                          className="is-compact"
                          name="packing_qty_input"
                          min="0"
                          max={row.available || 0}
                          value={inputVal}
                          onChange={(e) => setLineQty(row.line_idx, e.target.value)}
                          disabled={busy || !canSubmitQty}
                          testid={`packing-qty-input-${row.line_idx}`}
                          id={row.line_idx === 0 ? "packing-qty-input" : undefined}
                          aria-label={`Qty Packing ${row.name}`}
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
                <td className="num mono" data-testid="packing-draft-total">{fmt(draftTotal)}</td>
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
              disabled={busy || !canSubmitQty}
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
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            <button
              type="button"
              className="primary"
              onClick={handleSave}
              disabled={!canSave}
              data-testid="save-packing-qty-button"
            >
              Simpan Qty
            </button>
            <button
              type="button"
              className="ghost"
              onClick={onComplete}
              disabled={busy || !canProceedPartial}
              title={
                !hasQty
                  ? "Qty Packing harus diisi terlebih dahulu"
                  : isComplete
                    ? "Selesaikan Packing lengkap dan lanjut ke Surat Jalan"
                    : "Lanjut ke Surat Jalan dengan qty packing sebagian"
              }
              data-testid="complete-packing-button"
              data-mark-packed="true"
            >
              <CheckCircle2 size={16} /> {isComplete ? "Selesai Packing" : "Lanjut Packing Sebagian"}
            </button>
          </div>
        </div>
      )}
      {footerActions}
    </section>
  );
}
