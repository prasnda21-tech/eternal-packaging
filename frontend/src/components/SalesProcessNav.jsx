import { NavLink } from "react-router-dom";
import { buildSalesProcessNavLinks } from "../lib/salesProcessNav";

/**
 * Compact page-level sales workflow nav (Workflow 9.0).
 * Does not mutate documents — links only.
 */
export default function SalesProcessNav({
  hasPerm,
  orderId = "",
  poNumber = "",
  invoiceId = "",
}) {
  const links = buildSalesProcessNavLinks({
    hasPerm,
    orderId,
    poNumber,
    invoiceId,
  });
  if (!links.length) return null;

  return (
    <nav
      className="sales-process-nav"
      aria-label="Alur penjualan"
      data-testid="sales-process-nav"
    >
      {links.map((step) => (
        <NavLink
          key={step.id}
          to={step.to}
          className={({ isActive }) =>
            `sales-process-link${isActive ? " is-active" : ""}${
              step.kind === "related" ? " is-related" : ""
            }`
          }
          data-testid={`sales-process-${step.id}`}
          data-nav-kind={step.kind}
        >
          {step.label}
        </NavLink>
      ))}
    </nav>
  );
}
