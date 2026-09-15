import { ChevronLeft, Menu } from "lucide-react";
import { useMobileNav } from "./MobileNavContext";
import MobileHeaderActions from "./components/MobileHeaderActions";

function pageKicker(kicker) {
  const text = String(kicker || "").trim();
  if (!text || /^eternal packaging$/i.test(text)) return "";
  return text;
}

export default function MobileTopBar({
  kicker = "",
  title,
  meta,
  subtitle,
  onBack,
  backTestId = "mobile-back",
  dark = false,
  showMenu = true,
}) {
  const { menuOpen, toggleMenu } = useMobileNav();
  const hasTitleRow = Boolean(title || meta);
  const kickerText = pageKicker(kicker);

  return (
    <header
      className="mop-topbar mop-topbar--unified"
      data-testid="mobile-topbar"
      style={dark ? { background: "#0b1f2a", color: "#fff" } : undefined}
    >
      <div className="mop-topbar-row">
        {showMenu ? (
          <button
            type="button"
            className={`mop-topbar-menu${menuOpen ? " is-open" : ""}`}
            data-testid="mobile-nav-menu"
            aria-label="Menu"
            aria-expanded={menuOpen}
            aria-controls="mobile-sidebar"
            onClick={toggleMenu}
          >
            <Menu size={18} aria-hidden />
          </button>
        ) : null}
        <div className="mop-topbar-text">
          {onBack ? (
            <button type="button" className="mop-back" onClick={onBack} data-testid={backTestId}>
              <ChevronLeft size={18} /> Kembali
            </button>
          ) : null}
          {kickerText ? <p className="mop-topbar-kicker" data-testid="mobile-topbar-kicker">{kickerText}</p> : null}
          {hasTitleRow ? (
            <div className="mop-topbar-title-row" data-testid="mobile-topbar-title-row">
              {title ? (
                <h1 className="mop-topbar-title" data-testid="mobile-topbar-title">
                  {title}
                </h1>
              ) : (
                <span className="mop-topbar-title-spacer" aria-hidden />
              )}
              {meta ? (
                <p className="mop-topbar-meta" data-testid="mobile-topbar-meta">
                  {meta}
                </p>
              ) : null}
            </div>
          ) : null}
          {subtitle ? <p className="mop-topbar-sub">{subtitle}</p> : null}
        </div>
        <MobileHeaderActions />
      </div>
    </header>
  );
}
