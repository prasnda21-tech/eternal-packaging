/** Informational / existing-navigation six-role strip. Counts are pre-derived. */

import { Cog, CupSoda, Layers, Package, Send, Truck } from "lucide-react";

const ROLE_ICONS = {
  prep: CupSoda,
  screener: Layers,
  produksi: Cog,
  packing: Package,
  loading: Truck,
  pengiriman: Send,
};

export default function MobileOpsRoleSummary({
  roles = [],
  activeRoleId = "",
  onSelectRole,
  testid = "mop-ops-role-summary",
}) {
  return (
    <div className="mop-ops-roles" data-testid={testid} role="group" aria-label="Ringkasan peran operasional">
      {roles.map((role, idx) => {
        const interactive = typeof onSelectRole === "function" && role.interactive !== false;
        const selected = Boolean(activeRoleId) && activeRoleId === (role.workspaceId || role.id);
        const count = role.count;
        const Tag = interactive ? "button" : "div";
        const Icon = ROLE_ICONS[role.id] || Cog;
        const extras = interactive
          ? {
            type: "button",
            onClick: () => onSelectRole(role),
          }
          : {};
        return (
          <Tag
            key={role.id}
            className={`mop-ops-role${selected ? " is-active" : ""}${interactive ? " is-interactive" : ""}`}
            data-testid={`mop-ops-role-${role.id}`}
            aria-pressed={interactive ? selected : undefined}
            {...extras}
          >
            <span className="mop-ops-role-icon" aria-hidden="true">
              <Icon size={18} strokeWidth={2.2} />
            </span>
            <span className="mop-ops-role-copy">
              <span className="mop-ops-role-label">{role.label}</span>
              {count ? (
                <>
                  <strong className="mop-ops-role-count" data-testid={`mop-ops-role-count-${role.id}`}>
                    {count.total} {role.noun}
                  </strong>
                  {count.urgent > 0 ? (
                    <span className="mop-ops-role-urgent" data-testid={`mop-ops-role-urgent-${role.id}`}>
                      {count.urgent} urgent
                    </span>
                  ) : null}
                </>
              ) : (
                <span className="mop-ops-role-count is-pending" data-testid={`mop-ops-role-count-${role.id}`}>
                  —
                </span>
              )}
            </span>
            {idx < roles.length - 1 ? <span className="mop-ops-role-chevron" aria-hidden="true">›</span> : null}
          </Tag>
        );
      })}
      <div className="mop-ops-role-aside" data-testid="mop-ops-role-aside">
        Satu Sistem, Setiap Role Tahu Apa yang Harus Dikerjakan.
      </div>
    </div>
  );
}
