import { Info, Paperclip, Settings } from "lucide-react";

const RAIL_ITEMS = [
  { id: "info", label: <>Informasi<br />PO</>, Icon: Info },
  { id: "operational", label: "Operasional", Icon: Settings },
  { id: "documents", label: <>Dokumen &<br />Pengecualian</>, Icon: Paperclip },
];

/**
 * Fixed context rail — part of the shell, never a floating action button.
 */
export default function PoContextRail({ active, onSelect }) {
  return (
    <aside className="po-context-rail" aria-label="Panel detail PO" data-testid="po-context-rail">
      {RAIL_ITEMS.map(({ id, label, Icon }) => (
        <button
          key={id}
          type="button"
          className={active === id ? "is-active" : ""}
          aria-pressed={active === id}
          data-testid={`po-rail-${id}`}
          onClick={() => onSelect(active === id ? null : id)}
        >
          <Icon size={21} /><span>{label}</span>
        </button>
      ))}
    </aside>
  );
}
