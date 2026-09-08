import { useState } from "react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { newActionId } from "../../lib/workflowEntries";
import { helperWorkModel } from "../../lib/workPanel";
import { mobileWorkStatus } from "../../lib/mobileWorkspaces";
import MobileMetricStrip from "../components/MobileMetricStrip";
import MobileQtyStepper from "../components/MobileQtyStepper";
import MobileStatusBadge from "../components/MobileStatusBadge";

export default function MobileHelperPrepWorkspace({ order, canEdit, onUpdated }) {
  const model = helperWorkModel(order);
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const status = mobileWorkStatus(model.headerDone, model.headerTarget);
  const locked = model.gateLocked || !canEdit;

  const saveLine = async (line) => {
    const qty = parseInt(drafts[line.lineIdx], 10);
    if (!qty) {
      setErr("Jumlah persiapan tidak valid.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await api.updateHelperPrepItems(order.id, [{
        line_idx: line.lineIdx,
        qty,
        action_id: newActionId(),
      }]);
      onUpdated(res.order);
      setDrafts((d) => ({ ...d, [line.lineIdx]: "" }));
    } catch (e) {
      setErr(formatApiError(e, "Gagal menyimpan persiapan."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="mobile-ws-prep">
      <div className="mop-ws-header"><h2>PERSIAPAN CUP</h2></div>
      <MobileStatusBadge status={status} />
      <MobileMetricStrip items={[
        { label: "Target", value: model.headerTarget.toLocaleString("id-ID") },
        { label: "Disiapkan", value: model.headerDone.toLocaleString("id-ID") },
        { label: "Sisa", value: model.headerRemain.toLocaleString("id-ID") },
      ]} />
      {locked && <p className="mop-card-meta">{model.gateMsg}</p>}
      {err && <div className="mop-error">{err}</div>}
      {model.lines.map((line) => (
        <div className="mop-line-block" key={line.lineIdx} data-testid={`mobile-prep-line-${line.lineIdx}`}>
          <strong>{line.name}</strong>
          {line.color ? <div className="mop-card-meta">{line.color}</div> : null}
          <p className="mop-card-meta">
            <span>Target {line.target.toLocaleString("id-ID")}</span>
            <span>Sisa {line.remaining.toLocaleString("id-ID")}</span>
          </p>
          <MobileQtyStepper
            id={`prep-${line.lineIdx}`}
            label="Qty"
            value={drafts[line.lineIdx] || ""}
            onChange={(v) => setDrafts((d) => ({ ...d, [line.lineIdx]: v }))}
            disabled={locked || busy}
            max={line.remaining}
          />
          <button type="button" className="mop-btn-primary" disabled={locked || busy} onClick={() => saveLine(line)}>
            Simpan Persiapan
          </button>
        </div>
      ))}
    </section>
  );
}
