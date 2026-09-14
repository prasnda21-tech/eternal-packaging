import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { MoreHorizontal } from "lucide-react";

/**
 * Compact purchasing row actions.
 * - 0 items → "—"
 * - 1 item → small button/link
 * - 2+ items → ⋯ popover
 * Supports disabled + title (tooltip reason).
 */
export default function PurchaseRowMenu({ items = [], testId }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const menuId = useId();
  const actions = (items || []).filter(Boolean);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!actions.length) {
    return <span className="muted small purchase-action-empty">—</span>;
  }

  const renderAction = (a, { asButtonClass } = {}) => {
    const disabled = Boolean(a.disabled);
    const title = a.title || a.disabledReason || undefined;
    const itemClass = [
      asButtonClass || "purchase-row-menu-item",
      a.danger ? "is-danger" : "",
      a.primary ? "is-primary" : "",
      disabled ? "is-disabled" : "",
    ].filter(Boolean).join(" ");

    if (a.to && !disabled) {
      return (
        <Link
          key={a.key || a.label}
          role="menuitem"
          className={itemClass}
          to={a.to}
          title={title}
          data-testid={a.testId}
          onClick={(e) => {
            e.stopPropagation();
            setOpen(false);
          }}
        >
          {a.label}
        </Link>
      );
    }

    return (
      <button
        key={a.key || a.label}
        type="button"
        role="menuitem"
        className={itemClass}
        title={title}
        disabled={disabled}
        data-testid={a.testId}
        onClick={(e) => {
          e.stopPropagation();
          if (disabled) return;
          setOpen(false);
          a.onClick?.(e);
        }}
      >
        {a.label}
      </button>
    );
  };

  if (actions.length === 1) {
    const only = actions[0];
    const cls = only.primary ? "primary sm" : "ghost sm";
    if (only.to && !only.disabled) {
      return (
        <Link className={cls} to={only.to} data-testid={only.testId || testId} onClick={(e) => e.stopPropagation()} title={only.title}>
          {only.label}
        </Link>
      );
    }
    return (
      <button
        type="button"
        className={cls}
        data-testid={only.testId || testId}
        title={only.title || only.disabledReason}
        disabled={Boolean(only.disabled)}
        onClick={(e) => {
          e.stopPropagation();
          if (only.disabled) return;
          only.onClick?.(e);
        }}
      >
        {only.label}
      </button>
    );
  }

  return (
    <div className="purchase-row-menu" ref={rootRef} data-testid={testId || "purchase-row-menu"}>
      <button
        type="button"
        className="ghost sm purchase-row-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        data-testid={testId ? `${testId}-trigger` : "purchase-row-menu-trigger"}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <MoreHorizontal size={14} />
        <span className="sr-only">Aksi</span>
      </button>
      {open && (
        <div className="purchase-row-menu-popover" role="menu" id={menuId} data-testid={testId ? `${testId}-menu` : "purchase-row-menu-popover"}>
          {actions.map((a) => renderAction(a))}
        </div>
      )}
    </div>
  );
}
