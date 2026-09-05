import { X } from "lucide-react";
import { cashBankTabByPath } from "../lib/cashBankWorkspace";

/**
 * Closable workspace tabs for Kas & Bank (Accurate-like density, Eternal branding).
 */
export default function CashBankWorkspaceTabs({ tabs, activePath, onActivate, onClose }) {
  if (!tabs?.length) return null;
  const active = cashBankTabByPath(activePath);

  return (
    <div className="kb-workspace-tabs" role="tablist" aria-label="Workspace Kas & Bank" data-testid="kas-bank-workspace-tabs">
      {tabs.map((tab) => {
        const isActive = active?.id === tab.id;
        return (
          <div
            key={tab.id}
            className={"kb-workspace-tab" + (isActive ? " is-active" : "")}
            role="tab"
            aria-selected={isActive}
            data-testid={`kas-bank-workspace-tab-${tab.id}`}
          >
            <button
              type="button"
              className="kb-workspace-tab-label"
              onClick={() => onActivate?.(tab)}
              title={tab.label}
            >
              {tab.label}
            </button>
            <button
              type="button"
              className="kb-workspace-tab-close"
              aria-label={`Tutup ${tab.label}`}
              data-testid={`kas-bank-workspace-tab-close-${tab.id}`}
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
