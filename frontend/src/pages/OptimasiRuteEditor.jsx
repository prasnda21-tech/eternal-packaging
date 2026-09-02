import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowDown, ArrowUp, MapPin, Navigation, Plus, RefreshCw, Save, Sparkles } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import GooglePlacePicker from "../components/GooglePlacePicker";
import RoutePlannerMap from "../components/RoutePlannerMap";
import {
  DEFAULT_PRIORITIES,
  END_MODES,
  LOCATION_PICK_MODES,
  LOCATION_SOURCES,
  ROUTE_PERMS,
  START_MODES,
  STRATEGIES,
  accuracyWarning,
  applyMove,
  canMoveStop,
  captureGeolocation,
  formatAccuracy,
  formatCoord,
  formatKm,
  googleMapsDirUrl,
  isValidCoordinate,
  mapsLinkForStop,
  missingLocationStops,
  startModeLabel,
  statusLabel,
} from "../lib/independentRoute";

const emptyDraft = {
  name: "",
  route_date: "",
  notes: "",
  start_mode: "CURRENT_LOCATION",
  end_mode: "LAST_STOP",
  strategy: "NEAREST",
  start_location: null,
  end_location: null,
};

export default function OptimasiRuteEditor() {
  const { id } = useParams();
  const isNew = !id || id === "baru";
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const [route, setRoute] = useState(null);
  const [settings, setSettings] = useState(null);
  const [draft, setDraft] = useState(emptyDraft);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [gps, setGps] = useState(null);
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState(null);
  const [history, setHistory] = useState([]);

  const canEdit = hasPerm(ROUTE_PERMS.editOwn) || hasPerm(ROUTE_PERMS.manage) || hasPerm(ROUTE_PERMS.create);
  const canOptimize = hasPerm(ROUTE_PERMS.optimize) || hasPerm(ROUTE_PERMS.manage);
  const levels = settings?.priority_levels?.length ? settings.priority_levels.filter((p) => p.enabled) : DEFAULT_PRIORITIES;

  const load = async (routeId) => {
    const [s, r, v] = await Promise.all([
      api.independentRouteSettings(),
      api.getIndependentRoute(routeId),
      api.listIndependentRouteVersions(routeId).catch(() => ({ versions: [] })),
    ]);
    setSettings(s.settings);
    setRoute(r.route);
    setDraft({
      name: r.route.name,
      route_date: r.route.route_date || "",
      notes: r.route.notes || "",
      start_mode: r.route.start_mode,
      end_mode: r.route.end_mode,
      strategy: r.route.strategy,
      start_location: r.route.start_location,
      end_location: r.route.end_location,
    });
    setHistory(v.versions || []);
  };

  useEffect(() => {
    api.independentRouteSettings().then((r) => {
      setSettings(r.settings);
      if (isNew) {
        setDraft((d) => ({
          ...d,
          start_mode: r.settings.default_start_mode || d.start_mode,
          end_mode: r.settings.default_end_mode || d.end_mode,
          strategy: r.settings.default_strategy || d.strategy,
        }));
      }
    }).catch(() => {});
    if (!isNew) {
      load(id).catch((e) => setError(e?.response?.data?.detail || "Gagal memuat rute."));
    }
  }, [id, isNew]);

  const warehouse = settings?.warehouse;
  const warehouseOk = isValidCoordinate(warehouse?.lat, warehouse?.lng);
  const stops = useMemo(() => route?.stops || [], [route?.stops]);
  const missing = missingLocationStops(stops);
  const p1Count = stops.filter((s) => s.priority_id === "p1" || s.priority_rank === 1).length;

  const mapPoints = useMemo(() => {
    const pts = [];
    const start = draft.start_location;
    if (draft.start_mode === "WAREHOUSE" && warehouseOk) pts.push({ kind: "START", lat: warehouse.lat, lng: warehouse.lng, seq: "S", label: "Gudang" });
    else if (isValidCoordinate(start?.lat, start?.lng)) pts.push({ kind: "START", lat: start.lat, lng: start.lng, seq: "S", label: "Start" });
    stops.forEach((s, i) => {
      if (isValidCoordinate(s.lat, s.lng)) {
        pts.push({
          id: s.id,
          kind: "STOP",
          lat: s.lat,
          lng: s.lng,
          seq: s.display_order || i + 1,
          priority_id: s.priority_id,
          label: s.display_name,
        });
      }
    });
    if (draft.end_mode === "WAREHOUSE" && warehouseOk) pts.push({ kind: "END", lat: warehouse.lat, lng: warehouse.lng, seq: "E", label: "Gudang" });
    else if (draft.end_mode === "MANUAL" && isValidCoordinate(draft.end_location?.lat, draft.end_location?.lng)) {
      pts.push({ kind: "END", lat: draft.end_location.lat, lng: draft.end_location.lng, seq: "E", label: "End" });
    }
    if (gps?.ok) pts.push({ kind: "ME", lat: gps.lat, lng: gps.lng, seq: "●", label: "Lokasi Saya" });
    return pts;
  }, [draft, stops, warehouse, warehouseOk, gps]);

  const notify = (msg) => {
    setInfo(msg);
    setTimeout(() => setInfo(""), 3500);
  };

  const saveSetup = async () => {
    setError("");
    setBusy(true);
    try {
      if (isNew) {
        const r = await api.createIndependentRoute({ ...draft, name: draft.name || `Rute ${new Date().toLocaleDateString("id-ID")}` });
        navigate(`/optimasi-rute/${r.route.id}`, { replace: true });
        return;
      }
      const r = await api.updateIndependentRoute(route.id, { ...draft, revision: route.revision });
      setRoute(r.route);
      notify("Rute disimpan.");
    } catch (e) {
      setError(e?.response?.data?.detail || "Gagal menyimpan rute.");
    } finally {
      setBusy(false);
    }
  };

  const grabGps = async () => {
    const result = await captureGeolocation();
    setGps(result);
    if (!result.ok) {
      setError(result.message);
      return;
    }
    setError("");
    setDraft((d) => ({ ...d, start_mode: "CURRENT_LOCATION", start_location: result }));
    notify("Lokasi Saya ditangkap.");
  };

  const optimize = async () => {
    if (!route) return;
    setError("");
    setBusy(true);
    try {
      const r = await api.optimizeIndependentRoute(route.id, {
        revision: route.revision,
        start_mode: draft.start_mode,
        end_mode: draft.end_mode,
        strategy: draft.strategy,
        start_location: draft.start_location,
        end_location: draft.end_location,
      });
      setRoute(r.route);
      setHistory((h) => [r.version, ...h]);
      notify("Urutan dioptimasi. Koordinat dan prioritas tidak berubah.");
    } catch (e) {
      setError(e?.response?.data?.detail || "Optimasi ditolak.");
    } finally {
      setBusy(false);
    }
  };

  const move = async (index, dir) => {
    if (!canMoveStop(stops, index, dir)) {
      setError("Urutan tidak boleh menyeberangi prioritas. Ubah prioritas stop dulu jika berwenang.");
      return;
    }
    const next = applyMove(stops, index, dir);
    setBusy(true);
    try {
      const r = await api.reorderIndependentRoute(route.id, {
        ordered_stop_ids: next.map((s) => s.id),
        revision: route.revision,
      });
      setRoute(r.route);
      notify("Urutan manual disimpan.");
    } catch (e) {
      setError(e?.response?.data?.detail || "Reorder ditolak.");
    } finally {
      setBusy(false);
    }
  };

  const dirPoints = useMemo(() => {
    const pts = [];
    const startPt = mapPoints.find((p) => p.kind === "START");
    if (startPt) pts.push(startPt);
    stops.filter((s) => isValidCoordinate(s.lat, s.lng)).sort((a, b) => (a.display_order || 0) - (b.display_order || 0)).forEach((s) => pts.push(s));
    const endPt = mapPoints.find((p) => p.kind === "END");
    if (endPt) pts.push(endPt);
    return pts;
  }, [mapPoints, stops]);

  return (
    <div className="iro-page iro-editor" data-testid="iro-editor-page">
      <div className="iro-hero">
        <div>
          <Link to="/optimasi-rute" className="iro-back">← Daftar rute</Link>
          <h1>{isNew ? "Buat Rute" : draft.name || "Detail Rute"}</h1>
          {!isNew && (
            <div className="iro-hero-meta">
              <span className={`iro-status is-${(route?.status || "draft").toLowerCase()}`}>{statusLabel(route?.status)}</span>
              <span className="iro-pill">{STRATEGIES.find((s) => s.id === draft.strategy)?.label}</span>
              {route?.optimization_stale ? <span className="iro-pill is-warn">Hasil optimasi usang</span> : null}
              {route?.sequence_overridden ? <span className="iro-pill is-manual">Urutan manual</span> : null}
            </div>
          )}
        </div>
        <div className="iro-hero-actions">
          {canEdit && (
            <button type="button" className="iro-btn-secondary" onClick={saveSetup} disabled={busy} data-testid="iro-save-route">
              <Save size={16} /> Simpan Rute
            </button>
          )}
          {!isNew && canOptimize && (
            <button type="button" className="iro-btn-primary" onClick={optimize} disabled={busy} data-testid="iro-optimize-btn">
              {route?.current_optimization_version ? <RefreshCw size={16} /> : <Sparkles size={16} />}
              {route?.current_optimization_version ? "Optimize Ulang" : "Optimize Route"}
            </button>
          )}
        </div>
      </div>

      {error ? <div className="iro-alert is-error" data-testid="iro-error">{error}</div> : null}
      {info ? <div className="iro-alert is-ok">{info}</div> : null}

      <section className="iro-card" data-testid="iro-setup">
        <h3>Route Setup</h3>
        <div className="iro-grid-3">
          <label>Nama rute
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} data-testid="iro-name" />
          </label>
          <label>Tanggal
            <input type="date" value={draft.route_date || ""} onChange={(e) => setDraft({ ...draft, route_date: e.target.value })} />
          </label>
          <label>Strategi
            <select value={draft.strategy} onChange={(e) => setDraft({ ...draft, strategy: e.target.value })} data-testid="iro-strategy">
              {STRATEGIES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </label>
        </div>
        <div className="iro-mode-row">
          {START_MODES.map((m) => (
            <button key={m.id} type="button" className={`iro-mode ${draft.start_mode === m.id ? "is-on" : ""}`} data-testid={`iro-start-${m.id}`} onClick={() => setDraft({ ...draft, start_mode: m.id })}>
              <strong>Start · {m.label}</strong>
              <span>{m.hint}</span>
            </button>
          ))}
        </div>
        <div className="iro-mode-row">
          {END_MODES.map((m) => (
            <button key={m.id} type="button" className={`iro-mode is-end ${draft.end_mode === m.id ? "is-on" : ""}`} data-testid={`iro-end-${m.id}`} onClick={() => setDraft({ ...draft, end_mode: m.id })}>
              {m.label}
            </button>
          ))}
        </div>
        {draft.start_mode === "CURRENT_LOCATION" && (
          <div className="iro-gps-box" data-testid="iro-gps-box">
            <button type="button" className="iro-btn-primary" onClick={grabGps} data-testid="iro-gps-btn">Ambil Lokasi Saya</button>
            {gps?.ok ? (
              <p>
                {formatCoord(gps.lat, gps.lng)} · {formatAccuracy(gps.accuracy)}
                {accuracyWarning(gps.accuracy) ? <span className="iro-warn"> Akurasi rendah — koordinat tidak dikoreksi otomatis.</span> : null}
              </p>
            ) : gps ? <p className="iro-warn">{gps.message}</p> : <p>GPS hanya dipakai setelah izin diberikan. Tidak ada koordinat fiktif.</p>}
          </div>
        )}
        {draft.start_mode === "WAREHOUSE" && !warehouseOk && (
          <div className="iro-alert is-warn" data-testid="iro-warehouse-start-missing">Lokasi Gudang belum dikonfigurasi</div>
        )}
        {draft.start_mode === "MANUAL" && (
          <ManualPoint
            label="Titik awal"
            point={draft.start_location}
            onChange={(p) => setDraft({ ...draft, start_location: p })}
            testid="iro-start-manual"
          />
        )}
        {draft.end_mode === "WAREHOUSE" && !warehouseOk && (
          <div className="iro-alert is-warn">Lokasi Gudang belum dikonfigurasi</div>
        )}
        {draft.end_mode === "MANUAL" && (
          <ManualPoint
            label="Titik akhir"
            point={draft.end_location}
            onChange={(p) => setDraft({ ...draft, end_location: p })}
            testid="iro-end-manual"
          />
        )}
      </section>

      {!isNew && (
        <>
          <div className="iro-summary" data-testid="iro-summary">
            <SummaryCard tone="teal" label="Stop" value={stops.length} />
            <SummaryCard tone="rose" label="Prioritas P1" value={p1Count} />
            <SummaryCard tone="cyan" label="Estimasi jarak" value={formatKm(route?.estimated_distance_m)} note="antar titik" />
            <SummaryCard tone="blue" label="Start" value={startModeLabel(draft.start_mode)} />
            <SummaryCard tone="purple" label="End" value={END_MODES.find((m) => m.id === draft.end_mode)?.label || draft.end_mode} />
          </div>

          <section className="iro-card">
            <div className="iro-card-head">
              <h3>Customer / Outlet</h3>
              {canEdit && (
                <button type="button" className="iro-btn-primary" onClick={() => setModal({ step: 1 })} data-testid="iro-add-stop">
                  <Plus size={16} /> Tambah Stop
                </button>
              )}
            </div>
            <div className="iro-legend" data-testid="iro-priority-legend">
              {levels.map((p) => (
                <span key={p.id} className="iro-chip" style={{ background: p.color }}>{String(p.id).toUpperCase()} · {p.label}</span>
              ))}
            </div>
            {missing.length > 0 && (
              <div className="iro-alert is-warn" data-testid="iro-missing-location">
                {missing.length} customer belum memiliki titik lokasi.
              </div>
            )}
          </section>

          <RoutePlannerMap points={mapPoints} height={440} />

          <section className="iro-card" data-testid="iro-stop-list">
            <div className="iro-card-head">
              <h3>Daftar stop</h3>
              {dirPoints.length >= 2 && (
                <a className="iro-link" href={googleMapsDirUrl(dirPoints)} target="_blank" rel="noreferrer" data-testid="iro-open-route-maps">
                  <Navigation size={14} /> Buka rute di Maps
                </a>
              )}
            </div>
            {stops.length === 0 ? <p className="iro-empty">Belum ada stop. Tambah customer/outlet atau stop khusus rute.</p> : null}
            {stops.map((stop, index) => (
              <article key={stop.id} className="iro-stop-card" data-testid={`iro-stop-${stop.id}`}>
                <div className="iro-stop-seq">{stop.display_order || index + 1}</div>
                <div className="iro-stop-body">
                  <div className="iro-stop-top">
                    <strong>{stop.display_name}</strong>
                    <span className="iro-chip" style={{ background: stop.priority_color || "#64748b" }}>
                      {String(stop.priority_id || "P").toUpperCase()} · {stop.priority_label}
                    </span>
                    {stop.ref_kind === "ROUTE_ONLY" ? <span className="iro-pill">Stop khusus rute</span> : <span className="iro-pill">Referensi master</span>}
                  </div>
                  <p className="iro-coord-line">{stop.location_set ? formatCoord(stop.lat, stop.lng) : "LOKASI BELUM DISET"}</p>
                  {stop.location_source === LOCATION_SOURCES.GOOGLE_PLACES ? <p className="iro-muted">Source: Google Maps</p> : null}
                  {stop.address || stop.formatted_address ? <p className="iro-muted">{stop.formatted_address || stop.address}</p> : null}
                </div>
                <div className="iro-stop-actions">
                  <button type="button" disabled={!canMoveStop(stops, index, -1)} onClick={() => move(index, -1)} data-testid={`iro-up-${stop.id}`}><ArrowUp size={14} /></button>
                  <button type="button" disabled={!canMoveStop(stops, index, 1)} onClick={() => move(index, 1)} data-testid={`iro-down-${stop.id}`}><ArrowDown size={14} /></button>
                  {isValidCoordinate(stop.lat, stop.lng) && (
                    <a className="iro-link" href={mapsLinkForStop(stop)} target="_blank" rel="noreferrer" data-testid={`iro-maps-${stop.id}`}>
                      <MapPin size={14} /> Buka Stop di Maps
                    </a>
                  )}
                  {canEdit && (
                    <button type="button" className="iro-linkish" onClick={() => setModal({ step: 2, edit: stop })} data-testid={`iro-edit-loc-${stop.id}`}>Ubah Titik</button>
                  )}
                </div>
              </article>
            ))}
          </section>

          {history.length > 0 && (
            <section className="iro-card" data-testid="iro-history">
              <h3>Riwayat optimasi</h3>
              <ul className="iro-history">
                {history.map((v) => (
                  <li key={v.id}>v{v.optimization_version} · {v.strategy} · {formatKm(v.total_estimated_m)} · {String(v.optimized_at || "").replace("T", " ").slice(0, 16)}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {modal && (
        <StopWizard
          levels={levels}
          initial={modal.edit}
          step={modal.step}
          onClose={() => setModal(null)}
          onSave={async (payload) => {
            if (modal.edit) {
              const r = await api.updateIndependentStop(route.id, modal.edit.id, payload);
              setRoute(r.route);
            } else {
              const r = await api.addIndependentStop(route.id, payload);
              setRoute(r.route);
            }
            setModal(null);
            notify("Stop disimpan. Titik ini otoritatif untuk rute ini.");
          }}
        />
      )}
    </div>
  );
}

function SummaryCard({ tone, label, value, note }) {
  return (
    <div className={`iro-sum is-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {note ? <em>{note}</em> : null}
    </div>
  );
}

function pickModeFromSource(source) {
  if (source === LOCATION_SOURCES.GOOGLE_PLACES) return "GOOGLE";
  if (source === LOCATION_SOURCES.MANUAL_COORDINATE) return "MANUAL";
  if (source === LOCATION_SOURCES.CURRENT_LOCATION) return "GPS";
  return "MAP";
}

function LocationModeTabs({ mode, onChange, testidPrefix }) {
  return (
    <div className="iro-loc-modes" data-testid={`${testidPrefix}-modes`}>
      {LOCATION_PICK_MODES.map((m) => (
        <button
          key={m.id}
          type="button"
          className={`iro-loc-mode ${mode === m.id ? "is-on" : ""}`}
          data-testid={`${testidPrefix}-mode-${m.id}`}
          onClick={() => onChange(m.id)}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

function ManualPoint({ label, point, onChange, testid }) {
  const [lat, setLat] = useState(point?.lat ?? "");
  const [lng, setLng] = useState(point?.lng ?? "");
  const [mode, setMode] = useState(pickModeFromSource(point?.location_source));
  const [gpsErr, setGpsErr] = useState("");

  const applyManual = (nextLat, nextLng, source, extra = {}) => {
    setLat(nextLat);
    setLng(nextLng);
    if (isValidCoordinate(nextLat, nextLng)) {
      onChange({ lat: Number(nextLat), lng: Number(nextLng), location_source: source, ...extra });
    }
  };

  const grabHere = async () => {
    const result = await captureGeolocation();
    if (!result.ok) {
      setGpsErr(result.message);
      return;
    }
    setGpsErr("");
    applyManual(result.lat, result.lng, LOCATION_SOURCES.CURRENT_LOCATION, {
      accuracy_m: result.accuracy,
      captured_at: result.captured_at,
    });
  };

  return (
    <div className="iro-manual" data-testid={testid}>
      <p>{label}: {isValidCoordinate(lat, lng) ? formatCoord(lat, lng) : "belum diisi"}</p>
      <LocationModeTabs mode={mode} onChange={setMode} testidPrefix={testid} />
      {mode === "GOOGLE" && (
        <GooglePlacePicker
          testidPrefix={`${testid}-gmaps`}
          onConfirm={(snap) => applyManual(snap.lat, snap.lng, LOCATION_SOURCES.GOOGLE_PLACES, {
            google_place_id: snap.google_place_id,
            place_name: snap.place_name,
            formatted_address: snap.formatted_address,
            google_maps_url: snap.google_maps_url,
          })}
        />
      )}
      {mode === "MANUAL" && (
        <div className="iro-grid-2">
          <label>Latitude<input value={lat} onChange={(e) => applyManual(e.target.value, lng, LOCATION_SOURCES.MANUAL_COORDINATE)} /></label>
          <label>Longitude<input value={lng} onChange={(e) => applyManual(lat, e.target.value, LOCATION_SOURCES.MANUAL_COORDINATE)} /></label>
        </div>
      )}
      {mode === "GPS" && (
        <div>
          <button type="button" className="iro-btn-secondary" onClick={grabHere} data-testid={`${testid}-gps`}>Lokasi Saya</button>
          {gpsErr ? <p className="iro-warn">{gpsErr}</p> : null}
        </div>
      )}
      {mode === "MAP" && (
        <RoutePlannerMap
          points={isValidCoordinate(lat, lng) ? [{ lat: Number(lat), lng: Number(lng), kind: "START" }] : []}
          pickMode
          onPick={(p) => applyManual(p.lat, p.lng, LOCATION_SOURCES.MAP_PIN)}
          height={240}
        />
      )}
      {mode !== "MAP" && isValidCoordinate(lat, lng) && (
        <RoutePlannerMap points={[{ lat: Number(lat), lng: Number(lng), kind: "START" }]} height={200} />
      )}
    </div>
  );
}

function StopWizard({ levels, initial, step, onClose, onSave }) {
  const [phase, setPhase] = useState(step || 1);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState([]);
  const [name, setName] = useState(initial?.display_name || "");
  const [address, setAddress] = useState(initial?.address || "");
  const [refKind, setRefKind] = useState(initial?.ref_kind || "ROUTE_ONLY");
  const [refId, setRefId] = useState(initial?.ref_id || "");
  const [lat, setLat] = useState(initial?.lat ?? "");
  const [lng, setLng] = useState(initial?.lng ?? "");
  const [source, setSource] = useState(initial?.location_source || LOCATION_SOURCES.MAP_PIN);
  const [locMode, setLocMode] = useState(pickModeFromSource(initial?.location_source));
  const [googleMeta, setGoogleMeta] = useState({
    google_place_id: initial?.google_place_id || "",
    place_name: initial?.place_name || "",
    formatted_address: initial?.formatted_address || "",
    google_maps_url: initial?.google_maps_url || "",
  });
  const [priorityId, setPriorityId] = useState(initial?.priority_id || levels[2]?.id || "p3");
  const [err, setErr] = useState("");

  const search = async () => {
    if (q.trim().length < 2) return;
    const r = await api.searchIndependentRouteOutlets(q.trim());
    setHits(r.candidates || []);
  };

  const pickGps = async () => {
    const result = await captureGeolocation();
    if (!result.ok) {
      setErr(result.message);
      return;
    }
    setLat(result.lat);
    setLng(result.lng);
    setSource(LOCATION_SOURCES.CURRENT_LOCATION);
    setGoogleMeta({ google_place_id: "", place_name: "", formatted_address: "", google_maps_url: "" });
  };

  const applyPoint = (nextLat, nextLng, nextSource, extra = {}) => {
    setLat(nextLat);
    setLng(nextLng);
    setSource(nextSource);
    if (nextSource === LOCATION_SOURCES.GOOGLE_PLACES) {
      setGoogleMeta({
        google_place_id: extra.google_place_id || "",
        place_name: extra.place_name || "",
        formatted_address: extra.formatted_address || "",
        google_maps_url: extra.google_maps_url || "",
      });
      if (!name.trim() && extra.place_name) setName(extra.place_name);
      if (!address.trim() && extra.formatted_address) setAddress(extra.formatted_address);
    } else {
      setGoogleMeta({ google_place_id: "", place_name: "", formatted_address: "", google_maps_url: "" });
    }
  };

  const submit = async () => {
    setErr("");
    if (!name.trim()) {
      setErr("Nama stop wajib.");
      return;
    }
    if (!isValidCoordinate(lat, lng)) {
      setErr("Titik lokasi wajib sebelum stop bisa dioptimasi.");
      return;
    }
    try {
      await onSave({
        display_name: name.trim(),
        address,
        ref_kind: refKind,
        ref_id: refId || null,
        lat: Number(lat),
        lng: Number(lng),
        location_source: source,
        google_place_id: source === LOCATION_SOURCES.GOOGLE_PLACES ? googleMeta.google_place_id : null,
        place_name: source === LOCATION_SOURCES.GOOGLE_PLACES ? googleMeta.place_name : null,
        formatted_address: source === LOCATION_SOURCES.GOOGLE_PLACES ? googleMeta.formatted_address : null,
        google_maps_url: source === LOCATION_SOURCES.GOOGLE_PLACES ? googleMeta.google_maps_url : null,
        priority_id: priorityId,
      });
    } catch (e) {
      setErr(e?.response?.data?.detail || "Gagal menyimpan stop.");
    }
  };

  return (
    <div className="modal" data-testid="iro-stop-modal">
      <div className="modal-inner iro-modal">
        <h3>{initial ? "Ubah titik stop" : "Tambah Stop"}</h3>
        <p className="subhead">Langkah {phase} dari 4 · Titik yang disimpan bersifat otoritatif untuk rute ini.</p>
        {err ? <div className="iro-alert is-error">{err}</div> : null}
        {phase === 1 && (
          <div>
            <label>Cari customer / outlet
              <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="iro-stop-search" />
            </label>
            <div className="iro-row-actions">
              <button type="button" className="iro-btn-secondary" onClick={search}>Cari</button>
              <button type="button" className="iro-btn-secondary" data-testid="iro-route-only" onClick={() => { setRefKind("ROUTE_ONLY"); setRefId(""); }}>
                Stop khusus rute
              </button>
            </div>
            <ul className="iro-hits">
              {hits.map((h) => (
                <li key={`${h.ref_kind}-${h.ref_id}`}>
                  <button type="button" onClick={() => {
                    setName(h.display_name);
                    setAddress(h.address || "");
                    setRefKind(h.ref_kind);
                    setRefId(h.ref_id);
                    if (isValidCoordinate(h.lat, h.lng)) {
                      setLat(h.lat);
                      setLng(h.lng);
                      setSource(LOCATION_SOURCES.MANUAL_COORDINATE);
                    }
                  }}>
                    <strong>{h.display_name}</strong>
                    <span>{h.source_label} · {h.address || "tanpa alamat"}</span>
                  </button>
                </li>
              ))}
            </ul>
            <label>Nama tampilan
              <input value={name} onChange={(e) => setName(e.target.value)} data-testid="iro-stop-name" />
            </label>
          </div>
        )}
        {phase === 2 && (
          <div>
            <p className="iro-coord-line" data-testid="iro-stop-coord">
              Routing Point: {isValidCoordinate(lat, lng) ? formatCoord(lat, lng) : "belum dipilih"}
            </p>
            <LocationModeTabs mode={locMode} onChange={setLocMode} testidPrefix="iro-stop" />
            {locMode === "GOOGLE" && (
              <GooglePlacePicker
                testidPrefix="iro-gmaps"
                onConfirm={(snap) => applyPoint(snap.lat, snap.lng, LOCATION_SOURCES.GOOGLE_PLACES, snap)}
              />
            )}
            {locMode === "MANUAL" && (
              <div className="iro-grid-2">
                <label>Latitude<input value={lat} onChange={(e) => applyPoint(e.target.value, lng, LOCATION_SOURCES.MANUAL_COORDINATE)} data-testid="iro-stop-lat" /></label>
                <label>Longitude<input value={lng} onChange={(e) => applyPoint(lat, e.target.value, LOCATION_SOURCES.MANUAL_COORDINATE)} data-testid="iro-stop-lng" /></label>
              </div>
            )}
            {locMode === "GPS" && (
              <div className="iro-row-actions">
                <button type="button" className="iro-btn-secondary" onClick={pickGps} data-testid="iro-stop-gps">Pakai GPS saat ini</button>
              </div>
            )}
            {locMode === "MAP" && (
              <RoutePlannerMap
                points={isValidCoordinate(lat, lng) ? [{ lat: Number(lat), lng: Number(lng), kind: "STOP", priority_id: priorityId, seq: "•" }] : []}
                pickMode
                onPick={(p) => applyPoint(p.lat, p.lng, LOCATION_SOURCES.MAP_PIN)}
                height={260}
              />
            )}
            {locMode !== "MAP" && locMode !== "GOOGLE" && isValidCoordinate(lat, lng) && (
              <RoutePlannerMap
                points={[{ lat: Number(lat), lng: Number(lng), kind: "STOP", priority_id: priorityId, seq: "•" }]}
                height={200}
              />
            )}
            {locMode !== "MANUAL" && (
              <div className="iro-grid-2" hidden>
                <label>Latitude<input value={lat} readOnly data-testid="iro-stop-lat" /></label>
                <label>Longitude<input value={lng} readOnly data-testid="iro-stop-lng" /></label>
              </div>
            )}
          </div>
        )}
        {phase === 3 && (
          <div className="iro-legend" data-testid="iro-stop-priority">
            {levels.map((p) => (
              <button key={p.id} type="button" className={`iro-chip is-btn ${priorityId === p.id ? "is-on" : ""}`} style={{ background: p.color }} onClick={() => setPriorityId(p.id)}>
                {String(p.id).toUpperCase()} · {p.label}
              </button>
            ))}
          </div>
        )}
        {phase === 4 && (
          <div>
            <p><b>{name || "Tanpa nama"}</b></p>
            <p>Routing Point: {formatCoord(lat, lng)}</p>
            <p>Source: {source === LOCATION_SOURCES.GOOGLE_PLACES ? "Google Maps" : source}</p>
            <p>Prioritas: {String(priorityId).toUpperCase()}</p>
            <p>Jenis: {refKind === "ROUTE_ONLY" ? "Stop khusus rute (tidak membuat master)" : "Referensi master, koordinat tetap independen"}</p>
          </div>
        )}
        <div className="iro-row-actions">
          <button type="button" className="iro-btn-secondary" onClick={onClose}>Batal</button>
          {phase > 1 && <button type="button" className="iro-btn-secondary" onClick={() => setPhase(phase - 1)}>Kembali</button>}
          {phase < 4 && <button type="button" className="iro-btn-primary" data-testid="iro-stop-next" onClick={() => setPhase(phase + 1)}>Lanjut</button>}
          {phase === 4 && <button type="button" className="iro-btn-primary" data-testid="iro-stop-save" onClick={submit}><Save size={14} /> Simpan stop</button>}
        </div>
      </div>
    </div>
  );
}
