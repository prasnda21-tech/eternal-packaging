import { Link } from "react-router-dom";
import MobileStatusBadge from "./MobileStatusBadge";
import { mobileWorkStatus } from "../../lib/mobileWorkspaces";
import { formatIdQty } from "../../lib/divisionTasks";

export default function MobileTaskCard({ task, workspaceId }) {
  const status = mobileWorkStatus(task.completed_qty, task.target_qty);
  if (task.status === "selesai") status.key = "done";
  if (task.status === "sebagian") { status.key = "process"; status.label = "Proses"; }
  if (task.status === "belum" || task.status === "siap") { status.key = task.status === "siap" ? "wait" : "idle"; status.label = task.status_label || status.label; }
  return (
    <Link
      className="mop-card"
      to={`/m/orders/${encodeURIComponent(task.order_id)}?ws=${encodeURIComponent(workspaceId)}`}
      data-testid={`mobile-task-${task.order_number}`}
    >
      <div className="mop-card-kicker">{task.order_number}</div>
      <div className="mop-card-title">{task.customer_name || "—"}</div>
      <div className="mop-card-meta">
        <span>{task.item || "—"}</span>
        <span>{formatIdQty(task.target_qty)}</span>
      </div>
      <div className="mop-card-meta">
        <span>{task.work_date || task.deadline || ""}</span>
        <MobileStatusBadge status={{ key: status.key, label: task.status_label || status.label }} />
      </div>
    </Link>
  );
}
