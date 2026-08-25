import { useEffect, useState } from "react";
import { AlertTriangle, Info, PackageCheck } from "lucide-react";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";
import { formatDisplayDateTime } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { getWorkflowReady } from "../lib/stageState";
import { newActionId, entriesForLine } from "../lib/workflowEntries";
import { MetricCell, MetricRow, QtyOpsHead, statusClassFor } from "./qtyUi";
import AppendQtyEntries from "./AppendQtyEntries";
import ConfirmActionModal from "./ConfirmActionModal";

/**
 * HelperPrepPanel — Persiapan Helper PER LINE ITEM CETAK.
 * Revisi routing: hanya line item dengan production_process === "print" yang
 * masuk scope Persiapan Helper. Line non-print DIABAIKAN. Qty Dibutuhkan =
 * read-only dari line.quantity (source of truth PO). Legacy line tanpa
 * production_process dianggap "print" (backward compatible).
 */
const isPrintLine = (li) => (li?.production_process || "print").toLowerCase() !== "non_print";

export default function HelperPrepPanel({ order, canEdit, onUpdated, footerActions = null }) {
  const { user } = useAuth();
  const allLineItems = order?.line_items || [];
  const printLines = allLineItems
    .map((li, idx) => ({ li, idx }))
    .filter(({ li }) => isPrintLine(li));
  const helperItems = order?.helper_prep?.items || [];
  const helperEntries = order?.helper_prep?.entries || [];
  const requiresPrinting = order?.requires_printing !== false;

  const emptyDraft = () => {
    const map = {};
    for (const { idx } of printLines) map[idx] = "";
    return map;
  };
  const [draft, setDraft] = useState(emptyDraft);
  const [notes, setNotes] = useState(order?.helper_prep?.notes || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(null);

  useEffect(() => {
    setDraft(emptyDraft());
    setNotes(order?.helper_prep?.notes || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order?.id, order?.helper_prep?.updated_at, printLines.length]);

  const preparedOf = (idx) => {
    const existing = helperItems.find((it) => Number(it.line_idx) === Number(idx));
    return Number(existing?.qty_prepared || 0);
  };
  const totalRequired = printLines.reduce((s, { li }) => s + Number(li.quantity || 0), 0);
  const totalPrepared = printLines.reduce((s, { idx }) => s + preparedOf(idx), 0);
  const totalShortage = Math.max(totalRequired - totalPrepared, 0);
  const ready = getWorkflowReady(order);
  const helperLocked = !ready.helper;
  const saved = !!(order?.helper_prep?.updated_at);
  const statusLabel = helperLocked
    ? "IDLE"
    : (totalShortage === 0 && totalRequired > 0
      ? "DONE"
      : saved
        ? (totalShortage > 0 ? "IN PROGRESS" : "SAVED")
        : "READY");

  if (!requiresPrinting) {
    return (
      <section className="panel qty-ops-panel" data-testid="helper-prep-panel">
        <QtyOpsHead icon={PackageCheck} title="Persiapan Helper">
          <span className="qty-status" data-testid="helper-not-required-badge">Tidak Diperlukan</span>
        </QtyOpsHead>
        <p className="qty-ops-note" data-testid="helper-not-required-note">
          <Info size={12}/> PO ini tidak memiliki barang dengan proses Cetak. Persiapan Helper hanya menyiapkan barang yang akan melalui proses Cetak.
        </p>
        {footerActions}
      </section>
    );
  }

  const openConfirm = (idx, li) => {
    if (helperLocked) {
      setErr("Persiapan Helper tersedia setelah Penjadwalan disimpan.");
      return;
    }
    const qty = Math.max(0, Number(draft[idx] || 0));
    if (!qty) {
      setErr("Qty tambahan wajib diisi.");
      return;
    }
    setErr("");
    setConfirm({
      idx,
      name: li.name,
      qty,
      actionId: newActionId(),
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };

  const execute = async () => {
    if (!confirm || busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.updateHelperPrepItems(order.id, [{
        line_idx: confirm.idx,
        qty: confirm.qty,
        action_id: confirm.actionId,
      }], notes);
      onUpdated?.(r.order);
      setDraft((prev) => ({ ...prev, [confirm.idx]: "" }));
      setConfirm(null);
    } catch (e) {
      setErr(formatApiError(e, "Gagal menyimpan Persiapan Helper"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel qty-ops-panel" data-testid="helper-prep-panel">
        <QtyOpsHead icon={PackageCheck} title="Persiapan Helper">
          <span className={`qty-status ${statusClassFor(statusLabel)}`}>{statusLabel}</span>
          <span className="muted small" data-testid="helper-scope-badge">
            Scope Cetak: {totalRequired.toLocaleString("id-ID")} pcs
          </span>
          {totalShortage > 0 && (
            <span className="urgent-badge" data-testid="helper-total-shortage-badge">
              <AlertTriangle size={12}/> KURANG {totalShortage.toLocaleString("id-ID")} PCS
            </span>
          )}
          {totalShortage === 0 && totalPrepared >= totalRequired && totalRequired > 0 && (
            <span className="qty-status qty-status-done" data-testid="helper-total-ok-badge">DONE</span>
          )}
        </QtyOpsHead>

      <MetricRow columns={3}>
        <MetricCell label="Required" value={totalRequired} testid="helper-total-required" />
        <MetricCell label="Prepared" value={totalPrepared} testid="helper-total-prepared" />
        <div className="ind-metric" data-testid="helper-total-shortage">
          <span className="ind-metric-label">Remain</span>
          <strong>
            {totalShortage > 0 ? `-${totalShortage.toLocaleString("id-ID")}` : "0"}
            <span className="ind-metric-unit">pcs</span>
          </strong>
        </div>
      </MetricRow>

      <div className="qty-ops-body">
      <p className="qty-ops-note">
        Menampilkan hanya barang dengan proses <b>Cetak</b>. Barang Non-Cetak tidak melewati Persiapan Helper.
      </p>

      <table className="ind-table" data-testid="helper-prep-table">
        <thead>
          <tr>
            <th>Item</th>
            <th className="num">Target</th>
            <th className="num">Prepared</th>
            <th className="num">Remain</th>
            <th>Qty Persiapan</th>
          </tr>
        </thead>
        <tbody>
        {printLines.length === 0 && (
          <tr>
            <td colSpan={5} className="muted small">
              Tidak ada line item Cetak. (Semua line item sudah diset Non-Cetak.)
            </td>
          </tr>
        )}
        {printLines.map(({ li, idx }) => {
          const req = Number(li.quantity || 0);
          const prep = preparedOf(idx);
          const shortage = Math.max(req - prep, 0);
          return (
            <tr key={idx} data-testid={`helper-prep-row-${idx}`}>
              <td className="ind-item">
                <strong>{li.name}</strong>
                {li.color && <div className="muted small">{li.color}</div>}
              </td>
              <td className="num" data-testid={`helper-required-${idx}`}>{req.toLocaleString("id-ID")}</td>
              <td className="num">{prep.toLocaleString("id-ID")}</td>
              <td className="num" data-testid={`helper-shortage-${idx}`}>
                {shortage > 0 ? `-${shortage.toLocaleString("id-ID")}` : "0"}
              </td>
              <td>
                <AppendQtyEntries
                  entries={entriesForLine(helperEntries, idx)}
                  snapshotQty={prep}
                  snapshotAt={order?.helper_prep?.updated_at}
                  snapshotName={order?.helper_prep?.updated_by_name}
                  draft={draft[idx] ?? ""}
                  onDraftChange={(v) => setDraft({ ...draft, [idx]: v })}
                  onConfirmClick={() => openConfirm(idx, li)}
                  canEdit={canEdit && !helperLocked}
                  busy={busy}
                  remaining={shortage}
                  testid={`helper-append-${idx}`}
                  inputTestid={`helper-prepared-${idx}`}
                  confirmTestid={idx === 0 ? "helper-save-button" : `helper-confirm-${idx}`}
                />
              </td>
            </tr>
          );
        })}
        </tbody>
      </table>

      <label className="qty-notes">
        <span className="qty-notes-label">Catatan</span>
        <input
          type="text"
          className="manual-input"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          disabled={!canEdit || busy || helperLocked}
          placeholder="Catatan opsional"
          data-testid="helper-notes-input"
        />
      </label>

      {helperLocked && (
        <p className="muted small" data-testid="helper-locked-note" style={{ marginTop: 10 }}>
          Persiapan Helper berjalan paralel dengan Screener dan tersedia otomatis setelah Penjadwalan disimpan.
        </p>
      )}
      {err && <p style={{ color: "#8f1f1f", marginTop: 8 }} data-testid="helper-prep-error">{err}</p>}

      {!canEdit && (
        <p className="muted small" style={{ marginTop: 10 }}>Butuh permission <code>helper.prepare</code>.</p>
      )}
      {footerActions}
      </div>
      <ConfirmActionModal
        open={!!confirm}
        stage="Persiapan Helper"
        qty={confirm?.qty}
        name={user?.name || user?.email}
        time={confirm?.time}
        extra={confirm?.name ? <p className="muted small">{confirm.name}</p> : null}
        busy={busy}
        error={err}
        onCancel={() => { if (!busy) setConfirm(null); }}
        onConfirm={execute}
      />
    </section>
  );
}
