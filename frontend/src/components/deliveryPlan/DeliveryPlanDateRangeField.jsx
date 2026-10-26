import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarDays } from "lucide-react";
import { DayPicker } from "react-day-picker";
import "react-day-picker/dist/style.css";
import {
  formatRangeLabel,
  formatYmdLocal,
  normalizeRangeYmd,
  parseYmdLocal,
} from "./deliveryPlanDateRange";
import "./DeliveryPlanDateRangeField.css";

/**
 * Single-field inclusive date-range picker.
 * Applies only after both ends are chosen. Dismiss discards incomplete draft.
 * Reused by Rencana Jadwal Kirim and Jadwal Pengiriman (label / test ids optional).
 */
export default function DeliveryPlanDateRangeField({
  dateFrom,
  dateTo,
  onApply,
  label = "Tanggal Pengiriman",
  testIdPrefix = "dp-period",
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ from: undefined, to: undefined });
  const wrapRef = useRef(null);
  const applied = Boolean(dateFrom || dateTo);
  const tid = (suffix) => `${testIdPrefix}-${suffix}`;

  const selected = useMemo(() => {
    const from = draft.from;
    const to = draft.to;
    if (from || to) return { from, to };
    return undefined;
  }, [draft]);

  useEffect(() => {
    if (!open) return undefined;
    setDraft({
      from: parseYmdLocal(dateFrom),
      to: parseYmdLocal(dateTo),
    });
    return undefined;
  }, [open, dateFrom, dateTo]);

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const closeWithoutApply = () => setOpen(false);

  const applyComplete = (fromDate, toDate) => {
    const from = formatYmdLocal(fromDate);
    const to = formatYmdLocal(toDate);
    const norm = normalizeRangeYmd(from, to);
    onApply(norm.from, norm.to);
    setOpen(false);
  };

  const onSelect = (range) => {
    const nextFrom = range?.from;
    const nextTo = range?.to;
    setDraft({ from: nextFrom, to: nextTo });
    if (nextFrom && nextTo) {
      applyComplete(nextFrom, nextTo);
    }
  };

  const clearRange = () => {
    setDraft({ from: undefined, to: undefined });
    onApply("", "");
    setOpen(false);
  };

  const now = new Date();
  const fromYear = now.getFullYear() - 10;
  const toYear = now.getFullYear() + 5;

  return (
    <div className="dp-daterange" ref={wrapRef} data-testid={testIdPrefix}>
      <button
        type="button"
        className={`dp-daterange-trigger${applied ? " is-active" : ""}`}
        aria-label={`${label} ${formatRangeLabel(dateFrom, dateTo)}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        data-testid={tid("trigger")}
        onClick={() => setOpen((v) => !v)}
      >
        <CalendarDays size={14} aria-hidden />
        <span className="dp-daterange-label">{label}</span>
        <span className="dp-daterange-value" data-testid={tid("value")}>
          {formatRangeLabel(dateFrom, dateTo)}
        </span>
      </button>
      {open ? (
        <div
          className="dp-daterange-panel"
          role="dialog"
          aria-label={`Pilih rentang ${label}`}
          data-testid={tid("panel")}
        >
          <DayPicker
            mode="range"
            selected={selected}
            onSelect={onSelect}
            numberOfMonths={1}
            captionLayout="dropdown"
            fromYear={fromYear}
            toYear={toYear}
            defaultMonth={draft.from || parseYmdLocal(dateFrom) || now}
            weekStartsOn={1}
            showOutsideDays
            data-testid={`${testIdPrefix}-calendar`}
          />
          <div className="dp-daterange-actions">
            <button
              type="button"
              className="ghost sm"
              data-testid={tid("clear")}
              onClick={clearRange}
            >
              Hapus
            </button>
            <button
              type="button"
              className="ghost sm"
              data-testid={tid("dismiss")}
              onClick={closeWithoutApply}
            >
              Tutup
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
