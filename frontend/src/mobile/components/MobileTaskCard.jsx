import { Link } from "react-router-dom";
import MobileStatusBadge from "./MobileStatusBadge";
import { mobileWorkStatus } from "../../lib/mobileWorkspaces";
import { formatIdQty } from "../../lib/divisionTasks";
import {
  taskDeadlineLabel,
  taskOperatorNames,
  taskOperatorLabel,
  taskWorkDateLabel,
} from "../../lib/mobileTaskCard";

export default function MobileTaskCard({ task, workspaceId }) {
  const status = mobileWorkStatus(task.completed_qty, task.target_qty);
  if (task.status === "selesai") status.key = "done";
  if (task.status === "sebagian") { status.key = "process"; status.label = "Proses"; }
  if (task.status === "belum" || task.status === "siap") { status.key = task.status === "siap" ? "wait" : "idle"; status.label = task.status_label || status.label; }
  const operators = taskOperatorNames(task);
  return (
    <Link
      className="mop-card"
      to={`/m/orders/${encodeURIComponent(task.order_id)}?ws=${encodeURIComponent(workspaceId)}`}
      data-testid={`mobile-task-${task.task_id || task.order_number}`}
      data-line-idx={task.line_idx ?? ""}
    >
      <div className="mop-card-kicker">{task.order_number}</div>
      <div className="mop-card-title">{task.customer_name || "—"}</div>
      <div className="mop-card-sub">{task.item || "—"}</div>
      <div className="mop-task-facts">
        <div className="mop-task-fact" data-testid="mobile-task-operator">
          <span className="mop-task-fact-label">Operator</span>
          <span className="mop-task-fact-value">
            {operators.length
              ? operators.map((name) => <span key={name} className="mop-task-op-line">{name}</span>)
              : taskOperatorLabel(task)}
          </span>
        </div>
        <div className="mop-task-fact" data-testid="mobile-task-work-date">
          <span className="mop-task-fact-label">Tgl Pengerjaan</span>
          <span className="mop-task-fact-value">{taskWorkDateLabel(task)}</span>
        </div>
        <div className="mop-task-fact" data-testid="mobile-task-deadline">
          <span className="mop-task-fact-label">Deadline</span>
          <span className="mop-task-fact-value">{taskDeadlineLabel(task)}</span>
        </div>
      </div>
      <div className="mop-card-meta">
        <span>{formatIdQty(task.target_qty)}</span>
        <MobileStatusBadge status={{ key: status.key, label: task.status_label || status.label }} />
      </div>
    </Link>
  );
}
