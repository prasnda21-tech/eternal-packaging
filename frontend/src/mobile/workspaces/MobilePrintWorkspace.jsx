import { useRef, useState } from "react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { newActionId } from "../../lib/workflowEntries";
import { getWorkflowReady } from "../../lib/stageState";
import { operatorLineModels, operatorMyAssignmentModels, pickSubmitSplit } from "../../lib/workPanel";
import { mobileWorkStatus } from "../../lib/mobileWorkspaces";
import { invalidateProductionSummaryCache } from "../../lib/productionSummaryCache";
import { useAuth } from "../../lib/auth";
import MobileDesignFile from "../components/MobileDesignFile";
import MobileMetricStrip from "../components/MobileMetricStrip";
import MobileQtyStepper from "../components/MobileQtyStepper";
import MobileStatusBadge from "../components/MobileStatusBadge";

export default function MobilePrintWorkspace({ order, canEdit, onUpdated }) {
  const { user, hasPerm } = useAuth();
  const mine = operatorMyAssignmentModels(order, user);
  const display = mine.length ? mine : (hasPerm("schedule.edit") ? operatorLineModels(order) : []);
  const target = display.reduce((s, l) => s + l.target, 0);
  const done = display.reduce((s, l) => s + l.done, 0);
  const remain = Math.max(0, target - done);
  const status = mobileWorkStatus(done, target);
  const ready = getWorkflowReady(order);
  const locked = ready.print_qty !== true || !canEdit;
  const [drafts, setDrafts] = useState({});
  const [workDates, setWorkDates] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const actionIds = useRef({});
  const pct = target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0;

  const saveLine = async (line) => {
    const split = pickSubmitSplit(line, user, hasPerm);
    const qty = parseInt(drafts[line.itemId], 10);
    if (!split) {
      setErr("Tidak ada alokasi cetak untuk akun ini.");
      return;
    }
    const workDate = String(workDates[line.itemId] || "").trim();
    if (!qty) {
      setErr("Qty cetak tidak valid.");
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(workDate)) {
      setErr("Tanggal pekerjaan wajib diisi.");
      return;
    }
    setBusy(true);
    setErr("");
    const actionId = actionIds.current[line.itemId] || newActionId();
    actionIds.current[line.itemId] = actionId;
    try {
      const res = await api.updateOperatorProgress(order.id, split.id, qty, {
        qty,
        action_id: actionId,
        work_date: workDate,
        view: "operational",
      });
      delete actionIds.current[line.itemId];
      invalidateProductionSummaryCache();
      onUpdated(res.order);
      setDrafts((d) => ({ ...d, [line.itemId]: "" }));
    } catch (e) {
      setErr(formatApiError(e, "Gagal menyimpan hasil cetak."));
    } finally {
      setBusy(false);
    }
  };

  if (!display.length) {
    return (
      <section data-testid="mobile-ws-print" className="mop-ws-role mop-ws-role--print">
        <div className="mop-ws-header"><h2>CETAK</h2></div>
        <MobileDesignFile order={order} />
        <p className="mop-empty" data-testid="mobile-print-empty">Tidak ada alokasi cetak untuk akun ini.</p>
      </section>
    );
  }

  const gateReason = order?.print_qty_gate_reason || "Input hasil cetak belum tersedia.";

  return (
    <section data-testid="mobile-ws-print" className="mop-ws-role mop-ws-role--print">
      <div className="mop-ws-header"><h2>CETAK</h2></div>
      <MobileStatusBadge status={status} />
      <MobileDesignFile order={order} />
      <MobileMetricStrip items={[
        { label: "Target Cetak", value: target.toLocaleString("id-ID") },
        { label: "Sudah Cetak", value: done.toLocaleString("id-ID") },
        { label: "Sisa", value: remain.toLocaleString("id-ID") },
      ]} />
      <div className="mop-progress" aria-label="Progress cetak"><span style={{ width: `${pct}%` }} /></div>
      {locked && <p className="mop-card-meta" data-testid="mobile-print-gate">{gateReason}</p>}
      {err && <div className="mop-error">{err}</div>}
      {display.map((line) => (
        <div className="mop-line-block" key={`${line.lineIdx}-${line.itemId}`} data-testid={`mobile-print-line-${line.lineIdx}`}>
          <strong>{line.name}</strong>
          {line.color ? <div className="mop-card-meta">{line.color}</div> : null}
          <p className="mop-card-meta">
            <span>Line {line.lineIdx + 1}</span>
            <span>{line.done.toLocaleString("id-ID")} / {line.target.toLocaleString("id-ID")}</span>
          </p>
          <h3 className="mop-card-title" style={{ fontSize: 13 }}>Tambah Hasil Cetak</h3>
          <label className="mop-card-meta">Tanggal pekerjaan
            <input type="date" value={workDates[line.itemId] || ""} onChange={(e) => setWorkDates((d) => ({ ...d, [line.itemId]: e.target.value }))} data-testid={`mobile-print-date-${line.lineIdx}`} disabled={locked || busy} />
          </label>
          <MobileQtyStepper
            id={`print-${line.itemId}`}
            label="Qty"
            value={drafts[line.itemId] || ""}
            onChange={(v) => setDrafts((d) => ({ ...d, [line.itemId]: v }))}
            disabled={locked || busy}
            max={line.remaining}
          />
          <button type="button" className="mop-btn-primary" disabled={locked || busy} onClick={() => saveLine(line)}>
            Simpan Hasil Cetak
          </button>
        </div>
      ))}
    </section>
  );
}
