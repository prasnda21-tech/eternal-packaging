import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import api from "../lib/api";
import "./masterItemSearchSelect.css";

export const MASTER_ITEM_SEARCH_DEBOUNCE_MS = 300;
export const MASTER_ITEM_SEARCH_PAGE_SIZE = 40;
export const MASTER_ITEM_LIST_VISIBLE_ROWS = 5;
export const MASTER_ITEM_LIST_MAX_PX = 300;

/**
 * Viewport overlay box for the item list.
 * Opens upward when the space below the input cannot hold the list.
 */
export function placeMasterItemListBox(rect, viewport, measuredHeight = MASTER_ITEM_LIST_MAX_PX) {
  if (!rect || !viewport) return null;
  const gutter = 8;
  const gap = 4;
  const vw = Number(viewport.width) || 0;
  const vh = Number(viewport.height) || 0;
  const minWidth = Math.min(280, Math.max(vw - gutter * 2, 0));
  const width = Math.min(
    Math.max(Number(rect.width) || 0, minWidth),
    Math.max(vw - gutter * 2, 0),
  );
  const maxLeft = Math.max(gutter, vw - width - gutter);
  const left = Math.min(Math.max(gutter, Number(rect.left) || gutter), maxLeft);
  const spaceBelow = Math.max(0, vh - Number(rect.bottom) - gutter);
  const spaceAbove = Math.max(0, Number(rect.top) - gutter);
  const measured = Number(measuredHeight);
  const content = Number.isFinite(measured) && measured > 0 ? measured : MASTER_ITEM_LIST_MAX_PX;
  const openBelow = spaceBelow >= Math.min(MASTER_ITEM_LIST_MAX_PX, 160) || spaceBelow >= spaceAbove;
  const available = Math.max(0, (openBelow ? spaceBelow : spaceAbove) - gap);
  const maxHeight = Math.max(0, Math.min(MASTER_ITEM_LIST_MAX_PX, available));
  const usedHeight = Math.min(maxHeight, content);
  const top = openBelow
    ? Number(rect.bottom) + gap
    : Math.max(gutter, Number(rect.top) - gap - usedHeight);
  return {
    top: Math.round(top),
    left: Math.round(left),
    width: Math.round(width),
    maxHeight: Math.round(maxHeight),
    placement: openBelow ? "below" : "above",
  };
}

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

export function formatMasterItemSearchError(err) {
  if (Number(err?.response?.status) === 502) {
    return "Gagal memuat daftar barang. Coba lagi.";
  }
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  return err?.message || "Gagal mencari produk";
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
  /** Optional async ({ q, status, page_size }) => { items }. Defaults to inventory picker API. */
  fetchItems = null,
}) {
  const listId = useId();
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const reqSeq = useRef(0);
  const [listBox, setListBox] = useState(null);
  const [overModal, setOverModal] = useState(false);
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
      const params = {
        q: needle,
        status,
        page_size: MASTER_ITEM_SEARCH_PAGE_SIZE,
      };
      const response = typeof fetchItems === "function"
        ? await fetchItems(params)
        : await api.masterItemsPicker(params);
      if (!shouldApplySearchResult(requestId, reqSeq.current)) return;
      setItems(response.items || []);
      setHighlight(0);
      setPending(false);
    } catch (err) {
      if (!shouldApplySearchResult(requestId, reqSeq.current)) return;
      setItems([]);
      setError(formatMasterItemSearchError(err));
      setPending(false);
    } finally {
      if (shouldApplySearchResult(requestId, reqSeq.current)) setLoading(false);
    }
  }, [status, fetchItems]);

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

  const placeList = useCallback(() => {
    const anchor = rootRef.current;
    if (!anchor || !anchor.isConnected || typeof window === "undefined") {
      setOpen(false);
      setListBox(null);
      return;
    }
    const nextOverModal = Boolean(anchor.closest?.(".modal"));
    setOverModal((prev) => (prev === nextOverModal ? prev : nextOverModal));
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
        && prev.placement === next.placement
      ) {
        return prev;
      }
      return next;
    });
  }, []);

  useLayoutEffect(() => {
    if (!open || disabled || readOnly) {
      setListBox(null);
      setOverModal(false);
      return undefined;
    }
    placeList();
    window.addEventListener("resize", placeList);
    window.addEventListener("scroll", placeList, true);
    return () => {
      window.removeEventListener("resize", placeList);
      window.removeEventListener("scroll", placeList, true);
    };
  }, [open, disabled, readOnly, placeList, view, items.length]);

  useLayoutEffect(() => {
    if (!open || disabled || readOnly || !listBox) return undefined;
    placeList();
    return undefined;
  }, [open, disabled, readOnly, listBox, placeList]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event) => {
      const t = event.target;
      if (rootRef.current?.contains(t) || listRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    if (!open || view !== "results") return;
    const active = listRef.current?.querySelector(".mis-option.is-active");
    active?.scrollIntoView({ block: "nearest" });
  }, [highlight, open, view]);

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
      {open && !disabled && !readOnly && listBox && createPortal(
        <ul
          id={listId}
          ref={listRef}
          className={`mis-list${overModal ? " mis-list-over-modal" : ""}`}
          role="listbox"
          data-testid={`${testId}-list`}
          data-view={view}
          data-overlay="portal"
          data-placement={listBox.placement}
          data-visible-rows={MASTER_ITEM_LIST_VISIBLE_ROWS}
          style={{
            top: listBox.top,
            left: listBox.left,
            width: listBox.width,
            maxHeight: listBox.maxHeight,
          }}
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
        </ul>,
        document.body,
      )}
    </div>
  );
}
