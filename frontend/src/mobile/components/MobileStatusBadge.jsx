export default function MobileStatusBadge({ status }) {
  const key = status?.key || "idle";
  const label = status?.label || "Belum Mulai";
  return (
    <span className={`mop-badge is-${key}`} data-testid="mobile-status-badge">
      {label}
    </span>
  );
}
