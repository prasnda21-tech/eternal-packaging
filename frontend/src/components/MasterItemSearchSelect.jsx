import { useCallback, useEffect, useId, useRef, useState } from "react";
import api from "../lib/api";
import "./masterItemSearchSelect.css";

export const MASTER_ITEM_SEARCH_DEBOUNCE_MS = 300;
export const MASTER_ITEM_SEARCH_PAGE_SIZE = 40;

export function formatMasterItemLabel(item) {
  if (!item) return "";
  const name = String(item.item_name || item.name || "").trim();
  const code = String(item.item_code || "").trim();
  if (name && code) return `${name} — ${code}`;
  return name || code || String(item.accurate_item_id || item.id || "");
}

export function masterItemOptionKey(item) {
  if (!item) return "";
  return String(item.accurate_item_id || item.id || item.master_item_id || "");
}

/** Pure helpers for tests / race handling. */
export function shouldApplySearchResult(requestId, latestId) {
  return Number(requestId) === Number(latestId);
}

export function isDuplicateAccurateItem(selectedList, candidate) {
  const aid = String(candidate?.accurate_item_id || "").trim();
  if (!aid) return false;
  return (selectedList || []).some((row) => String(row?.accurate_item_id || "").trim() === aid);
}

/**
 * Listbox presentation state.
 * Empty / results only after the active query has settled (not during debounce or in-flight).
 */
export function listboxViewState({ pending, loading, error, itemCount }) {
  if (pending || loading) return "loading";
  if (error) return "error";
  if (!itemCount) return "empty";
  return "results";
}

/**
 * Live searchable master item picker.
 * Canonical identity: accurate_item_id (+ master_item_id when present).
 * Display text is never authoritative.
 */
export default function MasterItemSearchSelect({
  value = null,
  onChange,
  disabled = false,
  readOnly = false,
  allowClear = true,
  status = "active",
  placeholder = "Cari nama / kode produk…",
  emptyLabel = "Produk tidak ditemukan",
  testId = "master-item-search",
  className = "",
}) {
  const listId = useId();
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const reqSeq = useRef(0);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState([]);
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [highlight, setHighlight] = useState(0);

  const display = open ? query : (value ? formatMasterItemLabel(value) : "");
  const view = listboxViewState({
    pending,
    loading,
    error,
    itemCount: items.length,
  });

  const runSearch = useCallback(async (needle, requestId) => {
    setLoading(true);
    setError("");
    try {
      const response = await api.masterItems({
        q: needle,
        status,
        page: 1,
        page_size: MASTER_ITEM_SEARCH_PAGE_SIZE,
      });
      if (!shouldApplySearchResult(requestId, reqSeq.current)) return;
      setItems(response.items || []);
      setHighlight(0);
      setPending(false);
    } catch (err) {
      if (!shouldApplySearchResult(requestId, reqSeq.current)) return;
      setItems([]);
      setError(err?.response?.data?.detail || err?.message || "Gagal mencari produk");
      setPending(false);
    } finally {
      if (shouldApplySearchResult(requestId, reqSeq.current)) setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    if (!open || disabled || readOnly) return undefined;
    // Invalidate prior query immediately — never show stale options as current results.
    setItems([]);
    setError("");
    setPending(true);
    setLoading(false);
    const requestId = ++reqSeq.current;
    const handle = window.setTimeout(() => {
      runSearch(query.trim(), requestId);
    }, MASTER_ITEM_SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(handle);
      reqSeq.current += 1;
    };
  }, [query, open, disabled, readOnly, runSearch]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const selectItem = (item) => {
    if (!item || disabled || readOnly || pending || loading) return;
    onChange?.(item);
    setQuery("");
    setOpen(false);
    setItems([]);
    setPending(false);
  };

  const clear = () => {
    if (disabled || readOnly || !allowClear) return;
    onChange?.(null);
    setQuery("");
    setItems([]);
    setOpen(false);
    inputRef.current?.focus();
  };

  const onInputFocus = () => {
    if (disabled || readOnly) return;
    setOpen(true);
    setQuery("");
  };

  const onKeyDown = (event) => {
    if (disabled || readOnly) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, Math.max(items.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (event.key === "Enter" && open && view === "results" && items[highlight]) {
      event.preventDefault();
      selectItem(items[highlight]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div
      className={`mis-root ${className}`.trim()}
      ref={rootRef}
      data-testid={testId}
    >
      <div className="mis-control">
        <input
          ref={inputRef}
          className="mis-input"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          disabled={disabled}
          readOnly={readOnly}
          value={display}
          placeholder={placeholder}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            setItems([]);
            setPending(true);
            setError("");
            if (value) onChange?.(null);
          }}
          onFocus={onInputFocus}
          onKeyDown={onKeyDown}
          data-testid={`${testId}-input`}
        />
        {allowClear && value && !disabled && !readOnly && (
          <button
            type="button"
            className="mis-clear"
            onClick={clear}
            aria-label="Hapus pilihan"
            data-testid={`${testId}-clear`}
          >
            ×
          </button>
        )}
      </div>
      {open && !disabled && !readOnly && (
        <ul
          id={listId}
          className="mis-list"
          role="listbox"
          data-testid={`${testId}-list`}
          data-view={view}
        >
          {view === "loading" && (
            <li className="mis-status" data-testid={`${testId}-loading`}>Memuat…</li>
          )}
          {view === "error" && (
            <li className="mis-status mis-error" data-testid={`${testId}-error`}>{error}</li>
          )}
          {view === "empty" && (
            <li className="mis-status" data-testid={`${testId}-empty`}>{emptyLabel}</li>
          )}
          {view === "results" && items.map((item, index) => {
            const key = masterItemOptionKey(item);
            return (
              <li key={key || index}>
                <button
                  type="button"
                  role="option"
                  aria-selected={index === highlight}
                  className={`mis-option ${index === highlight ? "is-active" : ""}`}
                  onMouseEnter={() => setHighlight(index)}
                  onClick={() => selectItem(item)}
                  data-testid={`${testId}-option-${key}`}
                >
                  <span className="mis-option-name">{item.item_name || "—"}</span>
                  <span className="mis-option-meta">
                    {item.item_code ? `${item.item_code} · ` : ""}
                    {item.unit || "—"}
                    {item.accurate_item_id ? ` · ID ${item.accurate_item_id}` : ""}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
