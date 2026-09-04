import { useEffect, useState } from "react";
import api from "../lib/api";
import { validateSettingsDraft } from "../lib/salesActivity";
import { SaField, SaSection, SaTabHead } from "./SalesActivityUi";

export default function SalesActivitySettings({ notify, canEdit }) {
  const [settings, setSettings] = useState(null);
  const [users, setUsers] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.salesActivitySettings().then((r) => setSettings(r.settings)).catch((e) => notify?.(e.message));
    if (canEdit) api.salesActivityUsers().then((r) => setUsers(r.users || [])).catch(() => {});
    // notify is an unstable parent callback
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canEdit]);

  if (!settings) return <div className="muted">Memuat pengaturan...</div>;

  const rule = settings.default_rule;
  const setRule = (k, v) => setSettings({ ...settings, default_rule: { ...rule, [k]: v } });
  const setRe = (k, v) => setSettings({ ...settings, reassignment: { ...settings.reassignment, [k]: v } });
  const setDorm = (k, v) => setSettings({ ...settings, dormancy: { ...settings.dormancy, [k]: v } });

  const save = async () => {
    const errs = validateSettingsDraft(settings);
    if (errs.length) { setError(errs.join(". ")); return; }
    try {
      const res = await api.updateSalesActivitySettings(settings);
      setSettings(res.settings);
      setError("");
      notify?.("Pengaturan Sales Activity disimpan");
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  const toggleSeq = (id) => {
    const seq = settings.reassignment.sales_sequence || [];
    setRe("sales_sequence", seq.includes(id) ? seq.filter((x) => x !== id) : [...seq, id]);
  };

  const toggleProspectPic = (id) => {
    const ids = settings.prospect_pic_user_ids || [];
    setSettings({
      ...settings,
      prospect_pic_user_ids: ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    });
  };

  const setType = (idx, k, v) => {
    const types = settings.activity_types.map((t, i) => {
      if (i !== idx) return t;
      const next = { ...t, [k]: v };
      if (t.id === "visit" && (k === "visit_photo_required" || k === "evidence_required")) {
        next.visit_photo_required = v;
        next.evidence_required = v;
      }
      return next;
    });
    setSettings({ ...settings, activity_types: types });
  };

  return (
    <div className="settings-card sa-settings" data-testid="sa-settings">
      <SaTabHead
        title="Sales Activity & Ownership"
        description="Destinasi customer dormant tetap Pool. Import tetap PAUSED_MIGRATION sampai aktivasi eksplisit."
      >
        {canEdit && (
          <button className="primary" type="button" onClick={save} data-testid="sa-settings-save">
            Simpan Pengaturan
          </button>
        )}
      </SaTabHead>
      {!canEdit && <p className="muted">Anda hanya dapat melihat pengaturan ini.</p>}
      {error && <p className="error" data-testid="sa-settings-error">{error}</p>}
      <fieldset className="sa-settings-stack" disabled={!canEdit}>
        <SaSection title="Ownership">
          <label className="sa-check">
            <input type="checkbox" checked={!!rule.enabled} onChange={(e) => setRule("enabled", e.target.checked)} />
            Enabled
          </label>
          <SaField label="Durasi ownership (hari)">
            <input className="sa-control" type="number" min="1" value={rule.ownership_duration_days} onChange={(e) => setRule("ownership_duration_days", Number(e.target.value))} />
          </SaField>
          <SaField label="Timer mode">
            <select className="sa-control" value={rule.timer_mode} onChange={(e) => setRule("timer_mode", e.target.value)}>
              <option value="EARLIEST_OF_BOTH">Earliest of both</option>
              <option value="FIXED_EXPIRY">Fixed expiry</option>
              <option value="INACTIVITY_EXPIRY">Inactivity expiry</option>
            </select>
          </SaField>
          <label className="sa-check sa-field-wide">
            <input type="checkbox" checked={!!rule.valid_activity_resets_inactivity} onChange={(e) => setRule("valid_activity_resets_inactivity", e.target.checked)} />
            Valid activity mereset inactivity
          </label>
        </SaSection>

        <SaSection title="Warning / Expiry">
          <SaField label="Warning sebelum expire">
            <input className="sa-control" type="number" min="0" value={rule.warning_before_expiration_days} onChange={(e) => setRule("warning_before_expiration_days", Number(e.target.value))} />
          </SaField>
          <SaField label="Inactivity warning">
            <input className="sa-control" type="number" min="0" value={rule.inactivity_warning_days} onChange={(e) => setRule("inactivity_warning_days", Number(e.target.value))} />
          </SaField>
          <SaField label="Inactivity expiration">
            <input className="sa-control" type="number" min="1" value={rule.inactivity_expiration_days} onChange={(e) => setRule("inactivity_expiration_days", Number(e.target.value))} />
          </SaField>
        </SaSection>

        <SaSection title="Reassignment">
          <p className="sa-field-hint sa-field-wide">Mode: HYBRID · Fallback: Pool</p>
          <div className="sa-type-grid sa-field-wide" data-testid="sa-settings-sequence">
            {(users || []).map((u) => (
              <label key={u.id} className="sa-check">
                <input type="checkbox" checked={(settings.reassignment.sales_sequence || []).includes(u.id)} onChange={() => toggleSeq(u.id)} />
                {u.name} ({u.email})
              </label>
            ))}
          </div>
        </SaSection>

        <SaSection title="PIC Sales Prospek">
          <p className="sa-field-hint sa-field-wide">
            User aktif yang boleh dipilih sebagai Owner / PIC Sales pada Outlet Prospek.
          </p>
          <div className="sa-type-grid sa-field-wide" data-testid="sa-settings-prospect-pic">
            {(users || []).filter((u) => String(u.status || "active") === "active").map((u) => (
              <label key={u.id} className="sa-check">
                <input
                  type="checkbox"
                  checked={(settings.prospect_pic_user_ids || []).includes(u.id)}
                  onChange={() => toggleProspectPic(u.id)}
                  data-testid={`sa-settings-prospect-pic-${u.id}`}
                />
                {u.name}{u.role ? ` · ${u.role}` : ""}{u.email ? ` (${u.email})` : ""}
              </label>
            ))}
            {(users || []).filter((u) => String(u.status || "active") === "active").length === 0 && (
              <p className="muted">Tidak ada user aktif.</p>
            )}
          </div>
        </SaSection>

        <SaSection title="Activity Types">
          {(settings.activity_types || []).map((t, i) => (
            <div key={t.id} className="sa-section sa-field-wide">
              <strong>{t.label}</strong>
              <div className="sa-type-grid" style={{ marginTop: 10 }}>
                <label className="sa-check">
                  <input type="checkbox" checked={!!t.active} onChange={(e) => setType(i, "active", e.target.checked)} />
                  Aktif
                </label>
                <label className="sa-check">
                  <input type="checkbox" checked={!!t.counts_as_valid} onChange={(e) => setType(i, "counts_as_valid", e.target.checked)} />
                  Valid activity
                </label>
                <label className="sa-check">
                  <input type="checkbox" checked={!!t.resets_inactivity} onChange={(e) => setType(i, "resets_inactivity", e.target.checked)} />
                  Reset inactivity
                </label>
                <label className="sa-check">
                  <input type="checkbox" checked={!!(t.id === "visit" ? t.visit_photo_required || t.evidence_required : t.evidence_required)} onChange={(e) => setType(i, t.id === "visit" ? "visit_photo_required" : "evidence_required", e.target.checked)} />
                  {t.id === "visit" ? "Foto Visit wajib" : "Evidence wajib"}
                </label>
              </div>
            </div>
          ))}
        </SaSection>

        <SaSection title="Dormancy">
          <SaField label="Tanpa Pesanan Penjualan valid (hari)">
            <input className="sa-control" type="number" min="1" value={settings.dormancy.threshold_days} onChange={(e) => setDorm("threshold_days", Number(e.target.value))} />
          </SaField>
          <SaField label="Warning sebelum dormant">
            <input className="sa-control" type="number" min="0" value={settings.dormancy.warning_days} onChange={(e) => setDorm("warning_days", Number(e.target.value))} />
          </SaField>
          <p className="sa-readonly-value sa-field-wide" data-testid="sa-dormant-destination">Destinasi dormant: Pool</p>
        </SaSection>
      </fieldset>
    </div>
  );
}
