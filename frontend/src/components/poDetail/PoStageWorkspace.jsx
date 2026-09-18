/**
 * Active-stage / context-page workspace frame. Stage slots stay mounted (hidden) so drafts survive.
 */
export default function PoStageWorkspace({
  title,
  summary,
  viewingOtherStage,
  onBackToActive,
  contextView,
  onBackToWorkflow,
  notices,
  children,
}) {
  const showHead = !!(title || viewingOtherStage || contextView);
  return (
    <section className={"po-stage-ws-chrome po-stage-workspace" + (contextView ? " is-context-page" : "")} data-testid="po-stage-panel">
      {showHead ? (
        <header className="po-stage-ws-chrome-head po-stage-workspace-head">
          <div>
            {contextView ? (
              <span className="po-stage-ws-kicker">Halaman PO</span>
            ) : title ? (
              <span className="po-stage-ws-kicker">Workspace tahap</span>
            ) : null}
            {title ? <h2 data-testid="po-viewed-stage-title">{title}</h2> : null}
            {summary && !contextView ? <p className="po-stage-workspace-summary" data-testid="po-stage-workspace-summary">{summary}</p> : null}
          </div>
          {contextView ? (
            <button
              type="button"
              className="ghost sm"
              data-testid="btn-back-to-workflow"
              onClick={onBackToWorkflow}
            >
              Kembali ke Workflow
            </button>
          ) : viewingOtherStage ? (
            <button
              type="button"
              className="ghost sm"
              data-testid="btn-back-to-active-stage"
              onClick={onBackToActive}
            >
              Kembali ke Tahap Aktif
            </button>
          ) : null}
        </header>
      ) : null}
      {notices}
      <div className="po-stage-workspace-body" data-testid="po-stage-workspace-body">
        {children}
      </div>
    </section>
  );
}
