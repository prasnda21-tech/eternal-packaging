export default function MobileMetricStrip({ items, testid, className }) {
  return (
    <div className={`mop-metrics${className ? ` ${className}` : ""}`} data-testid={testid || "mobile-metrics"}>
      {items.map((item) => (
        <div className="mop-metric" key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
        </div>
      ))}
    </div>
  );
}
