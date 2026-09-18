/**
 * Same-PO context page. Replaces the stage workspace — never a drawer, modal, or overlay.
 */
export default function PoContextPage({
  view,
  active,
  testid,
  className = "",
  children,
}) {
  return (
    <section
      className={`po-context-page${className ? ` ${className}` : ""}`}
      data-testid={testid || `po-context-page-${view}`}
      data-view={view}
      hidden={!active}
      aria-hidden={!active}
    >
      <div className="po-context-page-body">
        {children}
      </div>
    </section>
  );
}
