import { useCallback, useId, useState } from "react";
import { Filter } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import "./headerSelectFilter.css";

function isGroupActive(group) {
  const empty = group.emptyValue ?? "all";
  return String(group.value ?? "") !== "" && String(group.value) !== String(empty);
}

/**
 * Compact table-header select filter.
 * Single-select applies immediately and closes.
 * Multi-select toggles without closing; clear resets.
 * Optional `groups` renders independent single-select sections in one popover.
 */
export default function HeaderSelectFilter({
  label,
  testid,
  options = [],
  value,
  multi = false,
  emptyValue = "all",
  onChange,
  groups = null,
  onGroupChange,
  onClearGroups,
  align = "start",
  className = "",
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const grouped = Array.isArray(groups) && groups.length > 0;

  const selectedMulti = multi
    ? (Array.isArray(value) ? value : [])
    : [];
  const active = grouped
    ? groups.some(isGroupActive)
    : multi
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

  const handleGroupSelect = useCallback((group, optionValue) => {
    onGroupChange?.(group.key, optionValue);
  }, [onGroupChange]);

  const handleClear = useCallback(() => {
    if (grouped) {
      onClearGroups?.();
      setOpen(false);
      return;
    }
    if (multi) onChange?.([]);
    else onChange?.(emptyValue);
    setOpen(false);
  }, [emptyValue, grouped, multi, onChange, onClearGroups]);

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
          className={"hsf-th-popover" + (grouped ? " is-grouped" : "")}
          onClick={(e) => e.stopPropagation()}
          onOpenAutoFocus={(e) => {
            const first = e.currentTarget.querySelector('[role="option"], button, input');
            if (first) {
              e.preventDefault();
              first.focus();
            }
          }}
        >
          {grouped ? (
            <div id={listId} className="hsf-th-groups">
              {groups.map((group, index) => {
                const groupEmpty = group.emptyValue ?? "";
                const groupTestid = group.testid || `${testid}-${group.key}`;
                return (
                  <div
                    key={group.key}
                    className={"hsf-th-group" + (index > 0 ? " is-divided" : "")}
                    data-testid={groupTestid}
                  >
                    <div className="hsf-th-pop-label" id={`${listId}-${group.key}-label`}>
                      {group.label}
                    </div>
                    <div
                      className="hsf-th-options"
                      role="listbox"
                      aria-labelledby={`${listId}-${group.key}-label`}
                    >
                      {(group.options || []).map((option) => {
                        const optionValue = option.value;
                        const selected = String(group.value ?? groupEmpty) === String(optionValue);
                        return (
                          <button
                            key={String(optionValue)}
                            type="button"
                            role="option"
                            aria-selected={selected ? "true" : "false"}
                            className={"hsf-th-option" + (selected ? " is-selected" : "")}
                            data-testid={`${groupTestid}-option-${String(optionValue).replace(/\s+/g, "-")}`}
                            onClick={() => handleGroupSelect(group, optionValue)}
                          >
                            <span className={"hsf-th-radio" + (selected ? " is-on" : "")} aria-hidden="true" />
                            <span>{option.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <>
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
            </>
          )}
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
