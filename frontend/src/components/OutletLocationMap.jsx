import { useEffect, useRef, useState } from "react";
import {
  googleMapsBrowserKey,
  latLngToPixel,
  loadGoogleMapsJs,
  pixelToLatLng,
  tilesForView,
} from "../lib/outletMap";

function OsmPreview({ lat, lng, placeKey, canEdit, onMove }) {
  const wrapRef = useRef(null);
  const onMoveRef = useRef(onMove);
  onMoveRef.current = onMove;
  const [size, setSize] = useState({ width: 480, height: 240 });
  const [viewCenter, setViewCenter] = useState({ lat: Number(lat), lng: Number(lng) });
  const lastPlace = useRef(placeKey);
  const dragging = useRef(false);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const apply = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0) setSize({ width: Math.round(rect.width), height: Math.round(rect.height) });
    };
    apply();
    const obs = typeof ResizeObserver !== "undefined" ? new ResizeObserver(apply) : null;
    obs?.observe(el);
    return () => obs?.disconnect();
  }, []);

  useEffect(() => {
    if (lat == null || lng == null) return;
    if (lastPlace.current !== placeKey) {
      lastPlace.current = placeKey;
      setViewCenter({ lat: Number(lat), lng: Number(lng) });
    }
  }, [placeKey, lat, lng]);

  const tiles = tilesForView(viewCenter, size.width, size.height);
  const pin = latLngToPixel(Number(lat), Number(lng), viewCenter, size.width, size.height);

  const moveFromEvent = (clientX, clientY) => {
    const el = wrapRef.current;
    if (!el || !canEdit) return;
    const rect = el.getBoundingClientRect();
    const next = pixelToLatLng(
      clientX - rect.left,
      clientY - rect.top,
      viewCenter,
      rect.width,
      rect.height,
    );
    onMoveRef.current?.(next.lat, next.lng);
  };

  return (
    <div
      ref={wrapRef}
      className="outlet-map-preview outlet-map-interactive"
      data-testid="outlet-loc-map"
      onClick={(e) => {
        if (dragging.current) return;
        moveFromEvent(e.clientX, e.clientY);
      }}
    >
      {tiles.map((t) => (
        <img
          key={t.key}
          alt=""
          src={t.src}
          draggable={false}
          className="outlet-map-tile"
          style={{ left: t.left, top: t.top }}
        />
      ))}
      <button
        type="button"
        className="outlet-map-marker"
        data-testid="outlet-loc-marker"
        aria-label="Marker lokasi outlet"
        style={{ left: pin.x, top: pin.y }}
        disabled={!canEdit}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => {
          if (!canEdit) return;
          e.preventDefault();
          e.stopPropagation();
          dragging.current = true;
          const onMoveDoc = (ev) => moveFromEvent(ev.clientX, ev.clientY);
          const onUp = () => {
            window.setTimeout(() => { dragging.current = false; }, 0);
            document.removeEventListener("mousemove", onMoveDoc);
            document.removeEventListener("mouseup", onUp);
          };
          document.addEventListener("mousemove", onMoveDoc);
          document.addEventListener("mouseup", onUp);
        }}
      />
      <span className="outlet-map-attrib">© OpenStreetMap</span>
    </div>
  );
}

function GooglePreview({ lat, lng, placeKey, canEdit, onMove }) {
  const elRef = useRef(null);
  const mapRef = useRef(null);
  const markerRef = useRef(null);
  const onMoveRef = useRef(onMove);
  onMoveRef.current = onMove;
  const lastPlace = useRef(placeKey);

  useEffect(() => {
    let cancelled = false;
    const key = googleMapsBrowserKey();
    loadGoogleMapsJs(key)
      .then(async (mapsApi) => {
        if (cancelled || !elRef.current) return;
        const mapsLib = mapsApi.importLibrary ? await mapsApi.importLibrary("maps") : mapsApi;
        const MapCtor = mapsLib.Map || window.google.maps.Map;
        const map = new MapCtor(elRef.current, {
          center: { lat: Number(lat), lng: Number(lng) },
          zoom: 16,
          mapId: "DEMO_MAP_ID",
          disableDefaultUI: true,
          zoomControl: true,
        });
        mapRef.current = map;
        let marker;
        try {
          const markerLib = await window.google.maps.importLibrary("marker");
          const Advanced = markerLib.AdvancedMarkerElement;
          marker = new Advanced({
            map,
            position: { lat: Number(lat), lng: Number(lng) },
            gmpDraggable: !!canEdit,
            title: "Lokasi outlet",
          });
          marker.addListener("dragend", () => {
            const pos = marker.position;
            const nextLat = typeof pos.lat === "function" ? pos.lat() : pos.lat;
            const nextLng = typeof pos.lng === "function" ? pos.lng() : pos.lng;
            onMoveRef.current?.(Number(nextLat), Number(nextLng));
          });
        } catch {
          marker = new window.google.maps.Marker({
            map,
            position: { lat: Number(lat), lng: Number(lng) },
            draggable: !!canEdit,
            title: "Lokasi outlet",
          });
          marker.addListener("dragend", () => {
            const pos = marker.getPosition();
            onMoveRef.current?.(pos.lat(), pos.lng());
          });
        }
        markerRef.current = marker;
        if (canEdit) {
          map.addListener("click", (ev) => {
            const pos = ev.latLng;
            if (!pos) return;
            if (marker.position !== undefined) marker.position = pos;
            else marker.setPosition(pos);
            onMoveRef.current?.(pos.lat(), pos.lng());
          });
        }
      })
      .catch(() => {
        mapRef.current = null;
      });
    return () => {
      cancelled = true;
    };
    // Map is created once per mount; position updates happen in the next effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canEdit]);

  useEffect(() => {
    const map = mapRef.current;
    const marker = markerRef.current;
    if (!map || !marker || lat == null || lng == null) return;
    const pos = { lat: Number(lat), lng: Number(lng) };
    if (marker.position !== undefined) marker.position = pos;
    else if (marker.setPosition) marker.setPosition(pos);
    if (lastPlace.current !== placeKey) {
      lastPlace.current = placeKey;
      if (map.setCenter) map.setCenter(pos);
    }
  }, [lat, lng, placeKey]);

  return (
    <div className="outlet-map-preview outlet-map-google" data-testid="outlet-loc-map">
      <div ref={elRef} className="outlet-map-google-canvas" data-testid="outlet-loc-marker" />
    </div>
  );
}

export default function OutletLocationMap({ lat, lng, placeKey = "", canEdit = true, onMove }) {
  const [googleOk, setGoogleOk] = useState(false);

  useEffect(() => {
    const key = googleMapsBrowserKey();
    if (!key) {
      setGoogleOk(false);
      return undefined;
    }
    let live = true;
    loadGoogleMapsJs(key)
      .then(() => {
        if (live) setGoogleOk(true);
      })
      .catch(() => {
        if (live) setGoogleOk(false);
      });
    return () => {
      live = false;
    };
  }, []);

  if (lat == null || lng == null) {
    return (
      <div className="outlet-map-preview is-empty" data-testid="outlet-loc-map-empty">
        Pilih alamat untuk melihat peta
      </div>
    );
  }
  if (googleOk) {
    return <GooglePreview lat={lat} lng={lng} placeKey={placeKey} canEdit={canEdit} onMove={onMove} />;
  }
  return <OsmPreview lat={lat} lng={lng} placeKey={placeKey} canEdit={canEdit} onMove={onMove} />;
}
