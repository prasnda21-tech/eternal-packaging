import { X } from "lucide-react";

/**
 * Closable workspace tabs (Accurate-like density). Same DOM/CSS as Kas & Bank.
 */
export default function WorkspaceTabs({
  tabs,
  activePath,
  onActivate,
  onClose,
  tabByPath,
  ariaLabel = "Workspace",
  testid = "workspace-tabs",
  testidPrefix = "workspace-tab",
}) {
  if (!tabs?.length) return null;
  const active = typeof tabByPath === "function" ? tabByPath(activePath) : null;
  const activeId = active?.id || tabs.find((t) => t.path === String(activePath || "").split("?")[0].replace(/\/$/, ""))?.id;

  return (
    <div className="kb-workspace-tabs" role="tablist" aria-label={ariaLabel} data-testid={testid}>
      {tabs.map((tab) => {
        const isActive = activeId === tab.id;
        return (
          <div
            key={tab.id}
            className={"kb-workspace-tab" + (isActive ? " is-active" : "")}
            role="tab"
            aria-selected={isActive}
            data-testid={`${testidPrefix}-${tab.id}`}
            data-workspace-tab="1"
            data-path={tab.path}
            data-active={isActive ? "1" : "0"}
          >
            <button
              type="button"
              className="kb-workspace-tab-label"
              onClick={() => onActivate?.(tab)}
              title={tab.title || tab.label}
            >
              {tab.label}
            </button>
            <button
              type="button"
              className="kb-workspace-tab-close"
              aria-label={`Tutup ${tab.label}`}
              data-testid={`${testidPrefix}-close-${tab.id}`}
              onClick={(e) => {
                e.stopPropagation();
                onClose?.(tab.id);
              }}
            >
              <X size={12} strokeWidth={2.25} aria-hidden="true" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
