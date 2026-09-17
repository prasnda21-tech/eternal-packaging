import { STATUS_PO_WORKFLOW_STAGES, workflowCellState } from "../lib/statusPoWorkflow";

export default function SalesPwaProgress({ order }) {
  return (
    <div className="spwa-progress" aria-hidden data-testid={`sales-progress-${order.id}`}>
      {STATUS_PO_WORKFLOW_STAGES.map((stage) => {
        const state = workflowCellState(order, stage.key);
        const cls = state === "completed" || state === "skipped" || state === "na" || state === "not_applicable"
          ? (state === "skipped" || state === "na" || state === "not_applicable" ? "skipped" : "done")
          : state === "active"
            ? "active"
            : state === "hold"
              ? "hold"
              : "";
        return <i key={stage.key} className={cls} title={`${stage.full}: ${state}`} />;
      })}
    </div>
  );
}
