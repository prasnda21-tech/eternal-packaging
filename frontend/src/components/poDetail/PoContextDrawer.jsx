import { X } from "lucide-react";

/**
 * Shared drawer chrome. Callers keep existing panel testids and body content.
 */
export default function PoContextDrawer({
  open,
  title,
  kicker,
  onClose,
  closeLabel,
  testid,
  labelledBy,
  children,
  className = "",
  as = "section",
}) {
  const Tag = as;
  return (
    <Tag
      className={`panel po-utility-drawer po-context-drawer${open ? " is-open" : ""}${className ? ` ${className}` : ""}`}
      data-testid={testid}
      hidden={!open}
      aria-hidden={!open}
      role={as === "aside" ? "dialog" : undefined}
      aria-modal={as === "aside" ? true : undefined}
      aria-labelledby={labelledBy}
    >
      <header className="panel-head">
        <div>
          {kicker ? <span className="po-summary-kicker">{kicker}</span> : null}
          <h3 id={labelledBy}>{title}</h3>
        </div>
        <button type="button" className="icon-button" aria-label={closeLabel || `Tutup ${title}`} onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      <div className="po-context-drawer-body">
        {children}
      </div>
    </Tag>
  );
}
