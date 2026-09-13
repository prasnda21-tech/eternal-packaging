import { ArrowRight } from "lucide-react";
import { scrollToPoDetailStageIndex } from "../../lib/poDetailStageNav";
import { resolveOperationalFocus } from "../../lib/poDetailPresentation";

/**
 * Compact operational focus — presentation only.
 * Surfaces existing stage/hold info; jumps to the real action controls via scroll.
 */
export default function PoDetailFocusPanel({ order, primaryAction = null }) {
  const focus = resolveOperationalFocus(order);
  if (!focus) return null;

  return (
    <section
      className={`po-focus-panel tone-${focus.tone}`}
      data-testid="po-focus-panel"
      aria-label="Fokus Saat Ini"
    >
      <div className="po-focus-head">
        <span className="po-focus-kicker">Fokus Saat Ini</span>
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
        <button
          type="button"
          className="ghost sm"
          data-testid="po-focus-goto-stage"
          onClick={() => scrollToPoDetailStageIndex(focus.stageIndex)}
        >
          Ke tahap {focus.stageName} <ArrowRight size={14} />
        </button>
      </div>
    </section>
  );
}
