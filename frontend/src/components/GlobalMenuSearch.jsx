import { Search, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  MENU_SEARCH_MAX_RESULTS,
  clampMenuSearchHighlight,
  filterMenuSearchItems,
} from "../lib/menuSearch";
import "./globalMenuSearch.css";

/**
 * Desktop command palette for authorized sidebar leaves.
 * Navigation is owned by the parent (navigate to leaf.to).
 */
export default function GlobalMenuSearch({
  open,
  items = [],
  onClose,
  onSelect,
  triggerRef,
}) {
  const dialogRef = useRef(null);
  const inputRef = useRef(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const openGeneration = useRef(0);

  const results = useMemo(
    () => filterMenuSearchItems(items, query, MENU_SEARCH_MAX_RESULTS),
    [items, query],
  );

  // Reset query/highlight each time the palette opens.
  useLayoutEffect(() => {
    if (!open) return;
    openGeneration.current += 1;
    setQuery("");
    setHighlight(0);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    const focusInput = () => {
      if (cancelled) return;
      inputRef.current?.focus({ preventScroll: true });
    };
    focusInput();
    const raf = window.requestAnimationFrame(focusInput);
    const timer = window.setTimeout(focusInput, 0);
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
      window.clearTimeout(timer);
    };
  }, [open]);

  useEffect(() => {
    setHighlight((prev) => clampMenuSearchHighlight(prev, results.length));
  }, [results.length]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose?.();
        const trigger = triggerRef?.current;
        if (trigger && typeof trigger.focus === "function") {
          window.setTimeout(() => trigger.focus({ preventScroll: true }), 0);
        }
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlight((prev) => clampMenuSearchHighlight(prev + 1, results.length));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlight((prev) => clampMenuSearchHighlight(prev - 1, results.length));
        return;
      }
      if (event.key === "Enter") {
        const item = results[highlight];
        if (!item?.to) return;
        event.preventDefault();
        onSelect?.(item);
        onClose?.();
        const trigger = triggerRef?.current;
        if (trigger && typeof trigger.focus === "function") {
          window.requestAnimationFrame(() => trigger.focus());
        }
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose, onSelect, results, highlight, triggerRef]);

  // Lightweight focus containment while open.
  useEffect(() => {
    if (!open) return undefined;
    const root = dialogRef.current;
    if (!root) return undefined;
    const onFocusIn = (event) => {
      if (root.contains(event.target)) return;
      event.stopPropagation();
      inputRef.current?.focus();
    };
    document.addEventListener("focusin", onFocusIn);
    return () => document.removeEventListener("focusin", onFocusIn);
  }, [open]);

  if (!open) return null;

  const activeId = results[highlight]
    ? `menu-search-option-${results[highlight].key || highlight}`
    : undefined;

  return (
    <div className="ep-menu-search" data-testid="global-menu-search">
      <button
        type="button"
        className="ep-menu-search-scrim"
        aria-label="Tutup pencarian menu"
        data-testid="global-menu-search-scrim"
        onClick={() => {
          onClose?.();
          const trigger = triggerRef?.current;
          if (trigger && typeof trigger.focus === "function") {
            window.requestAnimationFrame(() => trigger.focus());
          }
        }}
      />
      <div
        ref={dialogRef}
        className="ep-menu-search-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Cari menu"
        data-testid="global-menu-search-dialog"
      >
        <div className="ep-menu-search-field">
          <Search size={16} aria-hidden="true" />
          <input
            ref={inputRef}
            type="search"
            className="ep-menu-search-input"
            placeholder="Cari menu, divisi, atau bagian…"
            value={query}
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={activeId}
            data-testid="global-menu-search-input"
            onChange={(event) => {
              setQuery(event.target.value);
              setHighlight(0);
            }}
          />
          {query ? (
            <button
              type="button"
              className="ep-menu-search-clear"
              aria-label="Hapus pencarian"
              data-testid="global-menu-search-clear"
              onClick={() => {
                setQuery("");
                setHighlight(0);
                inputRef.current?.focus();
              }}
            >
              <X size={14} />
            </button>
          ) : null}
        </div>
        <ul
          id={listId}
          className="ep-menu-search-list"
          role="listbox"
          aria-label="Hasil menu"
          data-testid="global-menu-search-results"
        >
          {results.length === 0 ? (
            <li className="ep-menu-search-empty" role="presentation" data-testid="global-menu-search-empty">
              Tidak ada menu yang cocok
            </li>
          ) : (
            results.map((item, index) => {
              const selected = index === highlight;
              const optionId = `menu-search-option-${item.key || index}`;
              const secondary = [item.groupLabel, item.section].filter(Boolean).join(" · ");
              return (
                <li key={`${item.key || item.to}-${index}`} role="presentation">
                  <button
                    type="button"
                    id={optionId}
                    role="option"
                    aria-selected={selected}
                    className={"ep-menu-search-option" + (selected ? " is-active" : "")}
                    data-testid={`global-menu-search-option-${item.key || index}`}
                    onMouseEnter={() => setHighlight(index)}
                    onClick={() => {
                      onSelect?.(item);
                      onClose?.();
                      const trigger = triggerRef?.current;
                      if (trigger && typeof trigger.focus === "function") {
                        window.requestAnimationFrame(() => trigger.focus());
                      }
                    }}
                  >
                    <span className="ep-menu-search-option-label">{item.label}</span>
                    {secondary ? (
                      <span className="ep-menu-search-option-group">{secondary}</span>
                    ) : null}
                  </button>
                </li>
              );
            })
          )}
        </ul>
        <footer className="ep-menu-search-hint" data-testid="global-menu-search-hint">
          ↑↓ pilih · Enter buka · Esc tutup
        </footer>
      </div>
    </div>
  );
}
