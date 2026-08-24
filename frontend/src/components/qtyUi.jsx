export function fmtQty(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

export function statusClassFor(label) {
  switch (label) {
    case "DONE":
      return "qty-status-done";
    case "IN PROGRESS":
      return "qty-status-run";
    case "SAVED":
      return "qty-status-saved";
    case "READY":
      return "qty-status-ready";
    default:
      return "";
  }
}

export function MetricRow({ children, testid, columns }) {
  const cols = columns ? `cols-${columns}` : "";
  return (
    <div className={`ind-metrics ${cols}`.trim()} data-testid={testid}>
      {children}
    </div>
  );
}

export function MetricCell({ label, value, unit = "pcs", testid, className = "" }) {
  return (
    <div className={`ind-metric ${className}`.trim()} data-testid={testid}>
      <span className="ind-metric-label">{label}</span>
      <strong>
        {fmtQty(value)}
        {unit ? <span className="ind-metric-unit">{unit}</span> : null}
      </strong>
    </div>
  );
}

/** Alias — metric strip cells, bukan card terpisah. */
export function QuantityStat(props) {
  return <MetricCell {...props} />;
}

export function QuantityInput({
  label,
  unit = "pcs",
  className = "",
  testid,
  disabled,
  ...inputProps
}) {
  return (
    <label className={`qty-control ${disabled ? "is-disabled" : ""} ${className}`.trim()}>
      {label ? <span className="qty-control-label">{label}</span> : null}
      <span className="qty-control-field">
        <input type="number" disabled={disabled} data-testid={testid} {...inputProps} />
        {unit ? <span className="qty-control-unit">{unit}</span> : null}
      </span>
    </label>
  );
}

export function QtyOpsHead({ icon: Icon, title, children }) {
  return (
    <header className="panel-head qty-ops-head">
      <div className="qty-ops-title">
        {Icon ? <Icon size={14} strokeWidth={1.75} aria-hidden="true" /> : null}
        <h3>{title}</h3>
      </div>
      <div className="qty-ops-head-meta">{children}</div>
    </header>
  );
}
