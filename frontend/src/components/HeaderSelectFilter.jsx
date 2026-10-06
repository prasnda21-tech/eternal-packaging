import { useCallback, useId, useState } from "react";
import { Filter } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import "./headerSelectFilter.css";

/**
 * Compact table-header select filter.
 * Single-select applies immediately and closes.
 * Multi-select toggles without closing; clear resets.
 */
export default function HeaderSelectFilter({
  label,
  testid,
  options = [],
  value,
  multi = false,
  emptyValue = "all",
  onChange,
  align = "start",
  className = "",
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();

  const selectedMulti = multi
    ? (Array.isArray(value) ? value : [])
    : [];
  const active = multi
    ? selectedMulti.length > 0
    : String(value ?? "") !== "" && String(value) !== String(emptyValue);

  const handleSelect = useCallback((optionValue) => {
    if (multi) {
      const current = Array.isArray(value) ? value : [];
      const exists = current.includes(optionValue);
      const next = exists
        ? current.filter((item) => item !== optionValue)
        : [...current, optionValue];
      onChange?.(next);
      return;
    }
    onChange?.(optionValue);
    setOpen(false);
  }, [multi, onChange, value]);

  const handleClear = useCallback(() => {
    if (multi) onChange?.([]);
    else onChange?.(emptyValue);
    setOpen(false);
  }, [emptyValue, multi, onChange]);

  return (
    <div className={`hsf-th-inner${className ? ` ${className}` : ""}`}>
      <span className="hsf-th-label">{label}</span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className={"hsf-th-filter" + (active ? " is-active" : "")}
            aria-label={`Filter ${label}`}
            aria-expanded={open ? "true" : "false"}
            aria-haspopup="listbox"
            aria-controls={open ? listId : undefined}
            data-testid={testid}
            onClick={(e) => e.stopPropagation()}
          >
            <Filter size={11} aria-hidden="true" />
            {active ? <span className="hsf-th-dot" data-testid={`${testid}-active-dot`} /> : null}
          </button>
        </PopoverTrigger>
        <PopoverContent
          align={align}
          sideOffset={6}
          collisionPadding={12}
          className="hsf-th-popover"
          onClick={(e) => e.stopPropagation()}
          onOpenAutoFocus={(e) => {
            const first = e.currentTarget.querySelector('[role="option"], button, input');
            if (first) {
              e.preventDefault();
              first.focus();
            }
          }}
        >
          <div className="hsf-th-pop-label" id={`${listId}-label`}>{label}</div>
          <div
            id={listId}
            className="hsf-th-options"
            role="listbox"
            aria-labelledby={`${listId}-label`}
            aria-multiselectable={multi ? "true" : undefined}
          >
            {options.map((option) => {
              const optionValue = option.value;
              const selected = multi
                ? selectedMulti.includes(optionValue)
                : String(value) === String(optionValue);
              return (
                <button
                  key={String(optionValue)}
                  type="button"
                  role="option"
                  aria-selected={selected ? "true" : "false"}
                  className={"hsf-th-option" + (selected ? " is-selected" : "")}
                  data-testid={`${testid}-option-${String(optionValue).replace(/\s+/g, "-")}`}
                  onClick={() => handleSelect(optionValue)}
                >
                  {multi ? (
                    <span className={"hsf-th-check" + (selected ? " is-on" : "")} aria-hidden="true" />
                  ) : null}
                  <span>{option.label}</span>
                </button>
              );
            })}
          </div>
          {active ? (
            <button
              type="button"
              className="hsf-th-clear"
              data-testid={`${testid}-clear`}
              onClick={handleClear}
            >
              Hapus filter
            </button>
          ) : null}
        </PopoverContent>
      </Popover>
    </div>
  );
}
