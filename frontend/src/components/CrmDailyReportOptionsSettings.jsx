import { useCallback, useEffect, useId, useState } from "react";
import api from "../lib/api";

const CATEGORIES = [
  { key: "no_order_reasons", label: "Alasan Tidak Order" },
  { key: "complaint_categories", label: "Kategori Komplain" },
  { key: "issue_categories", label: "Kategori Kendala" },
];

const OTHER_ID = "__other__";

function errText(e) {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  return d?.message || e?.message || "Gagal";
}

function ensureOther(rows) {
  const list = Array.isArray(rows) ? rows.map((r) => ({ ...r })) : [];
  if (!list.some((r) => r.id === OTHER_ID || r.is_other)) {
    list.push({ id: OTHER_ID, label: "Lainnya", active: true, sort_order: 9999, is_other: true });
  }
  return list.map((r) => (
    r.id === OTHER_ID || r.is_other
      ? { ...r, id: OTHER_ID, is_other: true }
      : r
  ));
}

export default function CrmDailyReportOptionsSettings({ notify, canEdit }) {
  const uid = useId();
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.crmDailyReportOptionsSettings();
      const s = res.settings || {};
      setSettings({
        ...s,
        no_order_reasons: ensureOther(s.no_order_reasons),
        complaint_categories: ensureOther(s.complaint_categories),
        issue_categories: ensureOther(s.issue_categories),
        doc_version: s.doc_version || 1,
      });
    } catch (e) {
      setErr(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const setOption = (catKey, index, patch) => {
    setSettings((prev) => {
      if (!prev) return prev;
      const rows = ensureOther(prev[catKey]).map((row, i) => (i === index ? { ...row, ...patch } : row));
      return { ...prev, [catKey]: rows };
    });
  };

  const addOption = (catKey) => {
    setSettings((prev) => {
      if (!prev) return prev;
      const rows = ensureOther(prev[catKey]);
      const next = [
        ...rows.filter((r) => r.id !== OTHER_ID),
        { id: `opt-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, label: "", active: true, sort_order: rows.length * 10, is_other: false },
        ...rows.filter((r) => r.id === OTHER_ID),
      ];
      return { ...prev, [catKey]: next };
    });
  };

  const deactivateOption = (catKey, index) => {
    setOption(catKey, index, { active: false });
  };

  const save = async () => {
    if (!settings || !canEdit) return;
    setBusy(true);
    setErr("");
    try {
      const payload = {
        doc_version: settings.doc_version,
        no_order_reasons: ensureOther(settings.no_order_reasons),
        complaint_categories: ensureOther(settings.complaint_categories),
        issue_categories: ensureOther(settings.issue_categories),
      };
      for (const cat of CATEGORIES) {
        for (const row of payload[cat.key]) {
          if (!String(row.label || "").trim()) {
            setErr(`${cat.label}: label tidak boleh kosong`);
            setBusy(false);
            return;
          }
        }
      }
      const res = await api.updateCrmDailyReportOptionsSettings(payload);
      const s = res.settings || {};
      setSettings({
        ...s,
        no_order_reasons: ensureOther(s.no_order_reasons),
        complaint_categories: ensureOther(s.complaint_categories),
        issue_categories: ensureOther(s.issue_categories),
      });
      notify?.(res.message || "Pengaturan dropdown CRM disimpan");
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <p className="muted" data-testid="crm-options-loading">Memuat…</p>;
  }

  return (
    <div data-testid="crm-daily-report-options-tab" style={{ display: "grid", gap: 14, maxHeight: "calc(100vh - 180px)", overflow: "auto" }}>
      <p className="muted small" style={{ margin: 0 }}>
        Dropdown khusus Laporan Harian CRM. Opsi yang sudah ada tidak dihapus permanen — nonaktifkan saja.
        Identitas Lainnya tetap <code>{OTHER_ID}</code>.
      </p>
      {err ? <p style={{ color: "#8f1f1f", margin: 0 }} data-testid="crm-options-error">{err}</p> : null}

      {CATEGORIES.map((cat) => (
        <section key={cat.key} data-testid={`crm-options-${cat.key}`} style={{ display: "grid", gap: 8 }}>
          <h4 style={{ margin: 0 }}>{cat.label}</h4>
          <table className="ship-table">
            <thead>
              <tr>
                <th>Label</th>
                <th>Urutan</th>
                <th>Status</th>
                {canEdit ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {ensureOther(settings?.[cat.key]).map((row, i) => (
                <tr key={`${cat.key}-${row.id}`}>
                  <td>
                    <input
                      id={`${uid}-${cat.key}-${i}`}
                      value={row.label || ""}
                      onChange={(e) => setOption(cat.key, i, { label: e.target.value })}
                      disabled={!canEdit || row.id === OTHER_ID}
                      data-testid={`crm-opt-label-${cat.key}-${i}`}
                      autoComplete="off"
                    />
                  </td>
                  <td style={{ width: 80 }}>
                    <input
                      type="number"
                      value={row.sort_order ?? i}
                      onChange={(e) => setOption(cat.key, i, { sort_order: Number(e.target.value) || 0 })}
                      disabled={!canEdit || row.id === OTHER_ID}
                    />
                  </td>
                  <td>{row.active ? "Aktif" : "Nonaktif"}</td>
                  {canEdit ? (
                    <td>
                      {row.id !== OTHER_ID && row.active ? (
                        <button type="button" className="ghost sm" onClick={() => deactivateOption(cat.key, i)}>
                          Nonaktifkan
                        </button>
                      ) : null}
                      {row.id !== OTHER_ID && !row.active ? (
                        <button type="button" className="ghost sm" onClick={() => setOption(cat.key, i, { active: true })}>
                          Aktifkan
                        </button>
                      ) : null}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
          {canEdit ? (
            <button type="button" className="ghost sm" onClick={() => addOption(cat.key)} data-testid={`crm-opt-add-${cat.key}`}>
              Tambah opsi
            </button>
          ) : null}
        </section>
      ))}

      {canEdit ? (
        <div>
          <button type="button" className="primary sm" onClick={save} disabled={busy} data-testid="crm-options-save">
            {busy ? "Menyimpan…" : "Simpan pengaturan"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
