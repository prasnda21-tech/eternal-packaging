/** Compact accounting-style form primitives for Kas & Bank (no colored field cards). */
export function KbSection({ title, description, children, testid }) {
  return (
    <section className="kb-section" data-testid={testid}>
      <header className="kb-section-head">
        <h3>{title}</h3>
        {description ? <p>{description}</p> : null}
      </header>
      <div className="kb-section-body">{children}</div>
    </section>
  );
}

export function KbField({
  id,
  label,
  required = false,
  children,
  className = "",
  hint = "",
}) {
  return (
    <label className={`kb-field ${className}`.trim()} htmlFor={id || undefined}>
      <span className="kb-field-label">
        {label}
        {required ? <span className="ep-req" aria-hidden="true"> *</span> : null}
      </span>
      <div className="kb-field-control">{children}</div>
      {hint ? <span className="kb-field-hint">{hint}</span> : null}
    </label>
  );
}
