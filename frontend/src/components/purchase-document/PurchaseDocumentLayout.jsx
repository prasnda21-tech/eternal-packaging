import { useEffect, useRef, useState } from "react";
import "./purchaseDocument.css";

function mark(state) {
  if (state === "done") return "✓";
  if (state === "now") return "●";
  return "";
}

export function PurchaseWorkflowTimeline({ steps }) {
  return (
    <nav className="pd-flow" aria-label="Proses pembelian">
      {(steps || []).map((step) => (
        <div key={step.label} className={`pd-step${step.state === "next" ? "" : ` is-${step.state}`}`}>
          <span className="pd-track" />
          <span className="pd-dot">{mark(step.state)}</span>
          <span>{step.label}</span>
        </div>
      ))}
    </nav>
  );
}

export default function PurchaseDocumentLayout({
  title,
  number,
  numberTestId,
  headerTestId,
  facts = [],
  note,
  steps = [],
  children,
  totals = [],
  primary,
  extraMenu = [],
  finance = null,
  history = null,
  attachments = null,
  testId,
  footerTestId,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [panel, setPanel] = useState(null);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const close = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);

  const entries = [
    finance ? { id: "finance", label: "Detail Keuangan" } : null,
    history ? { id: "history", label: "Riwayat Dokumen" } : null,
    attachments ? { id: "attachments", label: "Lampiran" } : null,
    ...extraMenu.map((item, index) => ({ id: `extra-${index}`, label: item.label, onClick: item.onClick })),
  ].filter(Boolean);

  const openEntry = (entry) => {
    setMenuOpen(false);
    if (entry.onClick) {
      entry.onClick();
      return;
    }
    setPanel((current) => (current === entry.id ? null : entry.id));
  };

  return (
    <article className={`pd-doc${panel ? " is-panel" : ""}`} data-testid={testId}>
      <header className="pd-head" data-testid={headerTestId}>
        <div className="pd-title-row">
          <h1>{title}</h1>
          <div className="pd-head-side" ref={menuRef}>
            <div className="pd-docno" data-testid={numberTestId}>{number || "—"}</div>
            {entries.length > 0 && (
              <button type="button" className="pd-more" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
                ⋮ Lainnya
              </button>
            )}
            {menuOpen && (
              <div className="pd-menu" role="menu">
                {entries.map((entry) => (
                  <button key={entry.id} type="button" role="menuitem" onClick={() => openEntry(entry)}>
                    {entry.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="pd-facts">
          {facts.map((fact) => (
            <div key={fact.label}>
              <span className="pd-k">{fact.label}</span>
              <span className={`pd-v${fact.status ? " is-status" : ""}`} data-testid={fact.testId}>{fact.value ?? "—"}</span>
            </div>
          ))}
        </div>
        {note ? (
          <div className="pd-note">
            <span className="pd-k">Catatan</span>
            <span className="pd-v">{note}</span>
          </div>
        ) : null}
        {panel === "finance" && finance ? <div className="pd-panel" data-testid="purchase-finance-panel">{finance}</div> : null}
        {panel === "history" && history ? <div className="pd-panel">{history}</div> : null}
        {panel === "attachments" && attachments ? <div className="pd-panel">{attachments}</div> : null}
      </header>
      <PurchaseWorkflowTimeline steps={steps} />
      <div className="pd-body">{children}</div>
      <footer className="pd-dock" data-testid={footerTestId}>
        <div className="pd-totals">
          {totals.map((row, index) => (
            <div key={row.label} className={index === totals.length - 1 ? "is-grand" : ""}>
              <span>{row.label}</span>
              <span data-testid={row.testId}>{row.value ?? "—"}</span>
            </div>
          ))}
        </div>
        {primary}
      </footer>
    </article>
  );
}
