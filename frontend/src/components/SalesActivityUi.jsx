import "./salesActivity.css";

export function SaField({ label, required = false, hint, error, htmlFor, wide = false, children }) {
  return (
    <div className={`sa-field${wide ? " sa-field-wide" : ""}${error ? " is-error" : ""}`}>
      {label ? (
        <label className="sa-field-label" htmlFor={htmlFor}>
          {label}
          {required ? <span className="sa-req" aria-hidden="true"> *</span> : null}
        </label>
      ) : null}
      {children}
      {error ? <p className="sa-field-error">{error}</p> : hint ? <p className="sa-field-hint">{hint}</p> : null}
    </div>
  );
}

export function SaSection({ title, children }) {
  return (
    <section className="sa-section">
      <h3 className="sa-section-title">{title}</h3>
      <div className="sa-section-fields">{children}</div>
    </section>
  );
}

export function SaTabHead({ title, description, children }) {
  return (
    <div className="sa-tab-head">
      <div className="sa-tab-head-copy">
        {title ? <h2>{title}</h2> : null}
        {description ? <p className="muted">{description}</p> : null}
      </div>
      {children ? <div className="sa-page-actions">{children}</div> : null}
    </div>
  );
}

export function SaFormFooter({ onCancel, cancelLabel = "Batal", submitLabel = "Simpan", submitTestId }) {
  return (
    <div className="sa-form-footer">
      {onCancel ? (
        <button type="button" className="ghost" onClick={onCancel}>{cancelLabel}</button>
      ) : null}
      <button type="submit" className="primary" data-testid={submitTestId}>{submitLabel}</button>
    </div>
  );
}

export function fmtSaDate(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("id-ID");
}
