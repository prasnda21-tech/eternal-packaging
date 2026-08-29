import logoFull from "../assets/branding/eternal-logo-full-white.png";
import logoSymbol from "../assets/branding/eternal-logo-symbol.png";

export default function BrandMark({ compact = false, stacked = false, onDark = false }) {
  if (stacked) {
    return (
      <span className="brand-mark is-stacked">
        <img
          src={logoFull}
          alt="Eternal Packaging"
          className="brand-logo-full"
          data-testid="login-brand-logo"
        />
      </span>
    );
  }

  return (
    <span className={`brand-mark${compact ? " is-compact" : ""}${onDark ? " is-on-dark" : ""}`}>
      <img
        src={logoSymbol}
        alt=""
        className="brand-logo-symbol"
        data-testid="brand-symbol"
      />
      <span className="brand-copy">
        <strong>Eternal Packaging</strong>
        {!compact && <em>Sistem Produksi</em>}
      </span>
    </span>
  );
}
