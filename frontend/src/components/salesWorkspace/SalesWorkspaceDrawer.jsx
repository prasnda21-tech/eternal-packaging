import { useEffect, useRef } from "react";
import { X } from "lucide-react";

export default function SalesWorkspaceDrawer({
  open,
  title,
  subtitle,
  tabs = [],
  activeTab,
  onTabChange,
  onClose,
  footer,
  children,
  testId = "sales-workspace-drawer",
  width,
}) {
  const panelRef = useRef(null);
  const lastFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    lastFocusRef.current = document.activeElement;
    const panel = panelRef.current;
    const focusable = panel?.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    focusable?.[0]?.focus();

    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose?.();
        return;
      }
      if (e.key !== "Tab" || !focusable?.length) return;
      const list = Array.from(focusable);
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (lastFocusRef.current && typeof lastFocusRef.current.focus === "function") {
        lastFocusRef.current.focus();
      }
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="sw-drawer-root" data-testid={testId}>
      <button type="button" className="sw-drawer-scrim" aria-label="Tutup drawer" onClick={onClose} />
      <aside
        ref={panelRef}
        className="sw-drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : "Detail"}
        style={width ? { width } : undefined}
      >
        <header className="sw-drawer-head">
          <div>
            <h2 style={{ margin: 0, fontSize: 16 }}>{title}</h2>
            {subtitle ? <p style={{ margin: "2px 0 0", fontSize: 12, color: "#64748b" }}>{subtitle}</p> : null}
          </div>
          <button type="button" className="sw-icon-btn" onClick={onClose} aria-label="Tutup" data-testid="btn-close-drawer" title="Tutup">
            <X size={16} />
          </button>
        </header>
        {tabs.length > 0 && (
          <div className="sw-drawer-tabs" role="tablist">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.id}
                className={activeTab === tab.id ? "is-active" : ""}
                onClick={() => onTabChange?.(tab.id)}
                data-testid={`drawer-tab-${tab.id}`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}
        <div className="sw-drawer-body">{children}</div>
        {footer ? <footer className="sw-drawer-foot">{footer}</footer> : null}
      </aside>
    </div>
  );
}
