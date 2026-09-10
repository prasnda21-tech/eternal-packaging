import { ChevronLeft, Menu } from "lucide-react";
import { useMobileNav } from "./MobileNavContext";

export default function MobileTopBar({
  kicker = "ETERNAL PACKAGING",
  title,
  subtitle,
  onBack,
  dark = true,
  showMenu = true,
}) {
  const { menuOpen, toggleMenu } = useMobileNav();

  return (
    <header
      className="mop-topbar"
      data-testid="mobile-topbar"
      style={dark ? undefined : { background: "#fff", color: "#102027" }}
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
            <Menu size={20} aria-hidden />
          </button>
        ) : null}
        <div className="mop-topbar-text">
          {onBack ? (
            <button type="button" className="mop-back" onClick={onBack} data-testid="mobile-back">
              <ChevronLeft size={18} /> Kembali
            </button>
          ) : null}
          {kicker ? <p className="mop-topbar-kicker">{kicker}</p> : null}
          {title ? <h1>{title}</h1> : null}
          {subtitle ? <p className="mop-topbar-sub">{subtitle}</p> : null}
        </div>
      </div>
    </header>
  );
}
