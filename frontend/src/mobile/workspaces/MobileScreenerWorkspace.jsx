import { useState } from "react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { newActionId } from "../../lib/workflowEntries";
import { screenerWorkModel } from "../../lib/workPanel";
import { getWorkflowReady } from "../../lib/stageState";
import { mobileWorkStatus } from "../../lib/mobileWorkspaces";
import MobileDesignFile from "../components/MobileDesignFile";
import MobileMetricStrip from "../components/MobileMetricStrip";
import MobileQtyStepper from "../components/MobileQtyStepper";
import MobileStatusBadge from "../components/MobileStatusBadge";

export default function MobileScreenerWorkspace({ order, canEdit, onUpdated }) {
  const model = screenerWorkModel(order);
  const ready = getWorkflowReady(order);
  const status = mobileWorkStatus(model.headerDone, model.headerTarget);
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const locked = ready.screener !== true || !canEdit;

  const save = async () => {
    const n = parseInt(qty, 10);
    if (!n) {
      setErr("Jumlah screener tidak valid.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await api.updateScreenerInfo(order.id, { qty: n, notes: "", action_id: newActionId() });
      onUpdated(res.order);
      setQty("");
    } catch (e) {
      setErr(formatApiError(e, "Gagal menyimpan screener."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="mobile-ws-screener" className="mop-ws-role mop-ws-role--screener">
      <div className="mop-ws-header"><h2>SCREENER</h2></div>
      <MobileStatusBadge status={status} />
      <MobileDesignFile order={order} />
      <MobileMetricStrip
        testid="mobile-screener-metrics"
        className="mop-metrics--2col"
        items={[
          { label: "Target", value: model.headerTarget.toLocaleString("id-ID") },
          { label: "Selesai", value: model.headerDone.toLocaleString("id-ID") },
        ]}
      />
      {locked && <p className="mop-card-meta">{model.gateMsg}</p>}
      {err && <div className="mop-error">{err}</div>}
      <MobileQtyStepper id="screener-qty" label="Qty Screener" value={qty} onChange={setQty} disabled={locked || busy} />
      <button type="button" className="mop-btn-primary" disabled={locked || busy} onClick={save}>Simpan Screener</button>
    </section>
  );
}
