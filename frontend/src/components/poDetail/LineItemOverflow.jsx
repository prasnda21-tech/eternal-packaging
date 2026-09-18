import { useEffect, useRef, useState } from "react";
import { MoreVertical } from "lucide-react";

/**
 * Row-scoped overflow for leftover line-item facts. No new mutations.
 */
export default function LineItemOverflow({ line, index }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const notes = String(line?.notes || "").trim();

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="li-overflow" ref={rootRef} data-testid={`line-item-overflow-${index}`}>
      <button
        type="button"
        className="icon-button li-overflow-btn"
        aria-label="Aksi tambahan item"
        aria-expanded={open}
        data-testid={`line-item-more-${index}`}
        onClick={() => setOpen((v) => !v)}
      >
        <MoreVertical size={16} />
      </button>
      {open && (
        <div className="li-overflow-menu" role="menu" data-testid={`line-item-more-menu-${index}`}>
          {notes ? (
            <p className="li-overflow-notes" role="menuitem">{notes}</p>
          ) : (
            <p className="muted small" role="menuitem">Tidak ada aksi tambahan</p>
          )}
        </div>
      )}
    </div>
  );
}
