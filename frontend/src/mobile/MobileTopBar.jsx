import { ChevronLeft } from "lucide-react";

export default function MobileTopBar({
  kicker = "ETERNAL PACKAGING",
  title,
  subtitle,
  onBack,
  dark = true,
}) {
  return (
    <header className="mop-topbar" data-testid="mobile-topbar" style={dark ? undefined : { background: "#fff", color: "#102027" }}>
      {onBack ? (
        <button type="button" className="mop-back" onClick={onBack} data-testid="mobile-back">
          <ChevronLeft size={18} /> Kembali
        </button>
      ) : null}
      {kicker ? <p className="mop-topbar-kicker">{kicker}</p> : null}
      {title ? <h1>{title}</h1> : null}
      {subtitle ? <p className="mop-topbar-sub">{subtitle}</p> : null}
    </header>
  );
}
