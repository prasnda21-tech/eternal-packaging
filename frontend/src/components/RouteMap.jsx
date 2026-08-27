/** Static numbered route preview. Does not call the optimizer. */
export default function RouteMap({ depot, routes, unassigned }) {
  const assigned = [];
  (routes || []).forEach((r, ri) => {
    (r.stops || []).forEach((s) => {
      if (s.lat != null && s.lng != null) assigned.push({ ...s, plate: r.vehicle_plate, driver: r.driver_name, ri });
    });
  });
  const pts = [];
  if (depot?.lat != null && depot?.lng != null) pts.push({ lat: depot.lat, lng: depot.lng });
  assigned.forEach((s) => pts.push({ lat: s.lat, lng: s.lng }));
  if (depot?.lat != null && depot?.lng != null) pts.push({ lat: depot.lat, lng: depot.lng });
  const lats = pts.map((p) => p.lat);
  const lngs = pts.map((p) => p.lng);
  const minLat = Math.min(...lats, 0);
  const maxLat = Math.max(...lats, 1);
  const minLng = Math.min(...lngs, 0);
  const maxLng = Math.max(...lngs, 1);
  const sx = (lng) => ((lng - minLng) / (maxLng - minLng || 1)) * 280 + 10;
  const sy = (lat) => (1 - (lat - minLat) / (maxLat - minLat || 1)) * 160 + 10;
  const d = pts.map((p, i) => `${i ? "L" : "M"} ${sx(p.lng)} ${sy(p.lat)}`).join(" ");

  return (
    <div className="route-map" data-testid="route-map">
      <svg viewBox="0 0 300 180" role="img" aria-label="Peta rute">
        {pts.length > 1 && <path d={d} fill="none" stroke="#2c6e49" strokeWidth="2" />}
        {depot?.lat != null && <circle cx={sx(depot.lng)} cy={sy(depot.lat)} r="6" fill="#1f3d2b" data-testid="route-map-depot" />}
        {assigned.map((s, i) => (
          <g key={s.stop_id}>
            <circle cx={sx(s.lng)} cy={sy(s.lat)} r="8" fill="#3d7ea6" />
            <text x={sx(s.lng)} y={sy(s.lat) + 3} textAnchor="middle" fontSize="8" fill="#fff">{s.sequence || i + 1}</text>
          </g>
        ))}
      </svg>
      {(unassigned || []).length > 0 && (
        <p className="muted small" data-testid="route-unassigned-note">{unassigned.length} stop unassigned</p>
      )}
    </div>
  );
}
