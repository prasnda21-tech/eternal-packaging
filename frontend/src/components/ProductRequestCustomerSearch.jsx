import { useEffect, useId, useRef, useState } from "react";
import api from "../lib/api";
import { isActiveMasterCustomer } from "./CustomerSearchSelect";
import "./masterItemSearchSelect.css";

export const PRODUCT_REQUEST_CUSTOMER_SEARCH_DEBOUNCE_MS = 200;

export default function ProductRequestCustomerSearch({
  value = "",
  selectedId = null,
  onSelectCustomer,
  onClear,
  disabled = false,
  testId = "prq-customer-search",
}) {
  const listId = useId();
  const rootRef = useRef(null);
  const reqSeq = useRef(0);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const display = open ? query : String(value || "");

  useEffect(() => {
    if (!open || disabled) return undefined;
    setPending(true);
    setError("");
    setItems([]);
    const requestId = ++reqSeq.current;
    const handle = window.setTimeout(async () => {
      try {
        const data = await api.productRequestCustomers({ q: query.trim() });
        if (requestId !== reqSeq.current) return;
        setItems((data.customers || []).filter(isActiveMasterCustomer));
      } catch (err) {
        if (requestId !== reqSeq.current) return;
        setItems([]);
        setError(err?.response?.data?.detail || err?.message || "Gagal mencari customer");
      } finally {
        if (requestId === reqSeq.current) setPending(false);
      }
    }, PRODUCT_REQUEST_CUSTOMER_SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(handle);
      reqSeq.current += 1;
    };
  }, [query, open, disabled]);

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

  return (
    <div className="master-item-search" ref={rootRef} data-testid={testId}>
      <input
        className="master-item-search-input"
        value={display}
        disabled={disabled}
        placeholder="Cari nama / kode customer…"
        aria-controls={listId}
        aria-expanded={open}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
      />
      {(selectedId || value) && !disabled && (
        <button
          type="button"
          className="master-item-search-clear"
          data-testid={`${testId}-clear`}
          onClick={() => {
            onClear?.();
            setQuery("");
            setOpen(false);
          }}
        >
          Hapus
        </button>
      )}
      {open && !disabled && (
        <div className="master-item-search-list" id={listId} role="listbox" data-testid={`${testId}-list`}>
          {pending && <div className="master-item-search-empty">Mencari…</div>}
          {!pending && error && <div className="master-item-search-empty">{error}</div>}
          {!pending && !error && items.length === 0 && (
            <div className="master-item-search-empty">Ketik untuk mencari customer master</div>
          )}
          {!pending && items.map((customer) => (
            <button
              type="button"
              key={customer.id}
              role="option"
              className="master-item-search-option"
              data-testid={`${testId}-option-${customer.id}`}
              onClick={() => pick(customer)}
            >
              <strong>{customer.name}</strong>
              <span>{[customer.id, customer.pic, customer.phone, customer.city].filter(Boolean).join(" · ")}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
