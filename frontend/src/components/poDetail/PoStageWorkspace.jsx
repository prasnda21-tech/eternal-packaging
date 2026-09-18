/**
 * Active-stage workspace frame. Stage slots stay mounted (hidden) so drafts survive.
 */
export default function PoStageWorkspace({
  title,
  summary,
  viewingOtherStage,
  onBackToActive,
  notices,
  children,
}) {
  return (
    <section className="po-stage-ws-chrome po-stage-workspace" data-testid="po-stage-panel">
      <header className="po-stage-ws-chrome-head po-stage-workspace-head">
        <div>
          <span className="po-stage-ws-kicker">Workspace tahap</span>
          <h2 data-testid="po-viewed-stage-title">{title}</h2>
          {summary ? <p className="po-stage-workspace-summary" data-testid="po-stage-workspace-summary">{summary}</p> : null}
        </div>
        {viewingOtherStage && (
          <button
            type="button"
            className="ghost sm"
            data-testid="btn-back-to-active-stage"
            onClick={onBackToActive}
          >
            Kembali ke Tahap Aktif
          </button>
        )}
      </header>
      {notices}
      <div className="po-stage-workspace-body" data-testid="po-stage-workspace-body">
        {children}
      </div>
    </section>
  );
}
