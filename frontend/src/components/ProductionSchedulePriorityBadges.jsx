import { productionSchedulePriorityIndicators } from "../lib/productionSchedulePriority";

const BADGE_CLASS = {
  urgent: "urgent-badge",
  h1: "standard-badge prod-h1-badge",
};

export default function ProductionSchedulePriorityBadges({ row, testIdPrefix }) {
  const badges = productionSchedulePriorityIndicators(row);
  if (!badges.length) return null;
  return (
    <span className="prod-priority-badges" data-testid={testIdPrefix || "priority-badges"}>
      {badges.map((badge) => (
        <span
          key={badge.key}
          className={BADGE_CLASS[badge.key] || "standard-badge"}
          data-testid={testIdPrefix ? `${testIdPrefix}-${badge.key}` : `priority-${badge.key}`}
        >
          {badge.label}
        </span>
      ))}
    </span>
  );
}
