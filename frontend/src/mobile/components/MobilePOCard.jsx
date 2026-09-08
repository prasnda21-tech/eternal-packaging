import { Link } from "react-router-dom";
import { getCanonicalCustomerName } from "../../lib/canonicalCustomer";
import { STAGES, formatDeadline } from "../../lib/constants";
import { poCardPresentation } from "../../lib/mobileWorkspaces";

export default function MobilePOCard({ order }) {
  const card = poCardPresentation(order);
  const status = order.status_label || STAGES[order.current_stage] || "—";
  return (
    <Link className="mop-card" to={`/m/orders/${encodeURIComponent(order.id)}`} data-testid={`mobile-po-${order.po_number}`}>
      <div className="mop-card-kicker">{order.po_number}</div>
      <div className="mop-card-title">{getCanonicalCustomerName(order)}</div>
      <div className="mop-card-meta">
        <span>{card.product}</span>
        <span>{card.qty != null ? Number(card.qty).toLocaleString("id-ID") : ""}</span>
      </div>
      <div className="mop-card-meta">
        <span>{formatDeadline(order.deadline) !== "-" ? formatDeadline(order.deadline) : (order.work_date || "")}</span>
        <span className="mop-badge is-process">{status}</span>
      </div>
    </Link>
  );
}
