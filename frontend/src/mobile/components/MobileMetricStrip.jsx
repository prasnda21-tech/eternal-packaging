export default function MobileMetricStrip({ items, testid }) {
  return (
    <div className="mop-metrics" data-testid={testid || "mobile-metrics"}>
      {items.map((item) => (
        <div className="mop-metric" key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
        </div>
      ))}
    </div>
  );
}
