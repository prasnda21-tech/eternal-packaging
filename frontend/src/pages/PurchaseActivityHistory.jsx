import { actorDisplayName, formatPurchaseDateTime, hasPurchaseDocBeenUpdated } from "../lib/purchaseUi";

/**
 * Compact activity history for purchasing document detail.
 * Shows created / updated / deleted (cancelled) actors without technical metadata dump.
 */
export default function PurchaseActivityHistory({ doc, testId = "purchase-activity-history" }) {
  if (!doc) return null;

  const createdName = actorDisplayName(doc.created_by, doc.created_by_name);
  const cancelled = doc.cancelled === true;
  const cancelledName = cancelled
    ? actorDisplayName(doc.cancelled_by, doc.cancelled_by_name)
    : "";
  const updated = hasPurchaseDocBeenUpdated(doc);
  const updatedName = updated
    ? actorDisplayName(doc.updated_by, doc.updated_by_name)
    : "";

  const submitted = String(doc.submission_status || "").toUpperCase() === "SUBMITTED";

  return (
    <section className="purchase-activity-history" data-testid={testId}>
      <h4 className="purchase-activity-title">Riwayat Aktivitas</h4>
      <div className="purchase-activity-grid">
        <div data-testid={`${testId}-created`}>
          <span className="label">Dibuat oleh</span>
          <span className="value">{createdName}</span>
          <span className="muted small">{formatPurchaseDateTime(doc.created_at)}</span>
        </div>
        <div data-testid={`${testId}-updated`}>
          <span className="label">Terakhir diperbarui oleh</span>
          {updated ? (
            <>
              <span className="value">{updatedName}</span>
              <span className="muted small">{formatPurchaseDateTime(doc.updated_at)}</span>
            </>
          ) : (
            <span className="value muted">Belum ada perubahan</span>
          )}
        </div>
        {cancelled ? (
          <div data-testid={`${testId}-cancelled`}>
            <span className="label">Dihapus oleh</span>
            <span className="value">{cancelledName}</span>
            <span className="muted small">{formatPurchaseDateTime(doc.cancelled_at)}</span>
          </div>
        ) : null}
        {submitted ? (
          <>
            <div data-testid={`${testId}-submitted-by`}>
              <span className="label">Diajukan oleh</span>
              <span className="value">
                {actorDisplayName(doc.submitted_by, doc.submitted_by_name)}
              </span>
            </div>
            <div data-testid={`${testId}-submitted-to`}>
              <span className="label">Diajukan kepada</span>
              <span className="value">
                {actorDisplayName(doc.submitted_to, doc.submitted_to_name)}
              </span>
            </div>
            <div data-testid={`${testId}-submitted-at`}>
              <span className="label">Diajukan pada</span>
              <span className="value">{formatPurchaseDateTime(doc.submitted_at)}</span>
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}
