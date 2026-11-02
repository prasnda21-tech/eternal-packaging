import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { placeMasterItemListBox } from "./MasterItemSearchSelect";
import "./masterItemSearchSelect.css";

export const CUSTOMER_SEARCH_DEBOUNCE_MS = 150;

/** Active master only — missing/null is_active means active (legacy). */
export function isActiveMasterCustomer(customer) {
  if (!customer || typeof customer !== "object") return false;
  if (!customer.id) return false;
  if (!String(customer.name || "").trim()) return false;
  if (!("is_active" in customer) || customer.is_active == null) return true;
  return Boolean(customer.is_active);
}

export function customerSearchHaystack(customer) {
  if (!customer) return "";
  return [customer.name, customer.phone, customer.city, customer.pic]
    .map((v) => String(v || "").trim().toLowerCase())
    .filter(Boolean)
    .join(" ");
}

export function formatCustomerOptionMeta(customer) {
  if (!customer) return "";
  const bits = [customer.phone, customer.city].map((v) => String(v || "").trim()).filter(Boolean);
  return bits.join(" · ");
}

export function filterActiveMasterCustomers(customers, query) {
  const active = (customers || []).filter(isActiveMasterCustomer);
  const q = String(query || "").trim().toLowerCase();
  if (!q) return active;
  return active.filter((c) => customerSearchHaystack(c).includes(q));
}

/**
 * Customer live search for Pesanan Penjualan / Pra-PO create forms.
 * Canonical customer_id is set only via explicit result selection.
 * Free typing never silently binds an existing customer.
 * Focus/click shows active masters immediately — typing is not required to see the list.
 */
export const CUSTOMER_OPTIONS_LOAD_ERROR = "Gagal memuat daftar pelanggan.";

export default function CustomerSearchSelect({
  customers = [],
  value = "",
  selectedId = null,
  onTextChange,
  onSelectCustomer,
  onClear,
  disabled = false,
  placeholder = "Ketik nama customer…",
  noResultLabel = "Customer tidak ditemukan",
  testId = "input-customer",
  className = "",
  required = false,
  /** True while salesOrderOptions (or equivalent) is loading masters. */
  customersLoading = false,
  /** Distinct from empty search — set when master list failed to load. */
  loadError = "",
  /** Retry via the page's existing options loader. */
  onRetryLoad = null,
}) {
  const listId = useId();
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [filtered, setFiltered] = useState([]);
  const [highlight, setHighlight] = useState(0);
  const [listBox, setListBox] = useState(null);
  const [overModal, setOverModal] = useState(false);

  const display = open ? query : String(value || "");

  const applyFilterNow = useCallback((nextQuery) => {
    const next = filterActiveMasterCustomers(customers, nextQuery);
    setFiltered(next);
    setHighlight(0);
    setPending(false);
    return next;
  }, [customers]);

  const openList = useCallback((nextQuery) => {
    const q = String(nextQuery || "");
    setQuery(q);
    setOpen(true);
    // Sync first paint from available masters — never flash false empty before debounce.
    applyFilterNow(q);
  }, [applyFilterNow]);

  useEffect(() => {
    if (!open || disabled) return undefined;
    const q = String(query || "").trim();
    // Empty query: keep the full active list in sync immediately (focus / clear / customers load).
    if (!q) {
      applyFilterNow(query);
      return undefined;
    }
    // Typing: debounce filter only.
    setPending(true);
    const handle = window.setTimeout(() => {
      applyFilterNow(query);
    }, CUSTOMER_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query, open, disabled, customers, applyFilterNow]);

  const placeList = useCallback(() => {
    const anchor = rootRef.current;
    if (!anchor || !anchor.isConnected || typeof window === "undefined") {
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
    if (!open || disabled) {
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
  }, [open, disabled, placeList, filtered.length, pending]);

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

  const pick = (customer) => {
    if (!customer?.id) return;
    onSelectCustomer?.(customer);
    setQuery("");
    setOpen(false);
  };

  const clear = () => {
    onClear?.();
    setQuery("");
    setOpen(false);
  };

  const onKeyDown = (event) => {
    if (disabled) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!open) openList(String(value || ""));
      else setOpen(true);
      setHighlight((h) => Math.min(h + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (event.key === "Enter" && open) {
      event.preventDefault();
      if (!pending && filtered[highlight]) pick(filtered[highlight]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  const showClear = Boolean(String(value || "").trim() || selectedId) && !disabled;
  const loadErrorMessage = String(loadError || "").trim();

  const status = useMemo(() => {
    if (customersLoading) return "loading-customers";
    if (pending) return "loading";
    if (filtered.length) return "results";
    // API failure must not reuse the empty-search copy.
    if (loadErrorMessage) return "error";
    return "empty";
  }, [customersLoading, pending, filtered.length, loadErrorMessage]);

  const retry = (event) => {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    if (disabled || typeof onRetryLoad !== "function") return;
    onRetryLoad();
  };

  const listNode = open && !disabled && listBox ? (
    <ul
      id={listId}
      ref={listRef}
      className={`mis-list${overModal ? " mis-list-over-modal" : ""}`}
      role="listbox"
      data-testid={`${testId}-list`}
      data-overlay="portal"
      data-placement={listBox.placement}
      style={{
        top: listBox.top,
        left: listBox.left,
        width: listBox.width,
        maxHeight: listBox.maxHeight,
      }}
    >
      {status === "loading-customers" && (
        <li className="mis-status" data-testid={`${testId}-loading`}>
          Memuat daftar pelanggan…
        </li>
      )}
      {status === "loading" && (
        <li className="mis-status" data-testid={`${testId}-loading`}>
          Mencari…
        </li>
      )}
      {status === "error" && (
        <li className="mis-status mis-error" data-testid={`${testId}-load-error`}>
          <span>{loadErrorMessage || CUSTOMER_OPTIONS_LOAD_ERROR}</span>
          {typeof onRetryLoad === "function" ? (
            <button
              type="button"
              className="mis-retry"
              onClick={retry}
              data-testid={`${testId}-retry`}
            >
              Coba lagi
            </button>
          ) : null}
        </li>
      )}
      {status === "empty" && (
        <li className="mis-status" data-testid={`${testId}-empty`}>
          {noResultLabel}
        </li>
      )}
      {status === "results" &&
        filtered.map((customer, index) => {
          const key = String(customer.id);
          const active = highlight === index;
          const meta = formatCustomerOptionMeta(customer);
          return (
            <li key={key}>
              <button
                type="button"
                role="option"
                aria-selected={active}
                className={`mis-option ${active ? "is-active" : ""}`}
                onMouseEnter={() => setHighlight(index)}
                onClick={() => pick(customer)}
                data-testid={`${testId}-option-${key}`}
              >
                <span className="mis-option-name">{customer.name}</span>
                {meta ? <span className="mis-option-meta">{meta}</span> : null}
              </button>
            </li>
          );
        })}
    </ul>
  ) : null;

  return (
    <div className={`mis-root ${className}`.trim()} ref={rootRef} data-testid={`${testId}-root`}>
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
          required={required}
          autoComplete="off"
          onChange={(event) => {
            const next = event.target.value;
            setQuery(next);
            setOpen(true);
            setPending(true);
            // Free text never binds customer_id — parent must clear selection.
            onTextChange?.(next);
          }}
          onFocus={() => {
            if (disabled) return;
            openList(String(value || ""));
          }}
          onKeyDown={onKeyDown}
          data-testid={testId}
        />
        {showClear && (
          <button
            type="button"
            className="mis-clear"
            aria-label="Hapus customer"
            onClick={clear}
            data-testid={`${testId}-clear`}
          >
            ×
          </button>
        )}
      </div>
      {loadErrorMessage && !disabled ? (
        <div className="mis-load-error-hint" data-testid={`${testId}-load-error-hint`}>
          <span>{loadErrorMessage}</span>
          {typeof onRetryLoad === "function" ? (
            <button
              type="button"
              className="mis-retry"
              onClick={retry}
              disabled={customersLoading}
              data-testid={`${testId}-retry-inline`}
            >
              Coba lagi
            </button>
          ) : null}
        </div>
      ) : null}
      {typeof document !== "undefined" && listNode
        ? createPortal(listNode, document.body)
        : null}
    </div>
  );
}
