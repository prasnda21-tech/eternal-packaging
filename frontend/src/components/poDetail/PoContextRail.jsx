import { ChevronLeft, ClipboardList, GitBranch, Info, Paperclip } from "lucide-react";

export const PO_CONTEXT_RAIL_ITEMS = [
  { id: "informasi-po", railId: "info", kind: "page", label: <>Informasi<br />PO</>, text: "Informasi PO", Icon: Info },
  { id: "perencanaan", railId: "plan", kind: "menu", label: <>Perencanaan</>, text: "Perencanaan", Icon: ClipboardList },
  { id: "dokumen-pengecualian", railId: "documents", kind: "page", label: <>Dokumen &<br />Pengecualian</>, text: "Dokumen & Pengecualian", Icon: Paperclip },
  { id: "alur-pekerjaan", railId: "workflow", kind: "page", label: <>Alur<br />Pekerjaan</>, text: "Alur Pekerjaan", Icon: GitBranch },
];

/**
 * Fixed context navigation rail. Pages replace the workspace. Perencanaan opens a popover.
 */
export default function PoContextRail({
  active,
  onSelect,
  planOpen,
  onTogglePlan,
  planTriggerRef,
}) {
  return (
    <aside className="po-context-rail" aria-label="Navigasi detail PO" data-testid="po-context-rail">
      {PO_CONTEXT_RAIL_ITEMS.map(({ id, railId, kind, label, text, Icon }) => {
        const isMenu = kind === "menu";
        const isActive = isMenu ? !!planOpen : active === id;
        return (
          <button
            key={id}
            type="button"
            ref={isMenu ? planTriggerRef : undefined}
            className={isActive ? "is-active" : ""}
            aria-current={isMenu ? undefined : (isActive ? "page" : undefined)}
            aria-expanded={isMenu ? !!planOpen : undefined}
            aria-haspopup={isMenu ? "menu" : undefined}
            aria-label={text}
            data-testid={isMenu ? "po-plan-actions" : `po-rail-${railId}`}
            data-view={isMenu ? undefined : id}
            onClick={() => (isMenu ? onTogglePlan() : onSelect(id))}
          >
            <Icon size={21} />
            <span>{label}</span>
            {isMenu ? <ChevronLeft size={12} className="po-rail-chevron" aria-hidden="true" /> : null}
          </button>
        );
      })}
    </aside>
  );
}
