/** Outlet map helpers. Places search stays on the backend; this is preview/correction only. */

export const OSM_TILE_SIZE = 256;
export const OUTLET_MAP_ZOOM = 16;

/** Official Maps JavaScript API script host. https://developers.google.com/maps/documentation/javascript/load-maps-js-api */
export const GOOGLE_MAPS_JS_SRC = "https://maps.googleapis.com/maps/api/js";

export function googleMapsBrowserKey() {
  return String(process.env.REACT_APP_GOOGLE_MAPS_BROWSER_KEY || "").trim();
}

export function lngToWorldX(lng, zoom = OUTLET_MAP_ZOOM, tileSize = OSM_TILE_SIZE) {
  const n = 2 ** zoom;
  return ((Number(lng) + 180) / 360) * n * tileSize;
}

export function latToWorldY(lat, zoom = OUTLET_MAP_ZOOM, tileSize = OSM_TILE_SIZE) {
  const n = 2 ** zoom;
  const latRad = (Number(lat) * Math.PI) / 180;
  const y = (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2;
  return y * n * tileSize;
}

export function worldToLng(worldX, zoom = OUTLET_MAP_ZOOM, tileSize = OSM_TILE_SIZE) {
  const n = 2 ** zoom;
  return (worldX / (n * tileSize)) * 360 - 180;
}

export function worldToLat(worldY, zoom = OUTLET_MAP_ZOOM, tileSize = OSM_TILE_SIZE) {
  const n = 2 ** zoom;
  const y = worldY / (n * tileSize);
  const latRad = Math.atan(Math.sinh(Math.PI * (1 - 2 * y)));
  return (latRad * 180) / Math.PI;
}

export function pixelToLatLng(offsetX, offsetY, center, width, height, zoom = OUTLET_MAP_ZOOM) {
  const cx = lngToWorldX(center.lng, zoom);
  const cy = latToWorldY(center.lat, zoom);
  return {
    lat: Number(worldToLat(cy + (offsetY - height / 2), zoom).toFixed(6)),
    lng: Number(worldToLng(cx + (offsetX - width / 2), zoom).toFixed(6)),
  };
}

export function latLngToPixel(lat, lng, center, width, height, zoom = OUTLET_MAP_ZOOM) {
  return {
    x: width / 2 + (lngToWorldX(lng, zoom) - lngToWorldX(center.lng, zoom)),
    y: height / 2 + (latToWorldY(lat, zoom) - latToWorldY(center.lat, zoom)),
  };
}

export function tilesForView(center, width, height, zoom = OUTLET_MAP_ZOOM) {
  const cx = lngToWorldX(center.lng, zoom);
  const cy = latToWorldY(center.lat, zoom);
  const left = cx - width / 2;
  const top = cy - height / 2;
  const right = cx + width / 2;
  const bottom = cy + height / 2;
  const x0 = Math.floor(left / OSM_TILE_SIZE);
  const y0 = Math.floor(top / OSM_TILE_SIZE);
  const x1 = Math.floor(right / OSM_TILE_SIZE);
  const y1 = Math.floor(bottom / OSM_TILE_SIZE);
  const tiles = [];
  const n = 2 ** zoom;
  for (let ty = y0; ty <= y1; ty += 1) {
    for (let tx = x0; tx <= x1; tx += 1) {
      const wrappedX = ((tx % n) + n) % n;
      tiles.push({
        key: `${zoom}-${tx}-${ty}`,
        src: `https://tile.openstreetmap.org/${zoom}/${wrappedX}/${ty}.png`,
        left: tx * OSM_TILE_SIZE - left,
        top: ty * OSM_TILE_SIZE - top,
      });
    }
  }
  return tiles;
}

let mapsPromise;

export function loadGoogleMapsJs(key) {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (!key) return Promise.reject(new Error("no browser maps key"));
  if (window.google?.maps?.importLibrary) return Promise.resolve(window.google.maps);
  if (mapsPromise) return mapsPromise;
  mapsPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector("script[data-outlet-maps-js]");
    const finish = () => {
      if (window.google?.maps) resolve(window.google.maps);
      else reject(new Error("Maps JS gagal dimuat"));
    };
    if (existing) {
      existing.addEventListener("load", finish);
      existing.addEventListener("error", () => {
        mapsPromise = null;
        reject(new Error("Maps JS gagal dimuat"));
      });
      if (window.google?.maps) finish();
      return;
    }
    const script = document.createElement("script");
    script.dataset.outletMapsJs = "1";
    script.async = true;
    const params = new URLSearchParams({ key, v: "weekly", loading: "async" });
    script.src = `${GOOGLE_MAPS_JS_SRC}?${params.toString()}`;
    script.onload = finish;
    script.onerror = () => {
      mapsPromise = null;
      reject(new Error("Maps JS gagal dimuat"));
    };
    document.head.appendChild(script);
  });
  return mapsPromise;
}
