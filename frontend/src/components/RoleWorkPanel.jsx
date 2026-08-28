import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { formatDisplayDateTime } from "../lib/constants";
import { getWorkflowReady } from "../lib/stageState";
import { newActionId, sumEntryQty } from "../lib/workflowEntries";
import api from "../lib/api";
import { fmtQty, MetricCell, MetricRow } from "./qtyUi";
import ConfirmActionModal from "./ConfirmActionModal";
import RoleWorkHistory from "./RoleWorkHistory";
import RoleWorkEntryForm from "./RoleWorkEntryForm";
import SuratJalanPanel from "./SuratJalanPanel";
import LoadingPanel from "./LoadingPanel";
import DeliveryProofPanel from "./DeliveryProofPanel";
import SuratJalanViewButton from "./SuratJalanViewButton";
import AttachmentsPanel from "./AttachmentsPanel";
import OperatorSplitPanel from "./OperatorSplitPanel";
import {
  canSubmitDivision,
  fallbackDivision,
  helperWorkModel,
  operatorLineModels,
  operatorMyAssignmentModels,
  operatorStatusLabel,
  orderPrintDone,
  orderPrintTarget,
  packingWorkModel,
  pickSubmitSplit,
  screenerWorkModel,
  terkirimEvidence,
} from "../lib/workPanel";

function PoDetailLink({ order }) {
  const { hasPerm } = useAuth();
  if (!hasPerm("po.view")) return null;
  return <Link className="ghost sm" to={`/po/${order.id}`} data-testid="work-open-detail">Detail PO</Link>;
}

function InactivePanel({ order, division, reason }) {
  const { hasPerm } = useAuth();
  return (
    <section className="panel work-panel" data-testid="role-work-panel">
      <p className="muted">{reason || `Form ${division?.label || ""} belum diaktifkan sebagai Work Panel.`}</p>
      {hasPerm("po.view") && <Link to={`/po/${order.id}`}>Buka Detail PO</Link>}
    </section>
  );
}

function WorkHero({ order, title, itemLabel, testid, compactHeader }) {
  if (!compactHeader) {
    return <h3 data-testid={testid}>{title}</h3>;
  }
  return (
    <header className="work-panel-hero" data-testid={testid}>
      <div>
        <p className="eyebrow">{title}</p>
        <h1 data-testid="work-po-number">{order.po_number}</h1>
        <p className="subhead">
          Customer: {order.customer_name || order.customer || "—"}
          {" · "}
          Item: {itemLabel}
        </p>
      </div>
      <PoDetailLink order={order} />
    </header>
  );
}

function OperatorWorkPanel({ order, division, onUpdated, compactHeader }) {
  const { user, hasPerm } = useAuth();
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(null);

  const personal = !hasPerm("schedule.edit");
  const lines = useMemo(
    () => (personal ? operatorMyAssignmentModels(order, user) : operatorLineModels(order, division?.target_source || "print_line_qty")),
    [order, division?.target_source, personal, user]
  );
  const headerTarget = personal ? lines.reduce((s, l) => s + Number(l.target || 0), 0) : orderPrintTarget(order);
  const headerDone = personal ? lines.reduce((s, l) => s + Number(l.done || 0), 0) : orderPrintDone(order);
  const headerRemain = Math.max(0, headerTarget - headerDone);
  const status = operatorStatusLabel(headerDone, headerTarget);
  const itemLabel = lines.length === 1 ? lines[0].name : (lines.length ? `${lines.length} item` : "—");
  const ready = getWorkflowReady(order);
  const gateLocked = ready.print_qty !== true;
  const gateMsg = order?.print_qty_gate_reason
    || (!order?.sample_approved
      ? "Cetak Sample harus disetujui sebelum Qty Selesai Cetak dapat diisi."
      : (order?.screener_gate_reason || "Qty Screener harus diisi sebelum pekerjaan Operator."));
  const canSubmit = canSubmitDivision(hasPerm, division) && !gateLocked;
  const inputLabel = division?.input_label || "Qty Cetak";

  const openConfirm = (line) => {
    const qty = Math.max(0, Number(drafts[line.lineIdx] || 0));
    if (!qty) {
      setErr("Qty wajib diisi.");
      return;
    }
    if (qty > Number(line.remaining || 0)) {
      setErr(`Qty ${qty.toLocaleString("id-ID")} melebihi sisa ${Number(line.remaining || 0).toLocaleString("id-ID")}.`);
      return;
    }
    const split = pickSubmitSplit(line, user, hasPerm);
    if (!split) {
      setErr("Tidak ada assignment Operator untuk item ini.");
      return;
    }
    setErr("");
    setConfirm({
      line,
      split,
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
      const res = await api.updateOperatorProgress(order.id, confirm.split.id, confirm.qty, {
        qty: confirm.qty,
        action_id: confirm.actionId,
      });
      onUpdated?.(res.order);
      setDrafts((prev) => ({ ...prev, [confirm.line.lineIdx]: "" }));
      setConfirm(null);
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal menyimpan"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel work-panel" data-testid="role-work-panel">
      {compactHeader && (
        <header className="work-panel-hero" data-testid="operator-work-header">
          <div>
            <p className="eyebrow">Pekerjaan Operator</p>
            <h1 data-testid="work-po-number">{order.po_number}</h1>
            <p className="subhead">
              Customer: {order.customer_name || order.customer || "—"}
              {" · "}
              Item: {itemLabel}
            </p>
          </div>
          <PoDetailLink order={order} />
        </header>
      )}
      {!compactHeader && (
        <h3 data-testid="operator-work-title">Pekerjaan Operator</h3>
      )}

      <MetricRow columns={4} testid="operator-work-metrics">
        <MetricCell label="Target Cetak" value={headerTarget} testid="work-target" />
        <MetricCell label="Sudah Dikerjakan" value={headerDone} testid="work-done" />
        <MetricCell label="Sisa" value={headerRemain} testid="work-remain" />
        <MetricCell label="Status" value={status} format="text" className="is-status" testid="work-status" />
      </MetricRow>

      {gateLocked && <p className="muted small" data-testid="operator-work-gate">{gateMsg}</p>}
      {err && <div className="error" data-testid="operator-work-error">{err}</div>}

      {lines.length === 0 && (
        <p className="muted" data-testid="operator-work-empty">Tidak ada item cetak pada PO ini.</p>
      )}

      {lines.map((line) => {
        const split = pickSubmitSplit(line, user, hasPerm);
        const lineCanEdit = canSubmit && !!split;
        const total = line.entries.length ? sumEntryQty(line.entries) : line.done;
        return (
          <div key={line.itemId} className="work-line" data-testid={`operator-work-line-${line.lineIdx}`}>
            <div className="work-line-head">
              <strong>{line.name}{line.color ? ` · ${line.color}` : ""}</strong>
              <span className="muted small">
                Target {fmtQty(line.target)} · Selesai {fmtQty(line.done)} · Sisa {fmtQty(line.remaining)}
              </span>
            </div>
            <RoleWorkHistory entries={line.entries} unit={line.unit} testid={`work-history-${line.lineIdx}`} />
            <div className="work-line-total muted small">
              TOTAL: <strong className="mono">{fmtQty(total)}</strong> {line.unit}
              {" · "}SISA: <strong className="mono">{fmtQty(line.remaining)}</strong>
            </div>
            <RoleWorkEntryForm
              label={inputLabel}
              unit={line.unit}
              draft={drafts[line.lineIdx] ?? ""}
              onDraftChange={(v) => setDrafts((p) => ({ ...p, [line.lineIdx]: v }))}
              onConfirmClick={() => openConfirm(line)}
              canEdit={lineCanEdit}
              busy={busy}
              remaining={line.remaining}
              helperText={division?.helper_text}
              testid={`work-entry-${line.lineIdx}`}
            />
            {canSubmit && !split && line.splits.length === 0 && (
              <p className="muted small">Belum ada operator ditugaskan untuk item ini.</p>
            )}
          </div>
        );
      })}

      <ConfirmActionModal
        open={!!confirm}
        title="KONFIRMASI PEKERJAAN"
        stage="Operator"
        poNumber={order.po_number}
        customer={order.customer_name || order.customer}
        item={confirm?.line?.name}
        qty={confirm?.qty}
        name={user?.name || user?.email}
        time={confirm?.time}
        busy={busy}
        error={err}
        onCancel={() => { if (!busy) { setConfirm(null); } }}
        onConfirm={execute}
      />
    </section>
  );
}

function qtyModelFor(divisionId, order) {
  if (divisionId === "screener") return screenerWorkModel(order);
  if (divisionId === "helper") return helperWorkModel(order);
  if (divisionId === "packing") return packingWorkModel(order);
  return null;
}

async function submitQtyDivision(divisionId, orderId, confirm) {
  if (divisionId === "screener") {
    return api.updateScreenerInfo(orderId, { qty: confirm.qty, action_id: confirm.actionId });
  }
  if (divisionId === "helper") {
    return api.updateHelperPrepItems(orderId, [{
      line_idx: confirm.line.lineIdx,
      qty: confirm.qty,
      action_id: confirm.actionId,
    }]);
  }
  if (divisionId === "packing") {
    return api.savePackingInfo(orderId, [{
      line_idx: confirm.line.lineIdx,
      qty: confirm.qty,
      action_id: confirm.actionId,
    }]);
  }
  throw new Error("Divisi tidak didukung");
}

function QtyDivisionWorkPanel({ order, division, onUpdated, compactHeader }) {
  const { user, hasPerm } = useAuth();
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(null);
  const model = useMemo(() => qtyModelFor(division.id, order), [division.id, order]);
  const inputLabel = division.input_label || "Qty";
  const canSubmit = canSubmitDivision(hasPerm, division) && !model.gateLocked;

  const openConfirm = (line) => {
    const qty = Math.max(0, Number(drafts[line.lineIdx] || 0));
    if (!qty) {
      setErr("Qty wajib diisi.");
      return;
    }
    if (line.capRemaining !== false && qty > Number(line.remaining || 0)) {
      setErr(`Qty ${qty.toLocaleString("id-ID")} melebihi sisa ${Number(line.remaining || 0).toLocaleString("id-ID")}.`);
      return;
    }
    setErr("");
    setConfirm({
      line,
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
      const res = await submitQtyDivision(division.id, order.id, confirm);
      onUpdated?.(res.order);
      setDrafts((prev) => ({ ...prev, [confirm.line.lineIdx]: "" }));
      setConfirm(null);
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal menyimpan"));
    } finally {
      setBusy(false);
    }
  };

  if (!model) return null;

  return (
    <section className="panel work-panel" data-testid="role-work-panel">
      <WorkHero
        order={order}
        title={model.title}
        itemLabel={model.itemLabel}
        testid={`${division.id}-work-header`}
        compactHeader={compactHeader}
      />
      <MetricRow columns={model.hideRemain ? 3 : 4} testid={`${division.id}-work-metrics`}>
        <MetricCell label={model.metricLabels?.target || "Target"} value={model.headerTarget} testid="work-target" />
        <MetricCell label={model.metricLabels?.done || "Sudah Dikerjakan"} value={model.headerDone} testid="work-done" />
        {!model.hideRemain && (
          <MetricCell label={model.metricLabels?.remain || "Sisa"} value={model.headerRemain} testid="work-remain" />
        )}
        <MetricCell label="Status" value={model.status} format="text" className="is-status" testid="work-status" />
      </MetricRow>
      {model.overPrepared > 0 && (
        <p className="muted small" data-testid="helper-over-prepared-note">
          Kelebihan persiapan: {fmtQty(model.overPrepared)}
        </p>
      )}
      {model.gateLocked && <p className="muted small" data-testid={`${division.id}-work-gate`}>{model.gateMsg}</p>}
      {err && <div className="error" data-testid={`${division.id}-work-error`}>{err}</div>}
      {model.lines.length === 0 && (
        <p className="muted" data-testid={`${division.id}-work-empty`}>Tidak ada item untuk pekerjaan ini.</p>
      )}
      {model.lines.map((line) => {
        const total = line.entries.length ? sumEntryQty(line.entries) : line.done;
        const remainingProp = line.capRemaining === false ? undefined : line.remaining;
        return (
          <div key={line.itemId} className="work-line" data-testid={`${division.id}-work-line-${line.lineIdx}`}>
            <div className="work-line-head">
              <strong>{line.name}{line.color ? ` · ${line.color}` : ""}</strong>
              <span className="muted small">
                Target {fmtQty(line.target)} · Selesai {fmtQty(line.done)}
                {!model.hideRemain ? ` · Sisa ${fmtQty(line.remaining)}` : ""}
              </span>
            </div>
            <RoleWorkHistory entries={line.entries} unit={line.unit} testid={`work-history-${line.lineIdx}`} />
            <div className="work-line-total muted small">
              TOTAL: <strong className="mono">{fmtQty(total)}</strong> {line.unit}
              {!model.hideRemain ? (
                <>
                  {" · "}SISA: <strong className="mono">{fmtQty(line.remaining)}</strong>
                </>
              ) : null}
            </div>
            <RoleWorkEntryForm
              label={inputLabel}
              unit={line.unit}
              draft={drafts[line.lineIdx] ?? ""}
              onDraftChange={(v) => setDrafts((p) => ({ ...p, [line.lineIdx]: v }))}
              onConfirmClick={() => openConfirm(line)}
              canEdit={canSubmit}
              busy={busy}
              remaining={remainingProp}
              helperText={division?.helper_text}
              testid={`work-entry-${line.lineIdx}`}
            />
          </div>
        );
      })}
      <ConfirmActionModal
        open={!!confirm}
        title="KONFIRMASI PEKERJAAN"
        stage={model.stage}
        poNumber={order.po_number}
        customer={order.customer_name || order.customer}
        item={confirm?.line?.name}
        qty={confirm?.qty}
        name={user?.name || user?.email}
        time={confirm?.time}
        busy={busy}
        error={err}
        onCancel={() => { if (!busy) setConfirm(null); }}
        onConfirm={execute}
      />
    </section>
  );
}

function DesignerWorkPanel({ order, division, onUpdated, compactHeader }) {
  const { user, hasPerm } = useAuth();
  const itemLabel = (order?.line_items || []).length === 1
    ? (order.line_items[0].name || "—")
    : `${(order?.line_items || []).length} item`;
  const revisi = (order?.customer_approval?.status || "").toLowerCase() === "rejected";
  const canSubmit = canSubmitDivision(hasPerm, division);
  return (
    <section className="panel work-panel" data-testid="role-work-panel">
      <WorkHero
        order={order}
        title="Pekerjaan Designer"
        itemLabel={itemLabel}
        testid="designer-work-header"
        compactHeader={compactHeader}
      />
      {revisi && (
        <p className="muted small" data-testid="designer-revisi-note">
          Customer meminta Revisi. Upload design baru.
        </p>
      )}
      <AttachmentsPanel
        order={order}
        user={user}
        onUpdated={(next) => onUpdated?.(next?.order || next)}
        section="design"
        confirmUpload={canSubmit}
      />
    </section>
  );
}

function KepalaProduksiWorkPanel({ order, division, onUpdated, compactHeader, focusLineIdx }) {
  const { hasPerm } = useAuth();
  const items = order?.line_items || [];
  const idx = focusLineIdx == null || Number.isNaN(Number(focusLineIdx)) ? null : Number(focusLineIdx);
  const line = idx != null && items[idx] ? items[idx] : null;
  const itemLabel = line?.name
    || (items.length === 1 ? (items[0].name || "—") : `${items.length} item`);
  const canEdit = canSubmitDivision(hasPerm, division);
  return (
    <section className="panel work-panel" data-testid="role-work-panel">
      <WorkHero
        order={order}
        title="Pekerjaan Kepala Produksi"
        itemLabel={itemLabel}
        testid="kepala_produksi-work-header"
        compactHeader={compactHeader}
      />
      <OperatorSplitPanel
        order={order}
        canEdit={canEdit}
        onUpdated={onUpdated}
        mode="schedule"
        focusLineIdx={idx}
      />
    </section>
  );
}

function SuratJalanWorkPanel({ order, division, onUpdated, compactHeader }) {
  const { hasPerm } = useAuth();
  const canManage = canSubmitDivision(hasPerm, division);

  return (
    <section className="panel work-panel" data-testid="role-work-panel">
      <WorkHero
        order={order}
        title="Pekerjaan Surat Jalan"
        itemLabel={(order?.line_items || []).length === 1
          ? (order.line_items[0].name || "—")
          : `${(order?.line_items || []).length} item`}
        testid="surat_jalan-work-header"
        compactHeader={compactHeader}
      />
      <SuratJalanPanel
        order={order}
        canManage={canManage}
        canViewFile={hasPerm("delivery_note.view_file")}
        onUpdated={onUpdated}
      />
    </section>
  );
}

function LoadingWorkPanel({ order, division, onUpdated, compactHeader }) {
  const { hasPerm } = useAuth();
  const canManage = canSubmitDivision(hasPerm, division);
  return (
    <section className="panel work-panel" data-testid="role-work-panel">
      <WorkHero
        order={order}
        title="Pekerjaan Loading"
        itemLabel={(order?.line_items || []).length === 1
          ? (order.line_items[0].name || "—")
          : `${(order?.line_items || []).length} item`}
        testid="loading-work-header"
        compactHeader={compactHeader}
      />
      <LoadingPanel order={order} canManage={canManage} onUpdated={onUpdated} />
    </section>
  );
}

function TerkirimWorkPanel({ order, onUpdated, compactHeader }) {
  const evidence = terkirimEvidence(order);
  const delivered = Number(order?.qty_delivered || order?.delivered_qty || 0);
  const eligible = Number(order?.eligible_delivery_qty);
  const target = Number.isFinite(eligible)
    ? eligible
    : Number(order?.packing_required_total || order?.total_quantity || 0);
  const itemLabel = (order?.line_items || []).length === 1
    ? (order.line_items[0].name || "—")
    : `${(order?.line_items || []).length} item`;
  return (
    <section className="panel work-panel" data-testid="role-work-panel">
      <WorkHero
        order={order}
        title="Pekerjaan Terkirim"
        itemLabel={itemLabel}
        testid="terkirim-work-header"
        compactHeader={compactHeader}
      />
      <MetricRow columns={4} testid="terkirim-work-metrics">
        <MetricCell label="Target" value={target} testid="work-target" />
        <MetricCell label="Sudah Dikerjakan" value={delivered} testid="work-done" />
        <MetricCell label="Sisa" value={Math.max(0, target - delivered)} testid="work-remain" />
        <MetricCell
          label="Status"
          value={order.delivery_status || operatorStatusLabel(delivered, target)}
          format="text"
          className="is-status"
          testid="work-status"
        />
      </MetricRow>
      <RoleWorkHistory entries={evidence} mode="evidence" testid="work-history-delivery" />
      <DeliveryProofPanel order={order} onUpdated={onUpdated} />
      <div style={{ padding: "0 16px 12px" }}>
        <SuratJalanViewButton order={order} testid="terkirim-sj-lihat" />
      </div>
    </section>
  );
}

export default function RoleWorkPanel({
  order,
  division,
  onUpdated,
  compactHeader = true,
  focusLineIdx = null,
}) {
  if (!order) return null;
  const resolved = division || fallbackDivision("operator");
  if (resolved.implemented === false) {
    return <InactivePanel order={order} division={resolved} />;
  }
  if (resolved.active === false) {
    return (
      <InactivePanel
        order={order}
        division={resolved}
        reason={`Form ${resolved.label} sedang nonaktif di Pengaturan.`}
      />
    );
  }
  if (resolved.id === "designer") {
    return (
      <DesignerWorkPanel
        order={order}
        division={resolved}
        onUpdated={onUpdated}
        compactHeader={compactHeader}
      />
    );
  }
  if (resolved.id === "kepala_produksi") {
    return (
      <KepalaProduksiWorkPanel
        order={order}
        division={resolved}
        onUpdated={onUpdated}
        compactHeader={compactHeader}
        focusLineIdx={focusLineIdx}
      />
    );
  }
  if (resolved.id === "operator") {
    return (
      <OperatorWorkPanel
        order={order}
        division={resolved}
        onUpdated={onUpdated}
        compactHeader={compactHeader}
      />
    );
  }
  if (resolved.id === "surat_jalan") {
    return (
      <SuratJalanWorkPanel
        order={order}
        division={resolved}
        onUpdated={onUpdated}
        compactHeader={compactHeader}
      />
    );
  }
  if (resolved.id === "loading") {
    return (
      <LoadingWorkPanel
        order={order}
        division={resolved}
        onUpdated={onUpdated}
        compactHeader={compactHeader}
      />
    );
  }
  if (resolved.id === "terkirim") {
    return (
      <TerkirimWorkPanel
        order={order}
        onUpdated={onUpdated}
        compactHeader={compactHeader}
      />
    );
  }
  return (
    <QtyDivisionWorkPanel
      order={order}
      division={resolved}
      onUpdated={onUpdated}
      compactHeader={compactHeader}
    />
  );
}
