import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2, Info } from "lucide-react";
import api from "../lib/api";
import { STAGES } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { getWorkflowReady } from "../lib/stageState";

const isPrintLine = (li) => (li?.production_process || "print").toLowerCase() !== "non_print";

export default function OperatorSplitPanel({ order, canEdit, onUpdated, footerActions = null, mode = "all" }) {
  const { user, hasPerm } = useAuth();
  const [operators, setOperators] = useState([]);
  const [splits, setSplits] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [qtyDrafts, setQtyDrafts] = useState({});

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
  const helperPrepared = order?.helper_prepared_print_qty;
  const helperCapSet = helperPrepared !== null && helperPrepared !== undefined;
  const assignmentCap = typeof order?.operator_assignment_cap === "number"
    ? order.operator_assignment_cap
    : (helperCapSet ? Math.min(printQtyTotal, Number(helperPrepared)) : printQtyTotal);
  const remaining = assignmentCap - totalAssigned;
  const ready = getWorkflowReady(order);
  const scheduleLocked = mode !== "progress" && !ready.penjadwalan;
  const progressLocked = mode !== "schedule" && !ready.operator;
  const screenerGateMsg = order?.screener_gate_reason || "Qty Screener harus diisi sebelum pekerjaan Operator.";
  const showSchedule = mode === "all" || mode === "schedule";
  const showProgress = mode === "all" || mode === "progress";

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
      setErr(
        helperCapSet
          ? `Total pembagian ${totalAssigned} melebihi qty Helper Persiapan (${assignmentCap}).`
          : `Total pembagian ${totalAssigned} melebihi total qty Cetak PO (${printQtyTotal}).`
      );
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
      setErr(screenerGateMsg);
      return;
    }
    const raw = qtyDrafts[op.id];
    const qty = Math.max(0, Math.min(Number(raw ?? op.qty_completed ?? 0), op.quantity));
    setErr("");
    setBusy(true);
    try {
      const res = await api.updateOperatorProgress(order.id, op.id, qty);
      onUpdated(res.order);
      setQtyDrafts((prev) => ({ ...prev, [op.id]: qty }));
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal memperbarui qty"));
    } finally {
      setBusy(false);
    }
  };

  // Full non-print → Pembagian Operator TIDAK DIPERLUKAN.
  if (!requiresPrinting) {
    return (
      <section className="panel" data-testid={mode === "progress" ? "operator-progress-panel" : "operator-split-panel"}>
        <header className="panel-head">
          <h3>{mode === "progress" ? "Operator" : "Pembagian Operator"}</h3>
          <span className="pill sm" data-testid="operator-not-required-badge" style={{ background: "#efe6d3", color: "#7a6f52" }}>
            Tidak Diperlukan
          </span>
        </header>
        <p className="muted small" data-testid="operator-not-required-note">
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
    <section className="panel" data-testid={mode === "progress" ? "operator-progress-panel" : "operator-split-panel"}>
      <header className="panel-head">
        <h3>{mode === "progress" ? "Operator" : "Pembagian Operator"}</h3>
        <span className="pill" data-testid="operator-scope-badge">
          Scope Cetak: {printQtyTotal.toLocaleString("id-ID")} pcs
        </span>
        {helperCapSet && (
          <span className="pill sm" data-testid="operator-helper-cap-badge" style={{ background: "#efe6d3", color: "#7a6f52" }}>
            Cap Helper: {Number(assignmentCap).toLocaleString("id-ID")} pcs
          </span>
        )}
      </header>

      {/* Info baris tersedia untuk assignment */}
      {printLines.length > 0 && (
        <div className="muted small" data-testid="operator-print-lines-info" style={{ marginBottom: 8 }}>
          Barang Cetak: {printLines.map(({ li, idx }) => (
            <span key={idx} className="pill sm" style={{ background: "#e6ecf3", color: "#3a4b60", marginRight: 6 }}>
              {li.name} · {Number(li.quantity || 0).toLocaleString("id-ID")}
              {perLineAssigned[idx] ? ` · assigned ${perLineAssigned[idx].toLocaleString("id-ID")}` : ""}
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
        <p className="muted small" data-testid="operator-screener-gate-note">{screenerGateMsg}</p>
      )}

      {/* Current splits with per-operator progress */}
      {showProgress && (
      <div className="split-list">
        {(order.operator_splits || []).length === 0 && (
          <p className="empty">Belum ada operator ditugaskan.</p>
        )}
        {(order.operator_splits || []).map((op) => {
          const isSelf = user?.email === op.operator_email;
          const canEditQty = hasPerm("operator.update") && (isSelf || hasPerm("schedule.edit"));
          const draft = qtyDrafts[op.id] ?? op.qty_completed ?? 0;
          const remaining = Math.max(0, op.quantity - (op.qty_completed || 0));
          const pct = op.quantity ? Math.round(((op.qty_completed || 0) / op.quantity) * 100) : 0;
          const opLineLabel = (op.line_idx === null || op.line_idx === undefined)
            ? "Barang: (legacy)"
            : `Barang: ${lineName(op.line_idx)}`;
          return (
            <div key={op.id} className="split-row" data-testid={`operator-progress-${op.operator_email}`}>
              {/* Row 1: identitas operator + stage chip di sisi kanan */}
              <div className="op-row-head">
                <div className="op-ident">
                  <strong>{op.operator_name}</strong>
                  <span className="mono">{op.operator_email}</span>
                </div>
                <div className="stage-chip">{STAGES[op.stage]}</div>
              </div>

              {/* Row 2: barang yang dikerjakan */}
              <div className="op-row-item" data-testid={`operator-line-${op.operator_email}`}>
                {opLineLabel}
              </div>

              {/* Row 3: target / selesai / sisa + progress bar */}
              <div className="qty">
                <span>Target {op.quantity.toLocaleString("id-ID")}</span>
                <span className="mono status-ok">Selesai {(op.qty_completed || 0).toLocaleString("id-ID")}</span>
                <span className="mono status-wait">Sisa {remaining.toLocaleString("id-ID")}</span>
                <div className="pct-track sm" style={{ flex: 1, minWidth: 120 }}>
                  <div className="pct-fill" style={{ width: `${pct}%` }} />
                </div>
              </div>

              {/* Row 4: input qty + Simpan */}
              {canEditQty && (
                <div className="split-actions-inline">
                    <div className="qty-inline">
                      <input
                        type="number"
                        min="0"
                        max={op.quantity}
                        value={draft}
                        onChange={(e) => setQtyDrafts({ ...qtyDrafts, [op.id]: e.target.value })}
                        data-testid={`qty-input-${op.operator_email}`}
                        disabled={progressLocked || busy}
                      />
                      <button
                        className="ghost sm"
                        onClick={() => updateQty(op)}
                        disabled={busy || progressLocked}
                        data-testid={`qty-save-${op.operator_email}`}
                        title="Simpan qty selesai"
                      >Simpan qty</button>
                    </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      )}

      {/* Card 2 — Atur Pembagian (planning). Terpisah visual dari card progress di atas. */}
      {canEdit && showSchedule && (
        <div className="atur-pembagian-card" data-testid="atur-pembagian-card" style={{
          marginTop: 14,
          padding: 14,
          background: "var(--paper)",
          border: "1px solid var(--line)",
          borderRadius: 6,
        }}>
          <div className="split-edit-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
            <h4 style={{ margin: 0 }} data-testid="atur-pembagian-title">Atur Pembagian</h4>
            <span className={remaining < 0 ? "warn" : "muted"}>
              Sisa cap: {remaining.toLocaleString("id-ID")} pcs
              {helperCapSet ? " (Helper Persiapan)" : " (scope Cetak)"}
            </span>
          </div>
          {splits.map((s, i) => {
            const lineQty = Number(lineItems[Number(s.line_idx)]?.quantity || 0);
            const lineAssigned = perLineAssigned[Number(s.line_idx)] || 0;
            const overLine = s.line_idx !== "" && lineAssigned > lineQty;
            return (
              <div key={i} className="split-edit-row" style={{ gridTemplateColumns: "1.4fr 1.4fr 90px 150px 40px" }}>
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
                <input
                  type="number"
                  min="1"
                  value={s.quantity}
                  onChange={(e) => updateRow(i, { quantity: e.target.value })}
                  placeholder="Qty"
                  data-testid={`split-quantity-input-${i}`}
                  style={overLine ? { borderColor: "#8f1f1f" } : undefined}
                  title={overLine ? `Total ${lineAssigned} melebihi qty line ${lineQty}` : undefined}
                />
                <input
                  type="date"
                  value={s.work_date || ""}
                  onChange={(e) => updateRow(i, { work_date: e.target.value })}
                  data-testid={`split-work-date-input-${i}`}
                  title="Tanggal Pengerjaan"
                />
                <button className="ghost sm" onClick={() => removeRow(i)} aria-label="Hapus baris" data-testid={`split-remove-${i}`}>
                  <Trash2 size={14} />
                </button>
              </div>
            );
          })}
          {err && <div className="error" data-testid="split-error">{err}</div>}
          <div className="split-actions">
            <button className="ghost" onClick={addRow} disabled={printLines.length === 0 || scheduleLocked} data-testid="split-add-row">
              <Plus size={14} /> Tambah operator
            </button>
            <button className="primary" onClick={save} disabled={busy || printLines.length === 0 || scheduleLocked} data-testid="split-save">
              Simpan pembagian
            </button>
          </div>
        </div>
      )}
      {footerActions}
    </section>
  );
}
