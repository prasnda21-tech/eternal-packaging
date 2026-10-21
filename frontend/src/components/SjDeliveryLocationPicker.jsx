import { useEffect, useLayoutEffect, useRef, useState } from "react";
import api from "../lib/api";
import { hasCanonicalDeliveryCoordinates } from "../lib/deliveryRouteBridge";
import {
  mapsLinkMessage,
  parseGoogleMapsUrlRecord,
} from "../lib/googleMapsLink";
import {
  applyMapsLinkSelection,
  applyPlacesSelection,
} from "../lib/outletLocationSelection";
import OutletLocationMap from "./OutletLocationMap";

function toPicked(loc) {
  if (!hasCanonicalDeliveryCoordinates(loc)) return null;
  const lat = Number(loc.lat);
  const lng = Number(loc.lng);
  const placeId = loc.place_id || loc.google_place_id || "";
  return {
    place_id: placeId,
    selection_key: loc.selection_key || placeId || `maps-link:${lat},${lng}`,
    formatted_address: loc.formatted_address || loc.address || `Koordinat ${lat}, ${lng}`,
    lat,
    lng,
  };
}

export function sjOverridePayload(override) {
  if (!hasCanonicalDeliveryCoordinates(override)) return null;
  const out = {
    lat: Number(override.lat),
    lng: Number(override.lng),
  };
  const placeId = String(override.place_id || override.google_place_id || "").trim();
  if (placeId) {
    out.place_id = placeId;
    out.google_place_id = placeId;
  }
  const address = String(override.formatted_address || "").trim();
  if (address) out.formatted_address = address;
  return out;
}

export default function SjDeliveryLocationPicker({
  poLocation = null,
  override = null,
  onOverrideChange,
  disabled = false,
  /** Create form: hide large map / "Alamat Terpilih" panel; keep Maps link + apply. */
  compact = false,
}) {
  const poPicked = toPicked(poLocation);
  const overridePicked = toPicked(override);
  const displayed = overridePicked || poPicked;
  const overrideActive = !!overridePicked;
  const canEdit = !disabled;

  const [query, setQuery] = useState("");
  const [mapsLink, setMapsLink] = useState("");
  const [mapsStatus, setMapsStatus] = useState("");
  const [mapsMessage, setMapsMessage] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [provider, setProvider] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [suggestBox, setSuggestBox] = useState(null);
  const searchInputRef = useRef(null);

  useEffect(() => {
    setQuery("");
    setMapsLink("");
    setMapsStatus("");
    setMapsMessage("");
    setSuggestions([]);
    setErr("");
  }, [poLocation, overrideActive]);

  useEffect(() => {
    if (query.trim().length < 2) {
      setSuggestions([]);
      return undefined;
    }
    const t = setTimeout(async () => {
      try {
        const d = await api.searchDeliveryPlaces(query.trim());
        setProvider(d.provider || "");
        setSuggestions(d.suggestions || []);
        setErr("");
      } catch (e) {
        setSuggestions([]);
        setErr(e.response?.data?.detail || "Gagal mencari alamat");
      }
    }, 280);
    return () => clearTimeout(t);
  }, [query]);

  useLayoutEffect(() => {
    if (!suggestions.length) {
      setSuggestBox(null);
      return undefined;
    }
    const syncSuggestBox = () => {
      const input = searchInputRef.current;
      if (!input) {
        setSuggestBox(null);
        return;
      }
      const r = input.getBoundingClientRect();
      setSuggestBox({
        top: Math.round(r.bottom + 4),
        left: Math.round(r.left),
        width: Math.max(Math.round(r.width), 220),
      });
    };
    syncSuggestBox();
    window.addEventListener("resize", syncSuggestBox);
    document.addEventListener("scroll", syncSuggestBox, true);
    return () => {
      window.removeEventListener("resize", syncSuggestBox);
      document.removeEventListener("scroll", syncSuggestBox, true);
    };
  }, [suggestions, query]);

  const commitOverride = (picked) => {
    if (!canEdit || !picked || !hasCanonicalDeliveryCoordinates(picked)) return;
    onOverrideChange?.(picked);
  };

  const selectPlace = async (sug) => {
    if (!canEdit) return;
    setBusy(true);
    setErr("");
    try {
      const d = await api.deliveryPlaceDetails(sug.place_id);
      const p = d.place || {};
      const next = applyPlacesSelection({
        place_id: p.place_id || p.google_place_id || sug.place_id,
        formatted_address: p.formatted_address || sug.formatted_address || sug.label,
        lat: p.lat,
        lng: p.lng,
        name: p.name || "",
      }, "");
      commitOverride(next.picked);
      setQuery("");
      setSuggestions([]);
      setMapsStatus("");
      setMapsMessage("");
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat detail lokasi");
    } finally {
      setBusy(false);
    }
  };

  const applyPastedMapsLink = async () => {
    if (!canEdit || busy) return;
    const inspected = parseGoogleMapsUrlRecord(mapsLink);
    if (inspected.status === "EMPTY") {
      setMapsStatus("");
      setMapsMessage("");
      return;
    }
    if (inspected.status === "OK") {
      const next = applyMapsLinkSelection(inspected, "", displayed);
      commitOverride(next.picked);
      setMapsStatus("SUCCESS");
      setMapsMessage(mapsLinkMessage("SUCCESS"));
      setErr("");
      return;
    }
    if (inspected.status !== "NEEDS_RESOLVE") {
      setMapsStatus(inspected.status);
      setMapsMessage(mapsLinkMessage(inspected.status));
      return;
    }

    const previous = overridePicked;
    setBusy(true);
    setMapsStatus("RESOLVING");
    setMapsMessage(mapsLinkMessage("RESOLVING"));
    setErr("");
    try {
      const resolved = await api.resolveDeliveryMapsLink(inspected.url);
      const lat = Number(resolved.lat);
      const lng = Number(resolved.lng);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        setMapsStatus("NO_COORDINATES");
        setMapsMessage(mapsLinkMessage("NO_COORDINATES"));
        return;
      }
      const next = applyMapsLinkSelection({
        lat,
        lng,
        formatted_address: resolved.formatted_address,
      }, "", previous);
      commitOverride(next.picked);
      setMapsStatus("SUCCESS");
      setMapsMessage(mapsLinkMessage("SUCCESS"));
    } catch (e) {
      const detail = e.response?.data?.detail;
      const code = typeof detail === "object" && detail?.code ? detail.code : "RESOLVE_FAILURE";
      setMapsStatus(code);
      setMapsMessage((typeof detail === "object" && detail?.message) || mapsLinkMessage(code));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`outlet-loc-form${compact ? " is-compact" : ""}`}
      data-testid="sj-titik-lokasi"
      data-compact={compact ? "true" : "false"}
      style={{ marginTop: compact ? 8 : 16 }}
    >
      <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
        Titik Lokasi Pengiriman
      </label>
      <p
        className="muted small"
        data-testid="sj-location-status"
        style={{ margin: "0 0 8px 0", color: overrideActive ? "#0369a1" : "#64748b", fontWeight: overrideActive ? 600 : 400 }}
      >
        {overrideActive
          ? "Menggunakan titik lokasi Surat Jalan"
          : poPicked
            ? "Menggunakan titik lokasi PO (fallback)"
            : "Belum ada titik lokasi. Opsional — isi untuk override PO."}
      </p>
      <label className="outlet-loc-search-field">
        <span>Cari Outlet / Alamat</span>
        <input
          ref={searchInputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="sj-loc-search"
          placeholder="JUS.IN Kelapa Gading atau Jl. Boulevard Raya"
          autoComplete="off"
          disabled={!canEdit || busy}
        />
      </label>
      {provider === "google" && <p className="muted small">Powered by Google</p>}
      {provider === "mock" && <p className="muted small">Pencarian lokasi preview (mock lokal)</p>}
      <p className="outlet-loc-or">ATAU</p>
      <label>
        <span>Link Google Maps</span>
        <div className="outlet-maps-link-row">
          <input
            value={mapsLink}
            onChange={(e) => {
              setMapsLink(e.target.value);
              if (mapsStatus) {
                setMapsStatus("");
                setMapsMessage("");
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                e.stopPropagation();
                applyPastedMapsLink();
              }
            }}
            data-testid="sj-loc-maps-link"
            placeholder="Tempel link Google Maps di sini"
            autoComplete="off"
            disabled={!canEdit || busy}
          />
          <button
            type="button"
            className="ghost"
            onClick={applyPastedMapsLink}
            disabled={!canEdit || busy}
            data-testid="sj-loc-use-maps-link"
          >
            Gunakan Lokasi
          </button>
        </div>
      </label>
      {mapsMessage && (
        <p className={mapsStatus === "SUCCESS" ? "muted small" : "error"} data-testid="sj-loc-maps-status">
          {mapsMessage}
        </p>
      )}
      {compact ? (
        <p
          className="muted small"
          data-testid="sj-loc-selected"
          style={{ margin: "6px 0 0", lineHeight: 1.35 }}
          title={displayed?.formatted_address || ""}
        >
          {displayed
            ? `${displayed.formatted_address}${Number.isFinite(displayed.lat) ? ` · ${displayed.lat.toFixed(5)}, ${displayed.lng.toFixed(5)}` : ""}`
            : "Belum ada koordinat — tempel link lalu Gunakan Lokasi."}
        </p>
      ) : (
        <>
          <div>
            <span className="muted small">Alamat Terpilih</span>
            <p data-testid="sj-loc-selected">{displayed?.formatted_address || "Belum dipilih"}</p>
          </div>
          <OutletLocationMap
            lat={displayed?.lat}
            lng={displayed?.lng}
            placeKey={displayed?.selection_key || displayed?.place_id || ""}
            canEdit={canEdit}
            onMove={(nextLat, nextLng) => {
              if (!canEdit) return;
              commitOverride({
                ...(displayed || {}),
                lat: nextLat,
                lng: nextLng,
                selection_key: `maps-link:${nextLat},${nextLng}`,
              });
            }}
          />
          {displayed?.lat != null && canEdit && (
            <p className="muted small">Klik peta atau geser marker untuk set titik Surat Jalan. Koordinat terisi otomatis.</p>
          )}
        </>
      )}
      {overrideActive && (
        <button
          type="button"
          className="ghost sm"
          onClick={() => onOverrideChange?.(null)}
          disabled={!canEdit || busy}
          data-testid="sj-location-clear"
          style={{ marginTop: 8 }}
        >
          Hapus override — kembali ke lokasi PO
        </button>
      )}
      {err && <p className="error" data-testid="sj-loc-error">{err}</p>}
      {!!suggestions.length && (
        <ul
          className="outlet-place-suggest"
          data-testid="sj-loc-suggest"
          style={suggestBox || undefined}
        >
          {suggestions.map((s) => (
            <li key={s.place_id}>
              <button type="button" onClick={() => selectPlace(s)} data-testid={`sj-loc-pick-${s.place_id}`}>
                <span className="outlet-place-primary">{s.label}</span>
                {s.formatted_address && s.formatted_address !== s.label ? (
                  <span className="outlet-place-secondary">{s.formatted_address}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
