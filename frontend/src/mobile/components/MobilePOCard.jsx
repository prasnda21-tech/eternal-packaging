import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../../lib/auth";
import { getCanonicalCustomerName } from "../../lib/canonicalCustomer";
import { STAGES, formatDeadline } from "../../lib/constants";
import {
  capturePoOpenFromList,
  poNavStateFromList,
} from "../../lib/poListReturn";
import { poCardPresentation } from "../../lib/mobileWorkspaces";
import PoIdentityBadges from "../../components/PoIdentityBadges";

export default function MobilePOCard({ order }) {
  const { user } = useAuth();
  const location = useLocation();
  const card = poCardPresentation(order);
  const status = order.status_label || STAGES[order.current_stage] || "—";
  const navState = poNavStateFromList({
    pathname: location.pathname,
    search: location.search,
  });
  return (
    <Link
      className="mop-card"
      to={`/m/orders/${encodeURIComponent(order.id)}`}
      state={navState}
      data-testid={`mobile-po-${order.po_number}`}
      onClick={() => {
        capturePoOpenFromList({
          user,
          orderId: order.id,
          pathname: location.pathname,
          search: location.search,
          scrollY: typeof window !== "undefined" ? window.scrollY : undefined,
        });
      }}
    >
      <div className="mop-card-kicker">
        {order.po_number}{" "}
        <PoIdentityBadges order={order} testIdPrefix={`mobile-po-${order.po_number}`} />
      </div>
      <div className="mop-card-title">{getCanonicalCustomerName(order)}</div>
      <div className="mop-card-meta">
        <span>{card.product}</span>
        <span>{card.qty != null ? Number(card.qty).toLocaleString("id-ID") : ""}</span>
      </div>
      <div className="mop-card-meta">
        <span>
          {formatDeadline(
            order.tanggal_pengiriman
            || order.effective_delivery_date
            || order.delivery_deadline_effective
            || order.scheduled_delivery_date
            || order.delivery_deadline_manual
          ) !== "-"
            ? formatDeadline(
              order.tanggal_pengiriman
              || order.effective_delivery_date
              || order.delivery_deadline_effective
              || order.scheduled_delivery_date
              || order.delivery_deadline_manual
            )
            : (order.work_date || "")}
        </span>
        <span className="mop-badge is-process">{status}</span>
      </div>
    </Link>
  );
}
