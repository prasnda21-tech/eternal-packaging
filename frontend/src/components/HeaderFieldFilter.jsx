import { useId, useState } from "react";
import { Filter } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import "./headerSelectFilter.css";

/**
 * Compact header filter for text / date-range / qty-range popovers.
 * Optional onApply commits draft values and closes; Clear resets the column.
 */
export default function HeaderFieldFilter({
  label,
  testid,
  active = false,
  align = "start",
  error = "",
  onClear,
  onApply,
  applyDisabled = false,
  children,
  className = "",
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

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
            aria-haspopup="dialog"
            aria-controls={open ? panelId : undefined}
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
          className="hsf-th-popover hsf-th-popover-form"
          onClick={(e) => e.stopPropagation()}
          onOpenAutoFocus={(e) => {
            const first = e.currentTarget.querySelector("input, select, button");
            if (first) {
              e.preventDefault();
              first.focus();
            }
          }}
        >
          <div id={panelId} className="hsf-th-form" data-testid={`${testid}-panel`}>
            <div className="hsf-th-pop-label">{label}</div>
            {children}
            {error ? (
              <div className="hsf-th-error" role="alert" data-testid={`${testid}-error`}>
                {error}
              </div>
            ) : null}
            <div className="hsf-th-form-actions">
              {onApply ? (
                <button
                  type="button"
                  className="hsf-th-apply"
                  data-testid={`${testid}-apply`}
                  disabled={!!applyDisabled}
                  onClick={() => {
                    onApply();
                    if (!applyDisabled) setOpen(false);
                  }}
                >
                  Terapkan
                </button>
              ) : null}
              {active ? (
                <button
                  type="button"
                  className="hsf-th-clear"
                  data-testid={`${testid}-clear`}
                  onClick={() => {
                    onClear?.();
                    setOpen(false);
                  }}
                >
                  Hapus filter
                </button>
              ) : null}
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
