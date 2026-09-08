import { useEffect, useRef, useState } from "react";
import { ChevronDown, Search } from "lucide-react";
import { productionScheduleSummaryLines } from "../lib/productionScheduleSummary";

export function ProductionScheduleSearchCluster({
  value,
  onChange,
  inputTestId,
  searchButtonTestId,
  placeholder = "Cari PO / Customer / Outlet",
}) {
  return (
    <div className="production-schedule-search-cluster">
      <div className="production-schedule-search-field">
        <input
          placeholder={placeholder}
          value={value}
          onChange={onChange}
          data-testid={inputTestId}
        />
        <button
          type="button"
          className="production-schedule-search-btn"
          tabIndex={-1}
          aria-label="Cari"
          data-testid={searchButtonTestId}
        >
          <Search size={13} />
        </button>
      </div>
    </div>
  );
}

export default function ProductionScheduleRingkasan({ metrics, testidPrefix = "prod" }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const lines = productionScheduleSummaryLines(metrics);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="production-schedule-ringkasan" ref={wrapRef}>
      <button
        type="button"
        className="production-schedule-ringkasan-btn"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        data-testid={`${testidPrefix}-ringkasan`}
      >
        Ringkasan
        <ChevronDown size={12} />
      </button>
      {open ? (
        <div className="production-schedule-ringkasan-panel" data-testid={`${testidPrefix}-ringkasan-panel`}>
          {lines.map((line) => (
            <div key={line.key} className="production-schedule-ringkasan-row" data-testid={`${testidPrefix}-ringkasan-${line.key}`}>
              <span>{line.label}</span>
              <strong>{line.value}</strong>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
