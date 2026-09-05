/** Compact accounting-style form primitives for Kas & Bank (no colored field cards). */
import { Search, X } from "lucide-react";

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

/** Search input with Lucide icon inside the field — shared by Mutasi + Rekonsiliasi. */
export function KbSearchField({
  value,
  onChange,
  onSubmit,
  onClear,
  placeholder = "Cari…",
  testid,
  ariaLabel,
  className = "",
}) {
  return (
    <form
      className={`kb-search-field ${className}`.trim()}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit?.(e);
      }}
      role="search"
    >
      <Search size={14} strokeWidth={2} aria-hidden="true" className="kb-search-field-icon" />
      <input
        type="text"
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        data-testid={testid}
        aria-label={ariaLabel || placeholder}
        autoComplete="off"
      />
      {value ? (
        <button type="button" className="kb-search-field-clear" onClick={onClear} aria-label="Bersihkan">
          <X size={12} strokeWidth={2} aria-hidden="true" />
        </button>
      ) : null}
    </form>
  );
}
