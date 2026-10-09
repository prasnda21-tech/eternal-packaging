import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  formatSalesInvoiceDate,
  readSalesInvoiceDateInput,
  salesInvoiceDateToIso,
} from "../lib/salesInvoiceDate";

/**
 * Manual DD/MM/YYYY plus the native calendar.
 * Parent ISO is cleared as soon as the visible text is empty or invalid.
 */
const SalesInvoiceDateField = forwardRef(function SalesInvoiceDateField({
  valueIso,
  onChange,
  disabled = false,
  testId,
  calendarLabel = "Kalender tanggal faktur",
}, ref) {
  const [text, setText] = useState(() => formatSalesInvoiceDate(valueIso));
  const [error, setError] = useState("");
  const textRef = useRef(text);
  textRef.current = text;

  useImperativeHandle(ref, () => ({
    read() {
      const result = readSalesInvoiceDateInput(textRef.current);
      setError(result.ok ? "" : result.message);
      if (result.ok) {
        setText(formatSalesInvoiceDate(result.iso));
        if (result.iso !== (valueIso || "")) onChange(result.iso);
      } else if (valueIso) {
        onChange("");
      }
      return result;
    },
  }), [onChange, valueIso]);

  useEffect(() => {
    const current = readSalesInvoiceDateInput(textRef.current);
    if (valueIso && current.iso === valueIso) {
      setError("");
      return;
    }
    if (!valueIso && !current.ok) {
      return;
    }
    setText(valueIso ? formatSalesInvoiceDate(valueIso) : "");
    setError("");
  }, [valueIso]);

  const publish = (nextText) => {
    const result = readSalesInvoiceDateInput(nextText);
    setError(result.ok ? "" : result.message);
    if (result.ok) {
      setText(formatSalesInvoiceDate(result.iso));
      if (result.iso !== (valueIso || "")) onChange(result.iso);
      return;
    }
    setText(nextText);
    if (valueIso) onChange("");
  };

  const isoValue = salesInvoiceDateToIso(valueIso);

  return (
    <div className="fp-invoice-date">
      <input
        type="text"
        inputMode="numeric"
        autoComplete="off"
        placeholder="DD/MM/YYYY"
        value={text}
        disabled={disabled}
        data-testid={testId}
        aria-invalid={error ? "true" : "false"}
        onChange={(event) => publish(event.target.value)}
        onBlur={() => {
          const result = readSalesInvoiceDateInput(textRef.current);
          if (result.ok) setText(formatSalesInvoiceDate(result.iso));
        }}
      />
      <input
        type="date"
        value={isoValue}
        disabled={disabled}
        aria-label={calendarLabel}
        data-testid={testId ? `${testId}-calendar` : undefined}
        onChange={(event) => publish(event.target.value)}
      />
      {error ? <span className="fp-invoice-date-error">{error}</span> : null}
    </div>
  );
});

export default SalesInvoiceDateField;
