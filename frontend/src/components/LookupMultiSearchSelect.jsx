import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { filterFacetOptions, listboxViewState } from "./FacetSearchSelect";
import { placeMasterItemListBox } from "./MasterItemSearchSelect";
import "./masterItemSearchSelect.css";

export const LOOKUP_MULTI_SEARCH_DEBOUNCE_MS = 150;

/**
 * Searchable multi-select lookup for create forms.
 * Selected ids are stable across query changes; typing never invents identities.
 */
export default function LookupMultiSearchSelect({
  options = [],
  values = [],
  onChange,
  getOptionValue,
  getOptionLabel,
  getOptionMeta,
  getSearchText,
  placeholder = "Ketik untuk mencari…",
  noResultLabel = "Tidak ada hasil yang cocok",
  disabled = false,
  testId = "lookup-multi-search",
  className = "",
  fetchOptions = null,
  isOptionDisabled = null,
  getOptionDisabledReason = null,
  selectedCountLabel = null,
}) {
  const listId = useId();
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const reqSeq = useRef(0);
  const gettersRef = useRef({
    getOptionValue,
    getOptionLabel,
    getOptionMeta,
    getSearchText,
    isOptionDisabled,
    getOptionDisabledReason,
  });
  gettersRef.current = {
    getOptionValue,
    getOptionLabel,
    getOptionMeta,
    getSearchText,
    isOptionDisabled,
    getOptionDisabledReason,
  };

  const selectedIds = useMemo(
    () => (Array.isArray(values) ? values.map((v) => String(v)).filter(Boolean) : []),
    [values],
  );
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [filtered, setFiltered] = useState(options || []);
  const [highlight, setHighlight] = useState(0);
  const [listBox, setListBox] = useState(null);
  const [knownOptions, setKnownOptions] = useState(options || []);

  useEffect(() => {
    if (typeof fetchOptions === "function") return;
    setKnownOptions(options || []);
  }, [options, fetchOptions]);

  const resolveValue = useCallback((opt) => {
    const fn = gettersRef.current.getOptionValue;
    return String(fn?.(opt) || "");
  }, []);

  const selectedOptions = useMemo(() => {
    const pool = [...(knownOptions || []), ...(options || [])];
    const map = new Map();
    pool.forEach((opt) => {
      const key = resolveValue(opt);
      if (key) map.set(key, opt);
    });
    return selectedIds.map((id) => map.get(id) || { __id: id }).filter(Boolean);
  }, [knownOptions, options, selectedIds, resolveValue]);

  const asyncMode = typeof fetchOptions === "function";
  const countLabel = typeof selectedCountLabel === "function"
    ? selectedCountLabel(selectedIds.length)
    : (selectedIds.length ? `${selectedIds.length} PO terpilih` : "");

  useEffect(() => {
    if (!open || disabled) return undefined;
    setPending(true);
    setFiltered([]);
    const requestId = ++reqSeq.current;
    const handle = window.setTimeout(async () => {
      const {
        getOptionLabel: labelFn,
        getSearchText: searchFn,
      } = gettersRef.current;
      try {
        if (asyncMode) {
          const rows = await fetchOptions(query);
          if (requestId !== reqSeq.current) return;
          const list = Array.isArray(rows) ? rows : [];
          setKnownOptions((prev) => {
            const map = new Map();
            [...(prev || []), ...list, ...selectedOptions].forEach((row) => {
              const key = resolveValue(row);
              if (key) map.set(key, row);
            });
            return Array.from(map.values());
          });
          setFiltered(list);
        } else {
          const resolveSearch = searchFn || labelFn;
          setFiltered(filterFacetOptions(options, query, resolveSearch));
        }
        setHighlight(0);
        setPending(false);
      } catch {
        if (requestId !== reqSeq.current) return;
        setFiltered([]);
        setPending(false);
      }
    }, LOOKUP_MULTI_SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(handle);
      reqSeq.current += 1;
    };
  }, [
    query,
    open,
    disabled,
    asyncMode,
    fetchOptions,
    asyncMode ? null : options,
    resolveValue,
    selectedOptions,
  ]);

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
  }, [open, disabled, placeList, filtered, pending, selectedIds.length]);

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

  const optionDisabled = (opt) => {
    const fn = gettersRef.current.isOptionDisabled;
    return typeof fn === "function" ? Boolean(fn(opt)) : false;
  };

  const emit = (nextIds) => {
    const unique = Array.from(new Set(nextIds.map((id) => String(id)).filter(Boolean)));
    onChange?.(unique);
  };

  const toggle = (opt) => {
    if (!opt || optionDisabled(opt)) return;
    const id = resolveValue(opt);
    if (!id) return;
    setKnownOptions((prev) => {
      const map = new Map((prev || []).map((row) => [resolveValue(row), row]));
      map.set(id, opt);
      return Array.from(map.values());
    });
    if (selectedSet.has(id)) {
      emit(selectedIds.filter((x) => x !== id));
    } else {
      emit([...selectedIds, id]);
    }
  };

  const removeId = (id) => {
    emit(selectedIds.filter((x) => x !== String(id)));
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
      if (!pending && filtered[highlight]) toggle(filtered[highlight]);
    } else if (event.key === "Backspace" && !query && selectedIds.length) {
      event.preventDefault();
      removeId(selectedIds[selectedIds.length - 1]);
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
      className="mis-list ux90-lookup-list"
      role="listbox"
      aria-multiselectable="true"
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
        const {
          getOptionLabel: labelFn,
          getOptionMeta: metaFn,
          getOptionDisabledReason: reasonFn,
        } = gettersRef.current;
        const key = resolveValue(opt) || String(index);
        const active = highlight === index;
        const checked = selectedSet.has(key);
        const blocked = optionDisabled(opt);
        const meta = metaFn ? metaFn(opt) : "";
        const reason = blocked && typeof reasonFn === "function" ? reasonFn(opt) : "";
        return (
          <li key={key}>
            <button
              type="button"
              role="option"
              aria-selected={checked}
              aria-disabled={blocked ? "true" : undefined}
              disabled={blocked}
              className={`mis-option${active ? " is-active" : ""}${checked ? " is-selected" : ""}${blocked ? " is-disabled" : ""}`}
              onMouseEnter={() => setHighlight(index)}
              onClick={() => toggle(opt)}
              data-testid={`${testId}-option-${key}`}
            >
              <span className="ux90-lookup-multi-check" aria-hidden="true">{checked ? "✓" : ""}</span>
              <span className="mis-option-name">{labelFn(opt)}</span>
              {meta ? <span className="mis-option-meta">{meta}</span> : null}
              {reason ? <span className="mis-option-meta ux90-lookup-disabled-reason">{reason}</span> : null}
            </button>
          </li>
        );
      })}
    </ul>
  ) : null;

  return (
    <div
      className={`mis-root ux90-lookup ux90-lookup-multi${open ? " is-open" : ""}${className ? ` ${className}` : ""}`.trim()}
      ref={rootRef}
      data-testid={`${testId}-root`}
    >
      <div className="mis-control ux90-lookup-multi-control">
        <span className="ux90-lookup-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" strokeLinecap="round" />
          </svg>
        </span>
        <div className="ux90-lookup-multi-chips" data-testid={`${testId}-chips`}>
          {selectedOptions.map((opt) => {
            const id = opt.__id || resolveValue(opt);
            const label = opt.__id
              ? id
              : (gettersRef.current.getOptionLabel?.(opt) || id);
            return (
              <button
                key={id}
                type="button"
                className="ux90-lookup-chip"
                disabled={disabled}
                onClick={(e) => {
                  e.stopPropagation();
                  removeId(id);
                }}
                data-testid={`${testId}-chip-${id}`}
                title="Hapus pilihan"
              >
                <span>{label}</span>
                <span aria-hidden="true">×</span>
              </button>
            );
          })}
          <input
            className="mis-input ux90-lookup-input ux90-lookup-multi-input"
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-multiselectable="true"
            disabled={disabled}
            value={open ? query : (selectedIds.length ? "" : "")}
            placeholder={selectedIds.length ? (open ? placeholder : countLabel) : placeholder}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
            onKeyDown={onKeyDown}
            data-testid={testId}
            autoComplete="off"
          />
        </div>
        {!open && selectedIds.length ? (
          <span className="ux90-lookup-multi-count" data-testid={`${testId}-count`}>{countLabel}</span>
        ) : null}
      </div>
      {typeof document !== "undefined" && listNode ? createPortal(listNode, document.body) : null}
    </div>
  );
}
