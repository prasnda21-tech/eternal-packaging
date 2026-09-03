import React, { useEffect, useState, cloneElement, Children } from "react";
import { useFormStandard } from "./FormContext";
import { useFormSection } from "./FormSection";
import { AlertCircle } from "lucide-react";
import "./semantic-styles.css";

export function FormField({
  id,
  label,
  children,
  isRequired = false,
  error = null,
  disabled = false,
  helpText = "",
  status: explicitStatus,
  value: explicitValue,
  className = "",
}) {
  const formStandard = useFormStandard();
  const sectionId = useFormSection() || "default";

  const [internalFocused, setInternalFocused] = useState(false);

  useEffect(() => {
    if (id && formStandard?.registerField) {
      formStandard.registerField(id, {
        isRequired,
        section: sectionId,
        error,
        value: explicitValue,
      });
    }
  }, [id, formStandard, isRequired, sectionId, error, explicitValue]);

  useEffect(() => {
    if (id && formStandard?.updateField) {
      formStandard.updateField(id, {
        error,
        isRequired,
        section: sectionId,
        ...(explicitValue !== undefined ? { value: explicitValue } : {}),
      });
    }
  }, [error, isRequired, formStandard, id, sectionId, explicitValue]);

  const fieldState = (formStandard?.fields && formStandard.fields[id]) || {};
  const isFocused = internalFocused || fieldState.isFocused;
  const val = explicitValue !== undefined ? explicitValue : fieldState.value;

  let stateClass = "";
  if (disabled) {
    stateClass = "fs-disabled";
  } else if (error) {
    stateClass = "fs-error";
  } else if (isFocused) {
    stateClass = "fs-active";
  } else if (isRequired && (val === undefined || val === null || val === "")) {
    stateClass = "fs-incomplete";
  } else if (isRequired && val !== undefined && val !== null && val !== "") {
    stateClass = "fs-complete";
  }

  if (explicitStatus) {
    stateClass = `fs-${explicitStatus}`;
  }

  const handleFocus = () => {
    setInternalFocused(true);
    if (id && formStandard?.updateField) {
      formStandard.updateField(id, { isFocused: true });
    }
  };

  const handleBlur = () => {
    setInternalFocused(false);
    if (id && formStandard?.updateField) {
      formStandard.updateField(id, { isFocused: false, isTouched: true });
    }
  };

  const handleChangeCapture = (e) => {
    if (e.target && e.target.value !== undefined && id && formStandard?.updateField) {
      formStandard.updateField(id, { value: e.target.value });
    }
  };

  return (
    <div
      className={`fs-field-container p-2.5 rounded-md border transition-all duration-150 ${stateClass} ${className}`}
      data-testid={`form-field-${id}`}
    >
      {label && (
        <label
          htmlFor={id}
          className="block mb-1 text-xs font-semibold text-gray-700 flex items-center justify-between gap-1.5"
        >
          <span className="flex items-center gap-1.5">
            {label}
            {isRequired && <span className="ep-req" aria-hidden="true">*</span>}
            {isRequired && (
              <span
                className="fs-badge-required inline-block px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider"
                title="Field wajib diisi"
              >
                Wajib
              </span>
            )}
          </span>
          {isRequired && (
            <span className="text-[10px] text-gray-400 font-normal hidden sm:inline" aria-hidden="true">
              {val ? "? Terisi" : "Belum diisi"}
            </span>
          )}
        </label>
      )}

      <div
        onFocus={handleFocus}
        onBlur={handleBlur}
        onChangeCapture={handleChangeCapture}
        className="relative"
      >
        {Children.map(children, (child) => {
          if (React.isValidElement(child)) {
            return cloneElement(child, {
              id: child.props.id || id,
              "aria-invalid": Boolean(error),
              "aria-required": Boolean(isRequired),
              "aria-describedby": error
                ? `${id}-error`
                : helpText
                ? `${id}-help`
                : child.props["aria-describedby"],
              disabled: disabled || child.props.disabled,
            });
          }
          return child;
        })}
      </div>

      {error && (
        <div
          id={`${id}-error`}
          role="alert"
          className="text-red-600 text-xs mt-1.5 font-medium flex items-center gap-1"
        >
          <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}

      {helpText && !error && (
        <div id={`${id}-help`} className="text-gray-500 text-[11px] mt-1">
          {helpText}
        </div>
      )}
    </div>
  );
}
