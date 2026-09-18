/**
 * Locked one-screen Detail PO chrome.
 * Presentation only — no API, stage math, or quantity rules.
 */
export default function PoDetailShell({
  header,
  mobileChrome,
  stageBar,
  notices,
  workspace,
  rail,
}) {
  return (
    <div
      className="po-detail-shell"
      data-testid="po-detail-shell"
    >
      <div className="po-detail-shell-chrome">
        {header}
        {mobileChrome}
        {stageBar}
        {notices}
      </div>
      <div className="po-detail-shell-main" data-testid="po-detail-shell-main">
        {workspace}
        {rail}
      </div>
    </div>
  );
}
