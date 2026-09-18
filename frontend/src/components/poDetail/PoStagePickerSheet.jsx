import { X } from "lucide-react";
import { STAGES } from "../../lib/constants";
import { getGroupedStagePresentation } from "../../lib/poDetailPresentation";
import {
  PO_DETAIL_STEPPER_OMIT,
  PO_DETAIL_UI_STAGE_COUNT,
  collapsePoDetailUiStage,
  poDetailUiStageNumber,
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
          <h2 id="po-stage-sheet-title">{PO_DETAIL_UI_STAGE_COUNT} tahap workflow</h2>
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
          {STAGES.map((name, i) => {
            if (PO_DETAIL_STEPPER_OMIT.includes(i)) return null;
            const pres = getGroupedStagePresentation(i, order, actualStage);
            const isViewed = collapsePoDetailUiStage(viewedStage) === i;
            const isActual = collapsePoDetailUiStage(actualStage) === i;
            return (
              <li key={name}>
                <button
                  type="button"
                  className={
                    "po-stage-sheet-item"
                    + (isViewed ? " is-viewed" : "")
                    + (isActual ? " is-actual" : "")
                    + ` tone-${pres.tone}`
                  }
                  data-testid={`po-stage-sheet-item-${i}`}
                  onClick={() => onSelect(i)}
                >
                  <span className="po-stage-sheet-num">{poDetailUiStageNumber(i)}</span>
                  <span className="po-stage-sheet-main">
                    <strong>{name}</strong>
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
