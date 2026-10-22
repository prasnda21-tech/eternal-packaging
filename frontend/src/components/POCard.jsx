import { Link } from "react-router-dom";
import { Package, UserRound, ChevronRight } from "lucide-react";
import { STAGES, formatDeadline } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { schedulePoHref } from "../lib/workPanel";
import StageBar from "./StageBar";
import PoIdentityBadges from "./PoIdentityBadges";

export default function POCard({ order }) {
  const { hasPerm } = useAuth();
  const href = schedulePoHref(order.id, hasPerm);
  const operators = order.operator_splits?.length
    ? order.operator_splits.map((s) => s.operator_name).join(" / ")
    : "Belum ditentukan";

  return (
    <Link
      to={href}
      className="job-row"
      data-testid={`order-row-${order.po_number}`}
      aria-label={`Buka pekerjaan ${order.po_number}`}
    >
      <div className="job-top">
        <div>
          <span className="po" data-testid={`order-po-${order.po_number}`}>{order.po_number}</span>
          <PoIdentityBadges order={order} testIdPrefix={`order-priority-${order.po_number}`} />
        </div>
        <div>
          <span className="lbl">Tanggal Pengiriman</span>
          <strong data-testid={`order-tanggal-pengiriman-${order.po_number}`}>
            {formatDeadline(
              order.tanggal_pengiriman
              || order.effective_delivery_date
              || order.delivery_deadline_effective
              || order.scheduled_delivery_date
              || order.delivery_deadline_manual
            )}
          </strong>
        </div>
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
      <StageBar stage={order.current_stage} order={order} />
      <div className="job-bottom">
        <span className="status" data-testid={`order-status-${order.po_number}`}>
          {STAGES[order.current_stage]}
        </span>
        <span className="open-hint">Buka detail <ChevronRight size={16} /></span>
      </div>
    </Link>
  );
}
