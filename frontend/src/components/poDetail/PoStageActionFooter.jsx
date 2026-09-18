/**
 * Always-visible stage action strip. Unique testid per stage to avoid duplicates.
 */
export default function PoStageActionFooter({ children, testid, hidden = false }) {
  if (children == null || children === false) return null;
  return (
    <footer className="po-stage-action-footer" data-testid={testid || "po-stage-action-footer"} hidden={hidden}>
      {children}
    </footer>
  );
}
