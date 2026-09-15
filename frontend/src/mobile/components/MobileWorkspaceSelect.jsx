/** Compact permission-driven workspace picker — replaces horizontal task chips. */

export default function MobileWorkspaceSelect({
  workspaces = [],
  value,
  onChange,
  label = "Pilih Tugas",
  testid = "mobile-workspace-select",
  className = "",
}) {
  if (!workspaces.length) return null;
  const wrapClass = ["mop-ws-select", className].filter(Boolean).join(" ");
  if (workspaces.length === 1) {
    return (
      <div className={`${wrapClass} mop-ws-select--single`} data-testid={testid}>
        <span className="mop-ws-select-label">{label}</span>
        <strong data-testid={`${testid}-current`}>{workspaces[0].label}</strong>
      </div>
    );
  }
  return (
    <label className={wrapClass} data-testid={testid}>
      <span className="mop-ws-select-label">{label}</span>
      <select
        value={value || workspaces[0]?.id || ""}
        onChange={(e) => onChange?.(e.target.value)}
        aria-label={label}
        data-testid={`${testid}-input`}
      >
        {workspaces.map((ws) => (
          <option key={ws.id} value={ws.id}>{ws.label}</option>
        ))}
      </select>
    </label>
  );
}
