import { useEffect } from "react";
import { CupSoda, Package, Printer, ScanLine, Warehouse, X } from "lucide-react";

/**
 * Reusable PWA task shell:
 * - desktop/tablet: centered modal ~600–720px
 * - mobile: bottom sheet / near-full-screen
 * Overlay keeps the underlying detail page visible. No route change.
 *
 * Role identity color = type of task (cyan/purple/blue/orange/teal).
 * Workflow progress colors (gray/amber/green) stay in status marks, not here.
 */
export function opsRoleIcon(roleId) {
  const id = String(roleId || "");
  if (id === "prep") return <CupSoda size={22} aria-hidden="true" />;
  if (id === "screener") return <ScanLine size={22} aria-hidden="true" />;
  if (id === "print") return <Printer size={22} aria-hidden="true" />;
  if (id === "packing") return <Package size={22} aria-hidden="true" />;
  if (id === "loading") return <Warehouse size={22} aria-hidden="true" />;
  return null;
}

export default function MobileTaskModal({
  open,
  onClose,
  title,
  subtitle,
  icon = null,
  roleId = "",
  context = null,
  hideHeader = false,
  showClose = false,
  handle = true,
  testid = "mobile-task-modal",
  labelledBy,
  children,
}) {
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  const tone = String(roleId || "");
  const modalClass = tone ? `mop-task-modal mop-task-modal--${tone}` : "mop-task-modal";
  const resolvedIcon = icon || opsRoleIcon(tone);

  return (
    <div
      className="mop-task-modal-backdrop"
      data-testid={`${testid}-backdrop`}
      onClick={onClose}
    >
      <div
        className={modalClass}
        role="dialog"
        aria-modal="true"
        aria-label={title || "Tugas"}
        aria-labelledby={labelledBy}
        data-testid={testid}
        data-role={tone || undefined}
        onClick={(e) => e.stopPropagation()}
      >
        {handle ? <div className="mop-task-modal-handle" aria-hidden="true" data-testid={`${testid}-handle`} /> : null}
        {!hideHeader && (
          <header className="mop-task-modal-head">
            <div className="mop-task-modal-head-main">
              {resolvedIcon ? <span className="mop-task-modal-icon" aria-hidden="true">{resolvedIcon}</span> : null}
              <div className="mop-task-modal-head-text">
                {title ? <h2 id={labelledBy}>{title}</h2> : null}
                {subtitle ? <p>{subtitle}</p> : null}
              </div>
            </div>
            <button
              type="button"
              className="mop-task-modal-x"
              onClick={onClose}
              aria-label="Tutup"
              data-testid={`${testid}-close`}
            >
              <X size={20} aria-hidden="true" />
            </button>
          </header>
        )}
        {context && (context.customer || context.po || context.sj || context.item) ? (
          <div className="mop-task-context" data-testid={`${testid}-context`}>
            {context.customer ? (
              <div>
                <span>Customer</span>
                <strong>{context.customer}</strong>
              </div>
            ) : null}
            {context.po ? (
              <div>
                <span>No PO</span>
                <strong>{context.po}</strong>
              </div>
            ) : null}
            {context.sj ? (
              <div>
                <span>SJ</span>
                <strong>{context.sj}</strong>
              </div>
            ) : null}
            {context.item ? (
              <div>
                <span>Item</span>
                <strong>{context.item}</strong>
              </div>
            ) : null}
          </div>
        ) : null}
        <div className="mop-task-modal-body">
          {children}
        </div>
        {showClose ? (
          <button
            type="button"
            className="mop-task-close-btn"
            onClick={onClose}
            data-testid={`${testid}-footer-close`}
          >
            Tutup
          </button>
        ) : null}
      </div>
    </div>
  );
}
