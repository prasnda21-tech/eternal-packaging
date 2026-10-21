import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { filterFacetOptions, listboxViewState } from "./FacetSearchSelect";
import { placeMasterItemListBox } from "./MasterItemSearchSelect";
import "./masterItemSearchSelect.css";

export const LOOKUP_SEARCH_DEBOUNCE_MS = 150;

/**
 * Client-side searchable lookup for create forms.
 * Selected value is always the option id/key — typing never invents a new identity.
 * Editing the query after a selection clears the bound id until the user re-selects.
 */
export default function LookupSearchSelect({
  options = [],
  value = "",
  onChange,
  getOptionValue,
  getOptionLabel,
  getOptionMeta,
  getSearchText,
  placeholder = "Ketik untuk mencari…",
  noResultLabel = "Tidak ada hasil yang cocok",
  disabled = false,
  allowClear = true,
  testId = "lookup-search",
  className = "",
  /** Raise list above modal overlays (receipt create). */
  overModal = false,
}) {
  const listId = useId();
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const gettersRef = useRef({ getOptionValue, getOptionLabel, getOptionMeta, getSearchText });
  gettersRef.current = { getOptionValue, getOptionLabel, getOptionMeta, getSearchText };

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [filtered, setFiltered] = useState(options || []);
  const [highlight, setHighlight] = useState(0);
  const [listBox, setListBox] = useState(null);

  const selected = useMemo(() => {
    const resolveValue = gettersRef.current.getOptionValue;
    return (options || []).find((opt) => String(resolveValue(opt)) === String(value)) || null;
  }, [options, value]);

  const selectedLabel = selected
    ? gettersRef.current.getOptionLabel(selected)
    : "";
  const display = open ? query : selectedLabel;

  useEffect(() => {
    if (!open || disabled) return undefined;
    setPending(true);
    setFiltered([]);
    const handle = window.setTimeout(() => {
      const { getOptionLabel: labelFn, getSearchText: searchFn } = gettersRef.current;
      const resolveSearch = searchFn || labelFn;
      setFiltered(filterFacetOptions(options, query, resolveSearch));
      setHighlight(0);
      setPending(false);
    }, LOOKUP_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query, open, disabled, options]);

  const placeList = useCallback(() => {
    const anchor = rootRef.current;
    if (!anchor || !anchor.isConnected || typeof window === "undefined") {
      setListBox(null);
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const measured = listRef.current ? listRef.current.scrollHeight : 0;
    const next = placeMasterItemListBox(rect, {
      width: window.innerWidth,
      height: window.innerHeight,
    }, measured);
    if (!next) return;
    setListBox((prev) => {
      if (
        prev
        && prev.top === next.top
        && prev.left === next.left
        && prev.width === next.width
        && prev.maxHeight === next.maxHeight
      ) {
        return prev;
      }
      return next;
    });
  }, []);

  useLayoutEffect(() => {
    if (!open || disabled) {
      setListBox(null);
      return undefined;
    }
    placeList();
    window.addEventListener("resize", placeList);
    window.addEventListener("scroll", placeList, true);
    return () => {
      window.removeEventListener("resize", placeList);
      window.removeEventListener("scroll", placeList, true);
    };
  }, [open, disabled, placeList, filtered, pending]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event) => {
      const t = event.target;
      if (rootRef.current?.contains(t)) return;
      if (listRef.current?.contains(t)) return;
      setOpen(false);
      setQuery("");
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const pick = (opt) => {
    if (!opt) return;
    const { getOptionValue: valueFn } = gettersRef.current;
    onChange?.(valueFn(opt), opt);
    setQuery("");
    setOpen(false);
  };

  const clear = () => {
    onChange?.("", null);
    setQuery("");
    setOpen(false);
  };

  const view = listboxViewState({
    pending,
    loading: false,
    error: "",
    itemCount: filtered.length,
  });

  const onKeyDown = (event) => {
    if (disabled) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (event.key === "Enter" && open) {
      event.preventDefault();
      if (!pending && filtered[highlight]) pick(filtered[highlight]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      setQuery("");
    }
  };

  const listNode = open && !disabled && listBox ? (
    <ul
      ref={listRef}
      id={listId}
      className={`mis-list ux90-lookup-list${overModal ? " mis-list-over-modal mis-list-over-receipt" : ""}`}
      role="listbox"
      data-testid={`${testId}-list`}
      style={{
        top: listBox.top,
        left: listBox.left,
        width: listBox.width,
        maxHeight: listBox.maxHeight,
      }}
    >
      {view === "loading" && (
        <li className="mis-status" data-testid={`${testId}-loading`}>Mencari…</li>
      )}
      {view === "empty" && (
        <li className="mis-status" data-testid={`${testId}-empty`}>{noResultLabel}</li>
      )}
      {view === "results" && filtered.map((opt, index) => {
        const { getOptionValue: valueFn, getOptionLabel: labelFn, getOptionMeta: metaFn } = gettersRef.current;
        const key = String(valueFn(opt) || index);
        const active = highlight === index;
        const meta = metaFn ? metaFn(opt) : "";
        return (
          <li key={key}>
            <button
              type="button"
              role="option"
              aria-selected={active}
              className={`mis-option${active ? " is-active" : ""}`}
              onMouseEnter={() => setHighlight(index)}
              onClick={() => pick(opt)}
              data-testid={`${testId}-option-${key}`}
            >
              <span className="mis-option-name">{labelFn(opt)}</span>
              {meta ? <span className="mis-option-meta">{meta}</span> : null}
            </button>
          </li>
        );
      })}
    </ul>
  ) : null;

  return (
    <div
      className={`mis-root ux90-lookup${open ? " is-open" : ""}${className ? ` ${className}` : ""}`.trim()}
      ref={rootRef}
      data-testid={`${testId}-root`}
    >
      <div className="mis-control">
        <span className="ux90-lookup-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" strokeLinecap="round" />
          </svg>
        </span>
        <input
          className="mis-input ux90-lookup-input"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          disabled={disabled}
          value={display}
          placeholder={placeholder}
          autoComplete="off"
          onChange={(event) => {
            const next = event.target.value;
            setQuery(next);
            setOpen(true);
            setPending(true);
            setFiltered([]);
            if (value !== "" && value != null) {
              if (next !== selectedLabel) onChange?.("", null);
            }
          }}
          onFocus={() => {
            if (disabled) return;
            setOpen(true);
            setQuery(selectedLabel);
          }}
          onKeyDown={onKeyDown}
          data-testid={testId}
        />
        {allowClear && value !== "" && value != null && !disabled && (
          <button
            type="button"
            className="mis-clear"
            aria-label="Hapus pilihan"
            onClick={clear}
            data-testid={`${testId}-clear`}
          >
            ×
          </button>
        )}
      </div>
      {typeof document !== "undefined" && listNode
        ? createPortal(listNode, document.body)
        : null}
    </div>
  );
}
