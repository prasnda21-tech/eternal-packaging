import { useEffect } from "react";
import { X } from "lucide-react";

/**
 * Reusable PWA task shell:
 * - desktop/tablet: centered modal ~600–720px
 * - mobile: bottom sheet / near-full-screen
 * Overlay keeps the underlying detail page visible. No route change.
 */
export default function MobileTaskModal({
  open,
  onClose,
  title,
  subtitle,
  icon = null,
  hideHeader = false,
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

  return (
    <div
      className="mop-task-modal-backdrop"
      data-testid={`${testid}-backdrop`}
      onClick={onClose}
    >
      <div
        className="mop-task-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title || "Tugas"}
        aria-labelledby={labelledBy}
        data-testid={testid}
        onClick={(e) => e.stopPropagation()}
      >
        {handle ? <div className="mop-task-modal-handle" aria-hidden="true" data-testid={`${testid}-handle`} /> : null}
        {!hideHeader && (
          <header className="mop-task-modal-head">
            <div className="mop-task-modal-head-main">
              {icon ? <span className="mop-task-modal-icon" aria-hidden="true">{icon}</span> : null}
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
        <div className="mop-task-modal-body">
          {children}
        </div>
      </div>
    </div>
  );
}
