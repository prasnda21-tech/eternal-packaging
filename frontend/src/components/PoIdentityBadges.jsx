import { poIdentityBadgeItems } from "../lib/poIdentityBadges";

/** Compact URGENT / BUFFER STOCK badges beside PO identity. No NORMAL badge.

Pass omitUrgent when a sibling already renders URGENT (schedule priority / KP board)
so identity shows BUFFER STOCK only — one URGENT per identity area.
*/
export default function PoIdentityBadges({
  order,
  row,
  testIdPrefix = "po-identity",
  omitUrgent = false,
}) {
  const source = order || row;
  const items = poIdentityBadgeItems(source, { omitUrgent });
  if (!items.length) return null;
  return (
    <span className="po-identity-badges" data-testid={testIdPrefix}>
      {items.map((badge) => (
        <span
          key={badge.key}
          className={badge.className}
          data-testid={`${testIdPrefix}-${badge.key}`}
        >
          {badge.label}
        </span>
      ))}
    </span>
  );
}
