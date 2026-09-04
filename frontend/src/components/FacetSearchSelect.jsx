import { useEffect, useId, useMemo, useRef, useState } from "react";
import "./masterItemSearchSelect.css";

export const FACET_SEARCH_DEBOUNCE_MS = 150;

/** Pure UI state for listbox — empty only after active query settles. */
export function listboxViewState({ pending, loading, error, itemCount }) {
  if (pending || loading) return "loading";
  if (error) return "error";
  if (!itemCount) return "empty";
  return "results";
}

export function filterFacetOptions(options, needle, getSearchText) {
  const q = String(needle || "").trim().toLowerCase();
  const list = options || [];
  if (!q) return list;
  return list.filter((opt) => String(getSearchText(opt) || "").toLowerCase().includes(q));
}

/**
 * Client-side searchable select for report/facet product filters.
 * Does not call master-items API — filters the provided options only.
 */
export default function FacetSearchSelect({
  options = [],
  value = "",
  onChange,
  getOptionValue,
  getOptionLabel,
  getSearchText,
  emptyValue = "",
  emptyLabel = "Semua Produk",
  placeholder = "Cari produk…",
  noResultLabel = "Produk tidak ditemukan",
  disabled = false,
  testId = "facet-item-search",
  className = "",
}) {
  const listId = useId();
  const rootRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [filtered, setFiltered] = useState(options);
  const [highlight, setHighlight] = useState(0);

  const resolveValue = getOptionValue;
  const resolveLabel = getOptionLabel;
  const resolveSearch = getSearchText || getOptionLabel;

  const selected = useMemo(
    () => (options || []).find((opt) => String(resolveValue(opt)) === String(value)) || null,
    [options, value, resolveValue]
  );

  const display = open
    ? query
    : (selected ? resolveLabel(selected) : (value ? String(value) : ""));

  useEffect(() => {
    if (!open || disabled) return undefined;
    setPending(true);
    setFiltered([]);
    const handle = window.setTimeout(() => {
      setFiltered(filterFacetOptions(options, query, resolveSearch));
      setHighlight(0);
      setPending(false);
    }, FACET_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query, open, disabled, options, resolveSearch]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const pick = (opt) => {
    onChange?.(resolveValue(opt));
    setQuery("");
    setOpen(false);
  };

  const pickAll = () => {
    onChange?.(emptyValue);
    setQuery("");
    setOpen(false);
  };

  const view = listboxViewState({
    pending,
    loading: false,
    error: "",
    itemCount: filtered.length + 1,
  });

  const onKeyDown = (event) => {
    if (disabled) return;
    const rows = [null, ...filtered];
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, rows.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (event.key === "Enter" && open) {
      event.preventDefault();
      if (highlight === 0) pickAll();
      else if (filtered[highlight - 1]) pick(filtered[highlight - 1]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className={`mis-root ${className}`.trim()} ref={rootRef} data-testid={testId}>
      <div className="mis-control">
        <input
          className="mis-input"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          disabled={disabled}
          value={display}
          placeholder={placeholder}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            setPending(true);
            setFiltered([]);
          }}
          onFocus={() => {
            if (disabled) return;
            setOpen(true);
            setQuery("");
          }}
          onKeyDown={onKeyDown}
          data-testid={`${testId}-input`}
        />
        {value !== emptyValue && value !== "" && !disabled && (
          <button
            type="button"
            className="mis-clear"
            aria-label="Hapus filter produk"
            onClick={pickAll}
            data-testid={`${testId}-clear`}
          >
            ×
          </button>
        )}
      </div>
      {open && !disabled && (
        <ul id={listId} className="mis-list" role="listbox" data-testid={`${testId}-list`}>
          {(view === "loading") && (
            <li className="mis-status" data-testid={`${testId}-loading`}>Memuat…</li>
          )}
          {view !== "loading" && (
            <li>
              <button
                type="button"
                role="option"
                aria-selected={highlight === 0}
                className={`mis-option ${highlight === 0 ? "is-active" : ""}`}
                onMouseEnter={() => setHighlight(0)}
                onClick={pickAll}
                data-testid={`${testId}-option-all`}
              >
                <span className="mis-option-name">{emptyLabel}</span>
              </button>
            </li>
          )}
          {view !== "loading" && filtered.length === 0 && query.trim() && (
            <li className="mis-status" data-testid={`${testId}-empty`}>{noResultLabel}</li>
          )}
          {view !== "loading" && filtered.map((opt, index) => {
            const key = String(resolveValue(opt));
            const active = highlight === index + 1;
            return (
              <li key={key || index}>
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  className={`mis-option ${active ? "is-active" : ""}`}
                  onMouseEnter={() => setHighlight(index + 1)}
                  onClick={() => pick(opt)}
                  data-testid={`${testId}-option-${key}`}
                >
                  <span className="mis-option-name">{resolveLabel(opt)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
