import React from "react";
import { useFormStandard } from "./FormContext";
import { CheckCircle2, AlertCircle, Clock, MinusCircle } from "lucide-react";
import "./semantic-styles.css";

export function FormProgress({
  percentage: explicitPercentage,
  completed: explicitCompleted,
  total: explicitTotal,
  className = "",
  ...props
}) {
  const formStandard = useFormStandard();
  const progress = formStandard?.progress || { percentage: 100, completed: 0, total: 0 };

  const percentage = explicitPercentage !== undefined ? explicitPercentage : progress.percentage;
  const completed = explicitCompleted !== undefined ? explicitCompleted : progress.completed;
  const total = explicitTotal !== undefined ? explicitTotal : progress.total;

  let progressColor = "bg-blue-600";
  if (percentage === 100) {
    progressColor = "bg-emerald-600";
  } else if (percentage < 50) {
    progressColor = "bg-amber-500";
  }

  return (
    <div
      className={`bg-white p-4 rounded-lg border border-gray-200 shadow-sm ${className}`}
      aria-live="polite"
      data-testid="form-progress"
      {...props}
    >
      <div className="flex justify-between items-end mb-2">
        <div>
          <span className="text-[11px] font-bold text-gray-500 uppercase tracking-wider block">
            Kelengkapan Formulir
          </span>
          <p className="text-2xl font-extrabold text-gray-900 leading-tight">
            {percentage}%{" "}
            <span className="text-xs font-semibold text-gray-500 ml-1">
              {percentage === 100 ? "Lengkap" : "Terisi"}
            </span>
          </p>
        </div>
        {total > 0 && (
          <div className="text-xs font-semibold text-gray-600 bg-gray-100 px-2.5 py-1 rounded-full">
            {completed} dari {total} field wajib
          </div>
        )}
      </div>

      <div className="h-2.5 w-full bg-gray-100 rounded-full overflow-hidden">
        <div
          className={`h-full ${progressColor} transition-all duration-300 ease-out`}
          style={{ width: `${Math.min(100, Math.max(0, percentage))}%` }}
          role="progressbar"
          aria-valuenow={percentage}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Kelengkapan formulir: ${percentage}%`}
        />
      </div>
    </div>
  );
}

export function SectionChecklist({ sections = [], className = "" }) {
  const formStandard = useFormStandard();

  if (!sections || sections.length === 0) return null;

  return (
    <div
      className={`bg-white p-4 rounded-lg border border-gray-200 shadow-sm ${className}`}
      data-testid="section-checklist"
    >
      <h4 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-3">
        Daftar Bagian Formulir
      </h4>
      <ul className="space-y-2">
        {sections.map((sec) => {
          const status = sec.status || (formStandard?.getSectionStatus ? formStandard.getSectionStatus(sec.id) : "neutral");

          let StatusIcon = CheckCircle2;
          let iconColor = "text-emerald-600";
          let badgeBg = "bg-emerald-50 border-emerald-200 text-emerald-800";
          let label = "Lengkap";

          if (status === "error") {
            StatusIcon = AlertCircle;
            iconColor = "text-red-600";
            badgeBg = "bg-red-50 border-red-200 text-red-800";
            label = "Ada Error";
          } else if (status === "incomplete") {
            StatusIcon = Clock;
            iconColor = "text-amber-500";
            badgeBg = "bg-amber-50 border-amber-200 text-amber-800";
            label = "Belum Lengkap";
          } else if (status === "disabled") {
            StatusIcon = MinusCircle;
            iconColor = "text-gray-400";
            badgeBg = "bg-gray-50 border-gray-200 text-gray-600";
            label = "Nonaktif";
          } else if (status === "neutral") {
            StatusIcon = Clock;
            iconColor = "text-gray-400";
            badgeBg = "bg-gray-50 border-gray-200 text-gray-600";
            label = "Opsional";
          }

          return (
            <li
              key={sec.id}
              className="flex items-center justify-between gap-2 p-1.5 rounded-md hover:bg-gray-50 text-xs"
            >
              <div className="flex items-center gap-2 min-w-0">
                <StatusIcon className={`w-4 h-4 shrink-0 ${iconColor}`} aria-hidden="true" />
                <span className="font-medium text-gray-800 truncate">{sec.title}</span>
              </div>
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border shrink-0 ${badgeBg}`}>
                {label}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
