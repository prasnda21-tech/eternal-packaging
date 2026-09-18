import { useId } from "react";
import { ChevronDown } from "lucide-react";
import { STAGES } from "../../lib/constants";
import { getGroupedStagePresentation, getStagePresentation } from "../../lib/poDetailPresentation";
import { usePoStageAccordion } from "./PoStageAccordionContext";

/**
 * Progressive disclosure wrapper for Detail PO workflow stages.
 * - Always starts collapsed (no auto-open on active/blocked/error/refresh).
 * - Exclusive open via PoStageAccordionProvider.
 * - Uses `hidden` (not unmount) so in-progress form state is preserved.
 */
export default function PoStageSection({
  stageIndex,
  order,
  title,
  summary,
  hasError = false,
  grouped = false,
  children,
  testid,
}) {
  const currentStage = order?.current_stage || 0;
  const presentation = grouped
    ? getGroupedStagePresentation(stageIndex, order, currentStage)
    : getStagePresentation(stageIndex, order, currentStage);
  const { open, toggle } = usePoStageAccordion(stageIndex);
  const panelId = useId();

  const heading = title || STAGES[stageIndex] || `Tahap ${stageIndex + 1}`;
  // Prefer explicit summary; fall back to presentation; surface error cue on collapsed row
  const short =
    summary ||
    (hasError ? "Perlu perhatian" : null) ||
    presentation.summary;

  const onKeyDown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  return (
    <div
      className={`po-stage-section tone-${presentation.tone}${open ? " is-open" : " is-collapsed"}`}
      data-testid={testid || `po-stage-section-${stageIndex}`}
      data-stage-tone={presentation.tone}
      data-stage-index={stageIndex}
    >
      <button
        type="button"
        className="po-stage-summary-row"
        aria-expanded={open}
        aria-controls={panelId}
        data-testid={`po-stage-toggle-${stageIndex}`}
        onClick={toggle}
        onKeyDown={onKeyDown}
      >
        <span className="po-stage-summary-num" aria-hidden="true">
          {String(stageIndex + 1).padStart(2, "0")}
        </span>
        <span className="po-stage-summary-main">
          <span className="po-stage-summary-title">{heading}</span>
          <span className={`po-stage-summary-status tone-${presentation.tone}`}>
            {presentation.label}
          </span>
        </span>
        <span className="po-stage-summary-meta">{short}</span>
        <ChevronDown
          size={14}
          className={`po-stage-chevron${open ? " is-open" : ""}`}
          aria-hidden="true"
        />
      </button>
      <div id={panelId} className="po-stage-section-body" hidden={!open}>
        {children}
      </div>
    </div>
  );
}
