export default function BrandMark({ compact = false, stacked = false, onDark = false }) {
  const size = stacked ? 48 : compact ? 28 : 36;
  return (
    <span className={`brand-mark${compact ? " is-compact" : ""}${stacked ? " is-stacked" : ""}`}>
      <img
        src={onDark ? "/eternal-mark-on-dark.svg" : "/eternal-mark.svg"}
        alt=""
        width={size}
        height={size}
      />
      <span className="brand-copy">
        <strong>Eternal Packaging</strong>
        {!compact && <em>Sistem Produksi</em>}
      </span>
    </span>
  );
}
