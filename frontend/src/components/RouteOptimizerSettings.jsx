import { useEffect, useState } from "react";
import { MapPin, Save } from "lucide-react";
import api from "../lib/api";
import { DEFAULT_PRIORITIES, END_MODES, START_MODES, STRATEGIES, formatCoord, isValidCoordinate } from "../lib/independentRoute";
import RoutePlannerMap from "./RoutePlannerMap";

export default function RouteOptimizerSettings({ notify, canEdit }) {
  const [cfg, setCfg] = useState(null);
  const [error, setError] = useState("");
  const [lat, setLat] = useState("");
  const [lng, setLng] = useState("");

  const load = () => {
    api.independentRouteSettings()
      .then((r) => {
        const s = r.settings || {};
        setCfg(s);
        setLat(s.warehouse?.lat ?? "");
        setLng(s.warehouse?.lng ?? "");
      })
      .catch((e) => setError(e?.response?.data?.detail || "Gagal memuat pengaturan."));
  };

  useEffect(() => { load(); }, []);

  if (!cfg) return <div className="loading">Memuat pengaturan Optimasi Rute…</div>;

  const save = async () => {
    setError("");
    try {
      const warehouse = { ...cfg.warehouse, lat: lat === "" ? null : Number(lat), lng: lng === "" ? null : Number(lng), location_source: "MANUAL_COORDINATE" };
      const r = await api.updateIndependentRouteSettings({ ...cfg, warehouse });
      setCfg(r.settings);
      notify?.("Pengaturan Optimasi Rute disimpan.");
    } catch (e) {
      setError(e?.response?.data?.detail || "Gagal menyimpan.");
    }
  };

  const pick = (p) => {
    setLat(p.lat);
    setLng(p.lng);
  };

  const levels = cfg.priority_levels?.length ? cfg.priority_levels : DEFAULT_PRIORITIES;
  const warehouseOk = isValidCoordinate(lat, lng);

  return (
    <div className="iro-settings" data-testid="iro-settings">
      {error ? <div className="iro-alert is-error">{error}</div> : null}
      <section className="iro-card">
        <h3><MapPin size={16} /> Lokasi Gudang</h3>
        <p className="subhead">Wajib dikonfigurasi sebelum Start/End Gudang bisa dipakai. Jangan mengisi koordinat fiktif.</p>
        {!warehouseOk ? <div className="iro-alert is-warn" data-testid="iro-warehouse-missing">Lokasi Gudang belum dikonfigurasi</div> : (
          <p className="iro-coord-line" data-testid="iro-warehouse-coord">{formatCoord(lat, lng)}</p>
        )}
        <div className="iro-grid-2">
          <label>Latitude<input value={lat} disabled={!canEdit} onChange={(e) => setLat(e.target.value)} data-testid="iro-wh-lat" /></label>
          <label>Longitude<input value={lng} disabled={!canEdit} onChange={(e) => setLng(e.target.value)} data-testid="iro-wh-lng" /></label>
        </div>
        <RoutePlannerMap
          points={warehouseOk ? [{ lat: Number(lat), lng: Number(lng), kind: "WAREHOUSE", label: "Gudang" }] : []}
          pickMode={canEdit}
          onPick={pick}
          height={280}
        />
      </section>
      <section className="iro-card">
        <h3>Default rencana rute</h3>
        <div className="iro-grid-3">
          <label>Start
            <select value={cfg.default_start_mode} disabled={!canEdit} onChange={(e) => setCfg({ ...cfg, default_start_mode: e.target.value })}>
              {START_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          <label>End
            <select value={cfg.default_end_mode} disabled={!canEdit} onChange={(e) => setCfg({ ...cfg, default_end_mode: e.target.value })}>
              {END_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          <label>Strategi
            <select value={cfg.default_strategy} disabled={!canEdit} onChange={(e) => setCfg({ ...cfg, default_strategy: e.target.value })}>
              {STRATEGIES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
        </div>
      </section>
      <section className="iro-card">
        <h3>Tingkat prioritas</h3>
        <p className="subhead">Algoritma memakai rank, bukan label. Rank tidak boleh duplikat.</p>
        <div className="iro-priority-editor">
          {levels.map((p, idx) => (
            <div key={p.id} className="iro-priority-row">
              <span className="iro-chip" style={{ background: p.color }}>{String(p.id).toUpperCase()}</span>
              <input value={p.label} disabled={!canEdit} onChange={(e) => {
                const next = levels.map((x, i) => i === idx ? { ...x, label: e.target.value } : x);
                setCfg({ ...cfg, priority_levels: next });
              }} />
              <input type="number" value={p.rank} disabled={!canEdit} onChange={(e) => {
                const next = levels.map((x, i) => i === idx ? { ...x, rank: Number(e.target.value) } : x);
                setCfg({ ...cfg, priority_levels: next });
              }} />
              <input type="color" value={p.color} disabled={!canEdit} onChange={(e) => {
                const next = levels.map((x, i) => i === idx ? { ...x, color: e.target.value } : x);
                setCfg({ ...cfg, priority_levels: next });
              }} />
              <label className="iro-check">
                <input type="checkbox" checked={!!p.enabled} disabled={!canEdit} onChange={(e) => {
                  const next = levels.map((x, i) => i === idx ? { ...x, enabled: e.target.checked } : x);
                  setCfg({ ...cfg, priority_levels: next });
                }} /> Aktif
              </label>
            </div>
          ))}
        </div>
      </section>
      {canEdit && (
        <button type="button" className="iro-btn-primary" onClick={save} data-testid="iro-settings-save">
          <Save size={16} /> Simpan pengaturan
        </button>
      )}
    </div>
  );
}
