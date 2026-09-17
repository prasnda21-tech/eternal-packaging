import { ArrowRight } from "lucide-react";
import { resolveOperationalFocus } from "../../lib/poDetailPresentation";

/**
 * Compact operational focus — presentation only.
 * Jumps to the actual stage workspace without mutation.
 */
export default function PoDetailFocusPanel({
  order,
  primaryAction = null,
  onGotoStage,
  viewedStage = null,
  actualStage = null,
}) {
  const focus = resolveOperationalFocus(order);
  if (!focus) return null;
  const actualIndex = actualStage == null || Number.isNaN(Number(actualStage))
    ? Number(focus.stageIndex)
    : Number(actualStage);
  const viewingOther =
    viewedStage != null && Number(viewedStage) !== actualIndex;

  return (
    <section
      className={`po-focus-panel tone-${focus.tone}`}
      data-testid="po-focus-panel"
      aria-label="Fokus Saat Ini"
    >
      <div className="po-focus-head">
        <span className="po-focus-kicker">Tahap aktif saat ini</span>
        <strong className="po-focus-title" data-testid="po-focus-title">
          {focus.title}
        </strong>
      </div>
      <p className="po-focus-detail" data-testid="po-focus-detail">
        {focus.detail}
      </p>
      {focus.holdReasons?.length > 0 && (
        <ul className="po-focus-holds" data-testid="po-focus-holds">
          {focus.holdReasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <div className="po-focus-actions">
        {primaryAction}
        {viewingOther && typeof onGotoStage === "function" && (
          <button
            type="button"
            className="ghost sm"
            data-testid="po-focus-goto-stage"
            onClick={() => onGotoStage(actualIndex)}
          >
            Kembali ke Tahap Aktif <ArrowRight size={14} />
          </button>
        )}
      </div>
    </section>
  );
}
