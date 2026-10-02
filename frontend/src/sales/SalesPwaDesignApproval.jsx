import { approvalActionPath } from "../lib/customerTracking";

export function salesDesignApprovalPath(row) {
  return approvalActionPath({ status: row?.status, path: row?.path });
}

export function designApprovalIdentity(row) {
  const design = String(row?.design_name || "").trim();
  const items = Array.isArray(row?.items)
    ? row.items.map((item) => String(item?.label || "").trim()).filter(Boolean)
    : [];
  return [design, ...items].filter(Boolean).join(" · ");
}

export function orderExpectsDesignLink(order) {
  return String(order?.status || "").trim() === "Approval Design";
}

export default function SalesPwaDesignApproval({ order, expectMissing = false }) {
  const rows = Array.isArray(order?.design_approvals) ? order.design_approvals : [];
  if (!rows.length) {
    if (!expectMissing) return null;
    return (
      <p className="spwa-approval-missing" data-testid={`sales-approval-missing-${order?.id || "order"}`}>
        Link approval belum tersedia
      </p>
    );
  }
  return (
    <div className="spwa-approval-list" data-testid={`sales-approval-list-${order?.id || "order"}`}>
      {rows.map((row, index) => {
        const path = salesDesignApprovalPath(row);
        const identity = designApprovalIdentity(row);
        const key = String(row?.id || identity || index);
        return (
          <div key={key} className="spwa-approval-row" data-testid={`sales-approval-row-${key}`}>
            {identity ? <small className="spwa-approval-identity">{identity}</small> : null}
            {row?.status_label ? <small className="spwa-approval-status">{row.status_label}</small> : null}
            {path ? (
              <a
                href={path}
                target="_blank"
                rel="noopener noreferrer"
                className="spwa-approval-open"
                data-testid={`sales-approval-open-${key}`}
              >
                Buka Approval Desain
              </a>
            ) : (
              <p className="spwa-approval-missing" data-testid={`sales-approval-missing-${key}`}>
                Link approval belum tersedia
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
