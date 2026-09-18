import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { MoreVertical } from "lucide-react";

export default function SalesRowMenu({ items = [], testId }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (!rootRef.current?.contains(e.target)) setOpen(false);
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

  const visible = items.filter(Boolean);
  if (!visible.length) return null;

  return (
    <div className="sw-row-menu" ref={rootRef} data-testid={testId}>
      <button
        type="button"
        className="sw-icon-btn"
        aria-label="Menu aksi tambahan"
        title="Menu aksi tambahan"
        data-testid={testId ? `${testId}-trigger` : "row-menu-trigger"}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <MoreVertical size={16} />
      </button>
      {open && (
        <div className="sw-row-menu-pop" role="menu" onClick={(e) => e.stopPropagation()}>
          {visible.map((item) => {
            if (item.to) {
              return (
                <Link
                  key={item.id}
                  to={item.to}
                  state={item.state}
                  role="menuitem"
                  data-testid={item.testId}
                  onClick={() => setOpen(false)}
                >
                  {item.label}
                </Link>
              );
            }
            return (
              <button
                key={item.id}
                type="button"
                role="menuitem"
                className={item.danger ? "is-danger" : ""}
                data-testid={item.testId}
                onClick={() => {
                  setOpen(false);
                  item.onClick?.();
                }}
              >
                {item.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
