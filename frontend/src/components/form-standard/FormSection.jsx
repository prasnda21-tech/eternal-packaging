import React, { createContext, useContext } from "react";
import { useFormStandard } from "./FormContext";
import { CheckCircle2, AlertCircle, Clock, MinusCircle } from "lucide-react";
import "./semantic-styles.css";

const FormSectionContext = createContext(null);

export function useFormSection() {
  return useContext(FormSectionContext);
}

export function FormSection({
  id,
  title,
  children,
  description,
  status: explicitStatus,
  checklist = [],
  className = "",
}) {
  const formStandard = useFormStandard();
  const contextStatus = formStandard?.getSectionStatus ? formStandard.getSectionStatus(id) : "neutral";
  const status = explicitStatus || contextStatus;

  let statusClass = "fs-section-complete";
  let StatusIcon = CheckCircle2;
  let statusBadgeClass = "fs-badge-complete";
  let statusText = "Lengkap";

  if (status === "error") {
    statusClass = "fs-section-error";
    StatusIcon = AlertCircle;
    statusBadgeClass = "fs-badge-error";
    statusText = "Ada Error";
  } else if (status === "incomplete") {
    statusClass = "fs-section-incomplete";
    StatusIcon = Clock;
    statusBadgeClass = "fs-badge-incomplete";
    statusText = "Belum Lengkap";
  } else if (status === "disabled") {
    statusClass = "fs-section-disabled";
    StatusIcon = MinusCircle;
    statusBadgeClass = "fs-badge-disabled";
    statusText = "Nonaktif";
  } else if (status === "neutral") {
    statusClass = "fs-section-neutral";
    StatusIcon = null;
    statusBadgeClass = "";
    statusText = "";
  }

  return (
    <FormSectionContext.Provider value={id}>
      <section
        id={`section-${id}`}
        className={`fs-section p-5 mb-5 rounded-lg border bg-white ${statusClass} ${className}`}
        aria-labelledby={`section-title-${id}`}
        data-testid={`form-section-${id}`}
      >
        <div className="flex items-start justify-between mb-4 pb-3 border-b border-gray-100 gap-3">
          <div>
            <h3
              id={`section-title-${id}`}
              className="text-base font-semibold text-gray-900 flex items-center gap-2"
            >
              {title}
            </h3>
            {description && (
              <p className="text-xs text-gray-500 mt-0.5">{description}</p>
            )}
          </div>
          {StatusIcon && (
            <div
              className={`fs-status-pill inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold shrink-0 ${statusBadgeClass}`}
              aria-live="polite"
            >
              <StatusIcon className="w-3.5 h-3.5" aria-hidden="true" />
              <span>{statusText}</span>
            </div>
          )}
        </div>

        {checklist && checklist.length > 0 && (
          <div className="mb-4 p-3 bg-gray-50 rounded-md border border-gray-100 text-xs">
            <span className="font-semibold text-gray-700 block mb-1.5">Checklist Bagian:</span>
            <ul className="space-y-1">
              {checklist.map((item, idx) => {
                const isItemComplete = item.status === "complete" || item.isComplete;
                const isItemError = item.status === "error" || item.isError;
                const ItemIcon = isItemError ? AlertCircle : isItemComplete ? CheckCircle2 : Clock;
                const itemColor = isItemError
                  ? "text-red-700"
                  : isItemComplete
                  ? "text-green-700"
                  : "text-amber-700";
                return (
                  <li key={item.id || idx} className={`flex items-center gap-1.5 ${itemColor}`}>
                    <ItemIcon className="w-3 h-3 shrink-0" aria-hidden="true" />
                    <span>{item.label}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        <div className="fs-section-content space-y-4">{children}</div>
      </section>
    </FormSectionContext.Provider>
  );
}
