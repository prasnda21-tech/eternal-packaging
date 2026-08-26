import { useEffect, useMemo, useState } from "react";
import { CalendarClock, Info, Plus, Trash2, Users } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDateTime } from "../lib/constants";
import { newActionId } from "../lib/workflowEntries";
import { getWorkflowReady } from "../lib/stageState";
import { MetricCell, MetricRow, QuantityInput, QtyOpsHead, statusClassFor } from "./qtyUi";
import AppendQtyEntries from "./AppendQtyEntries";
import ConfirmActionModal from "./ConfirmActionModal";

const isPrintLine = (li) => (li?.production_process || "print").toLowerCase() !== "non_print";

export default function OperatorSplitPanel({ order, canEdit, onUpdated, footerActions = null, mode = "all" }) {
  const { user, hasPerm } = useAuth();
  const [operators, setOperators] = useState([]);
  const [splits, setSplits] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [qtyDrafts, setQtyDrafts] = useState({});
  const [confirm, setConfirm] = useState(null);

  const requiresPrinting = order?.requires_printing !== false;
  const lineItems = order?.line_items || [];

  // Cetak lines (with original idx preserved).
  const printLines = useMemo(
    () => lineItems.map((li, idx) => ({ li, idx })).filter(({ li }) => isPrintLine(li)),
    [lineItems]
  );
  const printQtyTotal = useMemo(
    () => (typeof order?.print_qty_total === "number"
      ? order.print_qty_total
      : printLines.reduce((s, { li }) => s + Number(li.quantity || 0), 0)),
    [order?.print_qty_total, printLines]
  );

  useEffect(() => {
    setSplits(
      (order.operator_splits || []).map((s) => ({
        operator_email: s.operator_email,
        operator_name: s.operator_name,
        quantity: s.quantity,
        id: s.id,
        stage: s.stage,
        line_idx: s.line_idx ?? "", // legacy = "" (unassigned line)
        work_date: s.work_date || "", // Fix: prefill tanggal existing per assignment
      }))
    );
  }, [order.operator_splits]);

  useEffect(() => {
    if (!canEdit) return;
    api.listOperators().then((d) => setOperators(d.operators || [])).catch(() => {});
  }, [canEdit]);

  const totalAssigned = splits.reduce((s, x) => s + Number(x.quantity || 0), 0);
  const assignmentCap = typeof order?.operator_assignment_cap === "number"
    ? order.operator_assignment_cap
    : printQtyTotal;
  const remaining = assignmentCap - totalAssigned;
  const ready = getWorkflowReady(order);
  const scheduleLocked = mode !== "progress" && !ready.penjadwalan;
  const printQtyReady = ready.print_qty === true;
  const progressLocked = mode !== "schedule" && !printQtyReady;
  const screenerGateMsg = order?.screener_gate_reason || "Qty Screener harus diisi sebelum pekerjaan Operator.";
  const printQtyGateMsg = order?.print_qty_gate_reason
    || (!order?.sample_approved
      ? "Cetak Sample harus disetujui sebelum Qty Selesai Cetak dapat diisi."
      : screenerGateMsg);
  const showSchedule = mode === "all" || mode === "schedule";
  const showProgress = mode === "all" || mode === "progress";
  const persistedSplits = order.operator_splits || [];
  const completedQty = typeof order?.print_completed_qty === "number"
    ? Number(order.print_completed_qty)
    : persistedSplits.reduce((s, x) => s + Number(x.qty_completed || 0), 0);
  const printTarget = (typeof order?.print_qty_target === "number" && Number(order.print_qty_target) > 0)
    ? Number(order.print_qty_target)
    : (typeof order?.operator_assigned_qty === "number" && Number(order.operator_assigned_qty) > 0
      ? Number(order.operator_assigned_qty)
      : totalAssigned || printQtyTotal);
  const printRemaining = Math.max(0, printTarget - completedQty);
  const printStatus = order?.print_status
    || (completedQty <= 0
      ? "BELUM MULAI CETAK"
      : (printTarget > 0 && completedQty >= printTarget ? "SELESAI CETAK" : "BELUM SELESAI CETAK"));
  const scheduleStatus = totalAssigned > 0 ? "SAVED" : (scheduleLocked ? "IDLE" : "READY");
  const statusLabel = showProgress && !showSchedule ? printStatus : scheduleStatus;

  // Per-line assigned totals for UX guardrails.
  const perLineAssigned = useMemo(() => {
    const m = {};
    for (const s of splits) {
      if (s.line_idx === "" || s.line_idx === null || s.line_idx === undefined) continue;
      const key = Number(s.line_idx);
      m[key] = (m[key] || 0) + Number(s.quantity || 0);
    }
    return m;
  }, [splits]);

  const addRow = () => {
    setSplits([...splits, { operator_email: "", operator_name: "", quantity: 0, line_idx: printLines[0]?.idx ?? "", work_date: "" }]);
  };

  const updateRow = (i, patch) => {
    const next = [...splits];
    next[i] = { ...next[i], ...patch };
    setSplits(next);
  };

  const removeRow = (i) => {
    setSplits(splits.filter((_, idx) => idx !== i));
  };

  const save = async () => {
    setErr("");
    if (scheduleLocked) {
      setErr("Penjadwalan belum siap — selesaikan Design dan Payment terlebih dahulu.");
      return;
    }
    if (totalAssigned > assignmentCap) {
      setErr(`Total pembagian ${totalAssigned} melebihi total qty Cetak PO (${printQtyTotal}).`);
      return;
    }
    for (const s of splits) {
      if (!s.operator_email || !Number(s.quantity)) {
        setErr("Setiap baris harus punya operator dan kuantitas.");
        return;
      }
      if (s.line_idx === "" || s.line_idx === null || s.line_idx === undefined) {
        setErr("Setiap baris harus terikat ke barang (line Cetak).");
        return;
      }
      const lineQty = Number(lineItems[Number(s.line_idx)]?.quantity || 0);
      if (perLineAssigned[Number(s.line_idx)] > lineQty) {
        setErr(`Total pembagian untuk "${lineItems[Number(s.line_idx)]?.name}" melebihi qty line (${lineQty}).`);
        return;
      }
    }
    setBusy(true);
    try {
      const res = await api.assignOperators(order.id, splits.map((s) => ({
        operator_email: s.operator_email,
        operator_name: s.operator_name,
        quantity: Number(s.quantity),
        line_idx: Number(s.line_idx),
        work_date: s.work_date ? s.work_date : null,
      })));
      onUpdated(res.order);
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal menyimpan pembagian"));
    } finally {
      setBusy(false);
    }
  };

  const updateQty = async (op) => {
    if (progressLocked) {
      setErr(printQtyGateMsg);
      return;
    }
    const raw = qtyDrafts[op.id];
    const qty = Math.max(0, Number(raw || 0));
    if (!qty) {
      setErr("Qty tambahan wajib diisi.");
      return;
    }
    setErr("");
    setConfirm({
      op,
      qty,
      actionId: newActionId(),
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };

  const executeQty = async () => {
    if (!confirm || busy) return;
    if (progressLocked) {
      setErr(printQtyGateMsg);
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await api.updateOperatorProgress(order.id, confirm.op.id, confirm.qty, {
        qty: confirm.qty,
        action_id: confirm.actionId,
      });
      onUpdated(res.order);
      setQtyDrafts((prev) => ({ ...prev, [confirm.op.id]: "" }));
      setConfirm(null);
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal memperbarui qty"));
    } finally {
      setBusy(false);
    }
  };

  const panelTestId = mode === "progress" ? "operator-progress-panel" : "operator-split-panel";
  const title = mode === "progress" ? "Qty Selesai Cetak" : "Pembagian Jadwal";
  const HeadIcon = mode === "progress" ? Users : CalendarClock;

  // Full non-print → Pembagian Operator TIDAK DIPERLUKAN.
  if (!requiresPrinting) {
    return (
      <section className="panel qty-ops-panel" data-testid={panelTestId}>
        <QtyOpsHead icon={HeadIcon} title={title}>
          <span className="qty-status" data-testid="operator-not-required-badge">Tidak Diperlukan</span>
        </QtyOpsHead>
        <p className="qty-ops-note" data-testid="operator-not-required-note">
          <Info size={12}/> PO ini tidak memiliki barang dengan proses Cetak. Pembagian Operator hanya untuk barang Cetak.
        </p>
        {footerActions}
      </section>
    );
  }

  const lineName = (idx) => {
    const li = lineItems[Number(idx)];
    return li ? `${li.name}${li.color ? ` · ${li.color}` : ""}` : `Line #${idx}`;
  };

  return (
    <section className="panel qty-ops-panel" data-testid={panelTestId}>
      <QtyOpsHead icon={HeadIcon} title={title}>
        <span className={`qty-status ${statusClassFor(statusLabel)}`} data-testid={showProgress && !showSchedule ? "print-status-badge" : undefined}>{statusLabel}</span>
        {showSchedule && (
          <span className="muted small" data-testid="operator-scope-badge">
            Scope Cetak: {printQtyTotal.toLocaleString("id-ID")} pcs
          </span>
        )}
      </QtyOpsHead>

      {showProgress && (
        <MetricRow columns={4} testid="print-qty-metrics">
          <MetricCell label="Target Cetak" value={printTarget} testid="print-qty-target" />
          <MetricCell label="Selesai" value={completedQty} testid="print-qty-completed" />
          <MetricCell label="Sisa" value={printRemaining} testid="print-qty-remaining" />
          <MetricCell
            label="Status"
            value={printStatus}
            format="text"
            className="is-status"
            testid="print-status"
          />
        </MetricRow>
      )}

      {showSchedule && (
        <MetricRow columns={3}>
          <MetricCell label="Target" value={printQtyTotal} />
          <MetricCell label="Allocated" value={totalAssigned} />
          <MetricCell label="Remain" value={remaining} />
        </MetricRow>
      )}

      <div className="qty-ops-body">
      {printLines.length > 0 && (
        <div className="qty-print-chips muted small" data-testid="operator-print-lines-info">
          Barang Cetak: {printLines.map(({ li, idx }) => (
            <span key={idx}>
              {li.name} {Number(li.quantity || 0).toLocaleString("id-ID")}
              {perLineAssigned[idx] ? ` / assigned ${perLineAssigned[idx].toLocaleString("id-ID")}` : ""}
              {idx < printLines.length - 1 ? " · " : ""}
            </span>
          ))}
        </div>
      )}

      {scheduleLocked && showSchedule && (
        <p className="muted small" data-testid="penjadwalan-locked-note">
          Penjadwalan otomatis tersedia setelah Design dan Payment (termasuk approval/bypass) selesai.
        </p>
      )}
      {progressLocked && showProgress && (
        <p className="muted small" data-testid="operator-sample-gate-note">{printQtyGateMsg}</p>
      )}

      {showProgress && (
      <div className="split-list">
        {persistedSplits.length === 0 && (
          <p className="empty">Belum ada operator ditugaskan.</p>
        )}
        {persistedSplits.length > 0 && (
          <div className="ind-op-head">
            <span>Operator</span>
            <span className="num">Allocated</span>
            <span className="num">Completed</span>
            <span className="num">Remain</span>
            <span>Qty selesai</span>
            <span></span>
          </div>
        )}
        {persistedSplits.map((op) => {
          const isSelf = user?.email === op.operator_email;
          const canEditQty = hasPerm("operator.update") && (isSelf || hasPerm("schedule.edit"));
          const draft = qtyDrafts[op.id] ?? "";
          const opRemaining = Math.max(0, op.quantity - (op.qty_completed || 0));
          const pct = op.quantity ? Math.round(((op.qty_completed || 0) / op.quantity) * 100) : 0;
          const opLineLabel = (op.line_idx === null || op.line_idx === undefined)
            ? "(legacy)"
            : lineName(op.line_idx);
          return (
            <div key={op.id} className="split-row ind-op-row" data-testid={`operator-progress-${op.operator_email}`}>
              <div className="qty-line-name">
                <strong>{op.operator_name}</strong>
                <span className="mono" data-testid={`operator-line-${op.operator_email}`}>{opLineLabel}</span>
                <div className="qty-progress-track pct-track sm" aria-hidden="true">
                  <div className="qty-progress-fill pct-fill" style={{ width: `${pct}%` }} />
                </div>
              </div>
              <div className="ind-op-qty">{op.quantity.toLocaleString("id-ID")}</div>
              <div className="ind-op-qty">{Number(op.qty_completed || 0).toLocaleString("id-ID")}</div>
              <div className="ind-op-qty">{opRemaining.toLocaleString("id-ID")}</div>
              {canEditQty ? (
                <AppendQtyEntries
                  entries={op.progress_entries}
                  snapshotQty={op.qty_completed}
                  snapshotAt={op.updated_at}
                  snapshotName={op.operator_name}
                  draft={draft}
                  onDraftChange={(v) => setQtyDrafts({ ...qtyDrafts, [op.id]: v })}
                  onConfirmClick={() => updateQty(op)}
                  canEdit={!progressLocked}
                  busy={busy}
                  remaining={opRemaining}
                  testid={`qty-append-${op.operator_email}`}
                />
              ) : <span />}
            </div>
          );
        })}
      </div>
      )}

      {canEdit && showSchedule && (
        <div className="atur-pembagian-card" data-testid="atur-pembagian-card">
          <div className="split-edit-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
            <h4 style={{ margin: 0 }} data-testid="atur-pembagian-title">Atur Pembagian</h4>
            <span className={remaining < 0 ? "warn" : "muted"}>
              Sisa cap: {remaining.toLocaleString("id-ID")} pcs (scope Cetak)
            </span>
          </div>
          {splits.map((s, i) => {
            const lineQty = Number(lineItems[Number(s.line_idx)]?.quantity || 0);
            const lineAssigned = perLineAssigned[Number(s.line_idx)] || 0;
            const overLine = s.line_idx !== "" && lineAssigned > lineQty;
            return (
              <div key={i} className="split-edit-row qty-assign-card ind-assign-row">
                <label className="qty-field">
                  <span className="qty-field-label">Barang</span>
                  <select
                    value={s.line_idx}
                    onChange={(e) => updateRow(i, { line_idx: e.target.value === "" ? "" : Number(e.target.value) })}
                    data-testid={`split-line-select-${i}`}
                  >
                    <option value="">-- Pilih barang (Cetak) --</option>
                    {printLines.map(({ li, idx }) => (
                      <option key={idx} value={idx}>
                        {li.name}{li.color ? ` · ${li.color}` : ""} ({Number(li.quantity || 0).toLocaleString("id-ID")})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="qty-field">
                  <span className="qty-field-label">Operator</span>
                  <select
                    value={s.operator_email}
                    onChange={(e) => {
                      const op = operators.find((o) => o.email === e.target.value);
                      updateRow(i, { operator_email: e.target.value, operator_name: op?.name || e.target.value });
                    }}
                    data-testid={`split-operator-select-${i}`}
                  >
                    <option value="">-- Pilih operator --</option>
                    {operators.map((o) => (
                      <option key={o.email} value={o.email}>{o.name}</option>
                    ))}
                  </select>
                </label>
                <QuantityInput
                  label="Qty dialokasikan"
                  min="1"
                  value={s.quantity}
                  onChange={(e) => updateRow(i, { quantity: e.target.value })}
                  placeholder="Qty"
                  testid={`split-quantity-input-${i}`}
                  className={overLine ? "is-invalid" : ""}
                  title={overLine ? `Total ${lineAssigned} melebihi qty line ${lineQty}` : undefined}
                />
                <label className="qty-field">
                  <span className="qty-field-label">Tanggal</span>
                  <input
                    type="date"
                    value={s.work_date || ""}
                    onChange={(e) => updateRow(i, { work_date: e.target.value })}
                    data-testid={`split-work-date-input-${i}`}
                    title="Tanggal Pengerjaan"
                  />
                </label>
                <button className="ghost qty-assign-remove" onClick={() => removeRow(i)} aria-label="Hapus baris" data-testid={`split-remove-${i}`}>
                  <Trash2 size={14} />
                </button>
              </div>
            );
          })}
          {err && <div className="error" data-testid="split-error">{err}</div>}
          <div className="split-actions qty-ops-foot" style={{ borderTop: splits.length ? undefined : "none", marginTop: splits.length ? undefined : 0, paddingTop: splits.length ? undefined : 0 }}>
            <button className="ghost" onClick={addRow} disabled={printLines.length === 0 || scheduleLocked} data-testid="split-add-row">
              <Plus size={14} /> Tambah operator
            </button>
            <button className="primary" onClick={save} disabled={busy || printLines.length === 0 || scheduleLocked} data-testid="split-save">
              Simpan pembagian
            </button>
          </div>
        </div>
      )}
      {showProgress && err && !showSchedule && <div className="error" data-testid="split-error">{err}</div>}
      {footerActions}
      <ConfirmActionModal
        open={!!confirm}
        stage="Operator"
        qty={confirm?.qty}
        name={user?.name || user?.email}
        time={confirm?.time}
        busy={busy}
        error={err}
        onCancel={() => { if (!busy) setConfirm(null); }}
        onConfirm={executeQty}
      />
      </div>
    </section>
  );
}
