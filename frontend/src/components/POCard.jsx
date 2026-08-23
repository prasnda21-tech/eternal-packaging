import { Link } from "react-router-dom";
import { Package, UserRound, ChevronRight } from "lucide-react";
import { STAGES, formatDeadline } from "../lib/constants";
import StageBar from "./StageBar";

export default function POCard({ order }) {
  const operators = order.operator_splits?.length
    ? order.operator_splits.map((s) => s.operator_name).join(" / ")
    : "Belum ditentukan";

  return (
    <Link
      to={`/po/${order.id}`}
      className="job-row"
      data-testid={`order-row-${order.po_number}`}
      aria-label={`Buka detail ${order.po_number}`}
    >
      <div className="job-top">
        <div>
          <span className="po" data-testid={`order-po-${order.po_number}`}>{order.po_number}</span>
          <span
            className={order.priority === "urgent" ? "urgent-badge" : "standard-badge"}
            data-testid={`order-priority-${order.po_number}`}
          >
            {order.priority === "urgent" ? "URGENT" : "NORMAL"}
          </span>
        </div>
        <span className="deadline" data-testid={`order-deadline-${order.po_number}`}>
          {formatDeadline(order.deadline)}
        </span>
      </div>
      <h3 data-testid={`order-outlet-${order.po_number}`}>{order.outlet}</h3>
      <div className="job-meta">
        <span>
          <Package size={14} />
          {(order.total_quantity || 0).toLocaleString("id-ID")} pcs · {order.item_count} item
        </span>
        <span>
          <UserRound size={14} />
          {operators}
        </span>
      </div>
      <StageBar stage={order.current_stage} />
      <div className="job-bottom">
        <span className="status" data-testid={`order-status-${order.po_number}`}>
          {STAGES[order.current_stage]}
        </span>
        <span className="open-hint">Buka detail <ChevronRight size={16} /></span>
      </div>
    </Link>
  );
}
