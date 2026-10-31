import { useEffect, useId, useRef, useState } from "react";
import {
  formatSalesInvoiceDate,
  readSalesInvoiceDateInput,
  salesInvoiceDateToIso,
} from "../../lib/salesInvoiceDate";
import "./SalesDashboardDateInput.css";

/**
 * One visible DD/MM/YYYY value plus an icon-only native calendar picker.
 * The picker keeps YYYY-MM-DD for the parent; its locale text is hidden.
 */
export default function SalesDashboardDateInput({
  valueIso,
  onChange,
  ariaLabel,
  testId,
}) {
  const labelId = useId();
  const calendarRef = useRef(null);
  const [text, setText] = useState(() => formatSalesInvoiceDate(valueIso));
  const [error, setError] = useState("");

  useEffect(() => {
    const current = salesInvoiceDateToIso(text);
    if (valueIso && current === valueIso) return;
    setText(formatSalesInvoiceDate(valueIso));
    setError("");
  }, [valueIso]); // eslint-disable-line react-hooks/exhaustive-deps

  const publishText = (nextText) => {
    setText(nextText);
    const trimmed = nextText.trim();
    if (!trimmed) {
      setError("");
      return;
    }
    const result = readSalesInvoiceDateInput(trimmed);
    if (result.ok) {
      setError("");
      setText(formatSalesInvoiceDate(result.iso));
      if (result.iso !== (valueIso || "")) onChange(result.iso);
      return;
    }
    setError(trimmed.length >= 10 ? result.message : "");
  };

  const onBlur = () => {
    const trimmed = text.trim();
    if (!trimmed) {
      setText(formatSalesInvoiceDate(valueIso));
      setError("");
      return;
    }
    const result = readSalesInvoiceDateInput(trimmed);
    if (result.ok) {
      setError("");
      setText(formatSalesInvoiceDate(result.iso));
      if (result.iso !== (valueIso || "")) onChange(result.iso);
      return;
    }
    setError(result.message);
  };

  const pickCalendar = (raw) => {
    if (!raw) return;
    const result = readSalesInvoiceDateInput(raw);
    if (!result.ok) {
      setError(result.message);
      return;
    }
    setError("");
    setText(formatSalesInvoiceDate(result.iso));
    if (result.iso !== (valueIso || "")) onChange(result.iso);
  };

  const openCalendar = () => {
    const input = calendarRef.current;
    if (!input) return;
    if (typeof input.showPicker === "function") {
      try {
        input.showPicker();
        return;
      } catch {
        /* fall through to click */
      }
    }
    input.click();
  };

  return (
    <span className="sd-date">
      <span className="sd-date-label" id={labelId}>{ariaLabel}</span>
      <span className="sd-date-control">
        <input
          type="text"
          inputMode="numeric"
          autoComplete="off"
          placeholder="DD/MM/YYYY"
          aria-labelledby={labelId}
          value={text}
          data-testid={testId}
          aria-invalid={error ? "true" : "false"}
          onChange={(event) => publishText(event.target.value)}
          onBlur={onBlur}
        />
        <button
          type="button"
          className="sd-date-trigger"
          aria-label={`Buka kalender ${ariaLabel}`}
          data-testid={testId ? `${testId}-trigger` : undefined}
          onClick={openCalendar}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <rect x="3" y="5" width="18" height="16" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
            <path d="M8 3v4M16 3v4M3 10h18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
        <input
          ref={calendarRef}
          type="date"
          tabIndex={-1}
          value={salesInvoiceDateToIso(valueIso)}
          aria-hidden="true"
          data-testid={testId ? `${testId}-calendar` : undefined}
          className="sd-date-calendar"
          onChange={(event) => pickCalendar(event.target.value)}
        />
      </span>
      {error ? <span className="sd-date-error" role="alert">{error}</span> : null}
    </span>
  );
}
