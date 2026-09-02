import { useEffect, useState } from "react";
import api from "../lib/api";
import RoutePlannerMap from "./RoutePlannerMap";
import {
  GOOGLE_PLACES_REQUIRED_CONFIG,
  GOOGLE_PLACES_REQUIRED_CONFIG_FALLBACK,
  GOOGLE_PLACES_UNCONFIGURED,
  GOOGLE_SEARCH_DEBOUNCE_MS,
  confirmGooglePlace,
  formatCoord,
  googlePlaceCandidateFromDetails,
  isValidCoordinate,
  mapsLinkForStop,
  parsePlacesStatus,
  parsePlacesSuggestions,
  shouldQueryPlaces,
} from "../lib/independentRoute";

export default function GooglePlacePicker({
  onConfirm,
  testidPrefix = "iro-gmaps",
  mapHeight = 220,
}) {
  const [status, setStatus] = useState(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [candidate, setCandidate] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [err, setErr] = useState("");
  const [empty, setEmpty] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api.independentRoutePlacesStatus()
      .then((payload) => {
        if (alive) setStatus(parsePlacesStatus(payload));
      })
      .catch(() => {
        if (alive) {
          setStatus(parsePlacesStatus({ configured: false, message: GOOGLE_PLACES_UNCONFIGURED }));
        }
      });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!status?.configured || !shouldQueryPlaces(query)) {
      setResults([]);
      setEmpty(false);
      return undefined;
    }
    const handle = setTimeout(async () => {
      try {
        const payload = await api.searchIndependentRoutePlaces(query.trim());
        const parsed = parsePlacesStatus(payload);
        if (!parsed.configured) {
          setStatus(parsed);
          setResults([]);
          setEmpty(false);
          return;
        }
        const suggestions = parsePlacesSuggestions(payload);
        setResults(suggestions);
        setEmpty(suggestions.length === 0);
        setErr("");
      } catch (e) {
        setResults([]);
        setEmpty(false);
        setErr(e?.response?.data?.detail || "Pencarian Google Maps gagal. Coba lagi atau pakai titik peta / koordinat.");
      }
    }, GOOGLE_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query, status?.configured]);

  const selectResult = async (sug) => {
    setBusy(true);
    setErr("");
    setConfirmed(false);
    try {
      const payload = await api.independentRoutePlaceDetails(sug.place_id);
      const next = googlePlaceCandidateFromDetails(payload?.place || {
        place_id: sug.place_id,
        name: sug.label,
        formatted_address: sug.formatted_address,
      });
      if (!next) {
        setCandidate(null);
        setErr("Detail tempat tidak memiliki koordinat valid.");
        return;
      }
      setCandidate(next);
    } catch (e) {
      setCandidate(null);
      setErr(e?.response?.data?.detail || "Detail tempat Google Maps gagal dimuat.");
    } finally {
      setBusy(false);
    }
  };

  const confirm = () => {
    const snap = confirmGooglePlace(candidate);
    if (!snap) return;
    setConfirmed(true);
    onConfirm?.(snap);
  };

  if (status && !status.configured) {
    return (
      <div className="iro-gmaps is-off" data-testid={`${testidPrefix}-unconfigured`}>
        <p className="iro-alert is-warn">{GOOGLE_PLACES_UNCONFIGURED}</p>
        <p className="iro-muted">
          Butuh {GOOGLE_PLACES_REQUIRED_CONFIG} di backend (cadangan {GOOGLE_PLACES_REQUIRED_CONFIG_FALLBACK}).
          Titik peta, koordinat manual, dan lokasi saya tetap bisa dipakai.
        </p>
      </div>
    );
  }

  return (
    <div className="iro-gmaps" data-testid={`${testidPrefix}-box`}>
      <label>Cari Lokasi di Google Maps
        <input
          value={query}
          onChange={(e) => { setQuery(e.target.value); setConfirmed(false); }}
          placeholder="Cari nama outlet, alamat, atau tempat..."
          data-testid={`${testidPrefix}-query`}
        />
      </label>
      {busy ? <p className="iro-muted">Memuat detail tempat…</p> : null}
      {err ? <div className="iro-alert is-error" data-testid={`${testidPrefix}-error`}>{err}</div> : null}
      {empty ? <p className="iro-muted" data-testid={`${testidPrefix}-empty`}>Tidak ada hasil Google Maps.</p> : null}
      {results.length > 0 && (
        <ul className="iro-hits iro-gmaps-hits" data-testid={`${testidPrefix}-results`}>
          {results.map((s) => (
            <li key={s.place_id}>
              <button type="button" onClick={() => selectResult(s)} data-testid={`${testidPrefix}-hit-${s.place_id}`}>
                <strong>{s.label}</strong>
                <span>{s.formatted_address || s.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {candidate && (
        <div className="iro-picked" data-testid={`${testidPrefix}-picked`}>
          <h4>LOKASI TERPILIH</h4>
          <p><b>Nama:</b> {candidate.place_name || "—"}</p>
          <p><b>Alamat:</b> {candidate.formatted_address || "—"}</p>
          <p className="iro-coord-line" data-testid={`${testidPrefix}-coord`}>
            <b>Koordinat:</b> {formatCoord(candidate.lat, candidate.lng)}
          </p>
          <p><b>Source:</b> Google Maps</p>
          {mapsLinkForStop(candidate) ? (
            <a className="iro-link" href={mapsLinkForStop(candidate)} target="_blank" rel="noreferrer" data-testid={`${testidPrefix}-open`}>
              Buka di Google Maps
            </a>
          ) : null}
          {isValidCoordinate(candidate.lat, candidate.lng) && (
            <RoutePlannerMap
              points={[{ lat: candidate.lat, lng: candidate.lng, kind: "STOP", seq: "•", label: candidate.place_name }]}
              height={mapHeight}
            />
          )}
          <button
            type="button"
            className="iro-btn-primary"
            onClick={confirm}
            data-testid={`${testidPrefix}-confirm`}
          >
            Gunakan Titik Ini
          </button>
          {confirmed ? <p className="iro-muted" data-testid={`${testidPrefix}-confirmed`}>Titik dikonfirmasi. Simpan stop untuk menyimpan.</p> : null}
        </div>
      )}
    </div>
  );
}
