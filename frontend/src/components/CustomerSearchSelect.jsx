import { useEffect, useId, useMemo, useRef, useState } from "react";
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
 * Customer live search for Pesanan Penjualan create form.
 * Canonical customer_id is set only via explicit result selection.
 * Free typing never silently binds an existing customer.
 */
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
}) {
  const listId = useId();
  const rootRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [filtered, setFiltered] = useState([]);
  const [highlight, setHighlight] = useState(0);

  const display = open ? query : String(value || "");

  useEffect(() => {
    if (!open || disabled) return undefined;
    setPending(true);
    setFiltered([]);
    const handle = window.setTimeout(() => {
      setFiltered(filterActiveMasterCustomers(customers, query));
      setHighlight(0);
      setPending(false);
    }, CUSTOMER_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query, open, disabled, customers]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
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
      setOpen(true);
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

  const status = useMemo(() => {
    if (pending) return "loading";
    if (!filtered.length) return "empty";
    return "results";
  }, [pending, filtered.length]);

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
            setFiltered([]);
            // Free text never binds customer_id — parent must clear selection.
            onTextChange?.(next);
          }}
          onFocus={() => {
            if (disabled) return;
            setOpen(true);
            setQuery(String(value || ""));
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
      {open && !disabled && (
        <ul
          id={listId}
          className="mis-list"
          role="listbox"
          data-testid={`${testId}-list`}
          style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 80 }}
        >
          {status === "loading" && (
            <li className="mis-status" data-testid={`${testId}-loading`}>
              Mencari…
            </li>
          )}
          {status === "empty" && (
            <li className="mis-status" data-testid={`${testId}-empty`}>
              {String(query || "").trim() ? noResultLabel : "Ketik untuk mencari customer aktif"}
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
      )}
    </div>
  );
}
