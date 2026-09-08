import { useState } from "react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { packingLinesForOrder } from "../../lib/packingCard";
import { newActionId } from "../../lib/workflowEntries";
import { mobileWorkStatus, packingMetrics } from "../../lib/mobileWorkspaces";
import MobileMetricStrip from "../components/MobileMetricStrip";
import MobileQtyStepper from "../components/MobileQtyStepper";
import MobileStatusBadge from "../components/MobileStatusBadge";

export default function MobilePackingWorkspace({ order, canEdit, onUpdated }) {
  const rows = packingLinesForOrder(order);
  const metrics = packingMetrics(order, rows);
  const status = mobileWorkStatus(metrics.packed, metrics.target);
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const saveLine = async (row) => {
    const qty = parseInt(drafts[row.line_idx], 10);
    if (!qty) {
      setErr("Jumlah packing tidak valid.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await api.savePackingInfo(order.id, [{
        line_idx: row.line_idx,
        qty,
        action_id: newActionId(),
      }]);
      onUpdated(res.order);
      setDrafts((d) => ({ ...d, [row.line_idx]: "" }));
    } catch (e) {
      setErr(formatApiError(e, "Jumlah packing tidak valid."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="mobile-ws-packing">
      <div className="mop-ws-header"><h2>PACKING</h2></div>
      <MobileStatusBadge status={status} />
      <MobileMetricStrip
        testid="mobile-packing-metrics"
        items={[
          { label: "Target", value: metrics.target.toLocaleString("id-ID") },
          { label: "Sudah Dicetak", value: metrics.printed.toLocaleString("id-ID") },
          { label: "Sudah Dipacking", value: metrics.packed.toLocaleString("id-ID") },
          { label: "Sisa", value: metrics.remaining.toLocaleString("id-ID") },
        ]}
      />
      {err && <div className="mop-error">{err}</div>}
      {metrics.lines.map((row) => (
        <div className="mop-line-block" key={row.line_idx} data-testid={`mobile-pack-line-${row.line_idx}`}>
          <strong>{row.name}</strong>
          {row.color ? <div className="mop-card-meta">{row.color}</div> : null}
          <p className="mop-card-meta">
            <span>Line {row.line_idx + 1}</span>
            <span>Sisa {row.remaining.toLocaleString("id-ID")}</span>
          </p>
          <MobileQtyStepper
            id={`pack-${row.line_idx}`}
            label="Qty"
            value={drafts[row.line_idx] || ""}
            onChange={(v) => setDrafts((d) => ({ ...d, [row.line_idx]: v }))}
            disabled={!canEdit || busy}
            max={row.remaining}
          />
          <button type="button" className="mop-btn-primary" disabled={!canEdit || busy} onClick={() => saveLine(row)}>
            Simpan Packing
          </button>
        </div>
      ))}
    </section>
  );
}
