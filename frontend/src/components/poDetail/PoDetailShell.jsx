/**
 * Locked one-screen Detail PO chrome.
 * Presentation only — no API, stage math, or quantity rules.
 */
export default function PoDetailShell({
  header,
  commandBar,
  mobileChrome,
  stageBar,
  notices,
  workspace,
  rail,
  drawers,
  drawerOpen = false,
}) {
  return (
    <div
      className={"po-detail-shell" + (drawerOpen ? " is-drawer-open" : "")}
      data-testid="po-detail-shell"
    >
      <div className="po-detail-shell-chrome">
        {header}
        {commandBar}
        {mobileChrome}
        {stageBar}
        {notices}
      </div>
      <div className="po-detail-shell-main" data-testid="po-detail-shell-main">
        {workspace}
        <div className="po-context-drawers" data-testid="po-context-drawers">
          {drawers}
        </div>
        {rail}
      </div>
    </div>
  );
}
