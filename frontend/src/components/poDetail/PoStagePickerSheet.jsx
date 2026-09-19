import { X } from "lucide-react";
import { getStagePresentation } from "../../lib/poDetailPresentation";
import {
  PO_DETAIL_VISUAL_STEPS,
  visualStepPresentationIndex,
} from "../../lib/poDetailStageNav";

export default function PoStagePickerSheet({
  open,
  order,
  actualStage,
  viewedStage,
  onSelect,
  onClose,
}) {
  if (!order) return null;
  return (
    <div
      className={"po-stage-sheet-root" + (open ? " is-open" : "")}
      data-testid="po-stage-sheet-root"
      hidden={!open}
    >
      <button
        type="button"
        className="po-stage-sheet-scrim"
        data-testid="po-stage-sheet-scrim"
        aria-label="Tutup daftar tahap"
        onClick={onClose}
      />
      <div
        className="po-stage-sheet"
        data-testid="po-stage-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="po-stage-sheet-title"
      >
        <header className="po-stage-sheet-head">
          <h2 id="po-stage-sheet-title">Tahap workflow</h2>
          <button
            type="button"
            className="icon-button"
            data-testid="po-stage-sheet-close"
            aria-label="Tutup"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>
        <ol className="po-stage-sheet-list">
          {PO_DETAIL_VISUAL_STEPS.map((step) => {
            const presIdx = visualStepPresentationIndex(step, actualStage);
            const pres = getStagePresentation(presIdx, order, actualStage);
            const isViewed = step.internalIndexes.includes(Number(viewedStage));
            const isActual = step.internalIndexes.includes(Number(actualStage));
            return (
              <li key={step.id}>
                <button
                  type="button"
                  className={
                    "po-stage-sheet-item"
                    + (isViewed ? " is-viewed" : "")
                    + (isActual ? " is-actual" : "")
                    + ` tone-${pres.tone}`
                  }
                  data-testid={`po-stage-sheet-item-${step.canonicalIndex}`}
                  data-visual-number={step.displayNumber}
                  onClick={() => onSelect(step.canonicalIndex)}
                >
                  <span className="po-stage-sheet-num">{step.displayNumber}</span>
                  <span className="po-stage-sheet-main">
                    <strong>{step.label}</strong>
                    <span className="po-stage-sheet-status">{pres.label || pres.summary}</span>
                  </span>
                  {isActual ? <span className="po-stage-sheet-flag">Aktif</span> : null}
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
