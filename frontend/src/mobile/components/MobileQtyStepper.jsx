export default function MobileQtyStepper({
  id,
  label,
  value,
  onChange,
  disabled,
  max,
}) {
  const n = Math.max(0, parseInt(value, 10) || 0);
  const set = (next) => {
    const capped = max != null ? Math.min(max, Math.max(0, next)) : Math.max(0, next);
    onChange(String(capped || ""));
  };
  return (
    <div className="mop-field">
      {label ? <label htmlFor={id}>{label}</label> : null}
      <div className="mop-stepper">
        <button type="button" aria-label="Kurangi" disabled={disabled} onClick={() => set(n - 1)}>-</button>
        <input
          id={id}
          inputMode="numeric"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value.replace(/[^\d]/g, ""))}
          aria-label={label || "Qty"}
        />
        <button type="button" aria-label="Tambah" disabled={disabled} onClick={() => set(n + 1)}>+</button>
      </div>
    </div>
  );
}
