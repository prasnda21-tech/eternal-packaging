import { useEffect, useMemo, useRef, useState } from "react";
import {
  fitCenter,
  isValidCoordinate,
  latLngToPixel,
  markerTone,
  pixelToLatLng,
  tilesForView,
} from "../lib/independentRoute";

export default function RoutePlannerMap({
  points = [],
  pickMode = false,
  onPick,
  height = 420,
}) {
  const wrapRef = useRef(null);
  const [size, setSize] = useState({ width: 640, height });
  const fitted = useMemo(() => fitCenter(points), [points]);
  const [center, setCenter] = useState(fitted);
  const [zoom, setZoom] = useState(fitted.zoom || 12);

  useEffect(() => {
    setCenter({ lat: fitted.lat, lng: fitted.lng });
    setZoom(fitted.zoom || 12);
  }, [fitted.lat, fitted.lng, fitted.zoom]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const sync = () => setSize({ width: el.clientWidth || 640, height: el.clientHeight || height });
    sync();
    const obs = typeof ResizeObserver !== "undefined" ? new ResizeObserver(sync) : null;
    obs?.observe(el);
    return () => obs?.disconnect();
  }, [height]);

  const tiles = tilesForView(center, size.width, size.height, zoom);
  const markers = points.filter((p) => isValidCoordinate(p.lat, p.lng)).map((p) => {
    const pos = latLngToPixel(p.lat, p.lng, center, size.width, size.height, zoom);
    return { ...p, ...pos, tone: markerTone(p.kind, p.priority_id) };
  });
  const path = markers.filter((m) => m.kind !== "ME").sort((a, b) => (a.seq || 0) - (b.seq || 0));

  const onClick = (e) => {
    if (!pickMode || !onPick) return;
    const rect = e.currentTarget.getBoundingClientRect();
    onPick(pixelToLatLng(e.clientX - rect.left, e.clientY - rect.top, center, size.width, size.height, zoom));
  };

  return (
    <div className="iro-map" ref={wrapRef} style={{ height }} data-testid="iro-map">
      <div className="iro-map-canvas" onClick={onClick} role={pickMode ? "button" : "presentation"}>
        {tiles.map((t) => (
          <img key={t.key} alt="" src={t.src} className="iro-map-tile" style={{ left: t.left, top: t.top }} />
        ))}
        {path.length > 1 && (
          <svg className="iro-map-path" width={size.width} height={size.height}>
            <polyline
              fill="none"
              stroke="#0f766e"
              strokeWidth="3"
              strokeLinejoin="round"
              points={path.map((p) => `${p.x},${p.y}`).join(" ")}
            />
          </svg>
        )}
        {markers.map((m) => (
          <div
            key={`${m.kind || "s"}-${m.id || m.seq}-${m.x}`}
            className={`iro-marker ${m.kind === "START" ? "is-start" : ""} ${m.kind === "END" ? "is-end" : ""}`}
            style={{ left: m.x, top: m.y, background: m.tone.bg }}
            title={m.label || m.name}
          >
            <span className="iro-marker-seq">{m.seq || m.tone.label}</span>
            {m.priority_id ? <span className="iro-marker-pri">{String(m.priority_id).toUpperCase()}</span> : null}
          </div>
        ))}
      </div>
      <div className="iro-map-tools">
        <button type="button" onClick={() => setZoom((z) => Math.min(18, z + 1))} data-testid="iro-zoom-in">+</button>
        <button type="button" onClick={() => setZoom((z) => Math.max(4, z - 1))} data-testid="iro-zoom-out">−</button>
      </div>
      {pickMode ? <p className="iro-map-hint">Klik peta untuk menentukan titik.</p> : null}
    </div>
  );
}
