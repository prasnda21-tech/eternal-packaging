import { Link } from "react-router-dom";
import { formatPurchaseDate } from "../lib/purchaseUi";

/**
 * Compact purchasing lifecycle strip (display only).
 * Does not change workflow — informative navigation only.
 */
export default function PurchaseLifecycleStrip({ stages = [], testId = "purchase-lifecycle" }) {
  if (!stages.length) return null;
  return (
    <div className="purchase-lifecycle" data-testid={testId} aria-label="Ringkasan siklus pembelian">
      {stages.map((stage, idx) => {
        const body = (
          <>
            <span className={`purchase-lifecycle-mark${stage.done ? " is-done" : ""}`} aria-hidden>
              {stage.done ? "✓" : "●"}
            </span>
            <span className="purchase-lifecycle-copy">
              <span className="purchase-lifecycle-label">{stage.label}</span>
              <span className="purchase-lifecycle-primary">{stage.primary}</span>
              {stage.secondary ? (
                <span className="purchase-lifecycle-secondary">{stage.secondary}</span>
              ) : null}
              {stage.date ? (
                <span className="purchase-lifecycle-date">{formatPurchaseDate(stage.date)}</span>
              ) : null}
            </span>
          </>
        );
        return (
          <div key={stage.key || idx} className="purchase-lifecycle-stage">
            {idx > 0 ? <span className="purchase-lifecycle-arrow" aria-hidden>↓</span> : null}
            {stage.href ? (
              <Link
                className="purchase-lifecycle-card is-link"
                to={stage.href}
                data-testid={`lifecycle-${stage.key}`}
              >
                {body}
              </Link>
            ) : (
              <div className="purchase-lifecycle-card" data-testid={`lifecycle-${stage.key}`}>
                {body}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
