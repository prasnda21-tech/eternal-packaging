/**
 * Compact branch selector for HOLDING_TEAM / local unrestricted scope.
 * Normal assigned users never see this — backend already fixes their branch.
 *
 * Purchasing/Inventory recovery only — not full ERP enterprise isolation.
 */
export default function BranchScopeSelect({
  branches = [],
  value = "",
  onChange,
  canSwitch = false,
  effectiveCode = "",
  testId = "branch-scope-select",
  label = "Cabang",
}) {
  const rows = (Array.isArray(branches) ? branches : []).filter((row) => row?.active !== false);
  if (!canSwitch) {
    if (!effectiveCode && !value) return null;
    const fixed = rows.find((row) => row.id === value);
    const code = fixed?.code || effectiveCode || "—";
    return (
      <span className="muted small" data-testid={`${testId}-fixed`}>
        Cabang: {code}
      </span>
    );
  }
  return (
    <label className="branch-scope-select" data-testid={testId}>
      <span className="muted small">{label}</span>
      <select
        value={value || ""}
        onChange={(e) => onChange?.(e.target.value)}
        data-testid={`${testId}-input`}
        aria-label={label}
      >
        {!value && (
          <option value="" disabled>
            Pilih cabang
          </option>
        )}
        {rows.map((row) => (
          <option key={row.id} value={row.id}>
            {row.code || row.name || row.id}
          </option>
        ))}
      </select>
    </label>
  );
}
