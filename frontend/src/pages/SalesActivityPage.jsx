import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  AUTOMATION_LABELS,
  GEO_UNAVAILABLE_MESSAGE,
  KIND_LABELS,
  automationPausedMigration,
  canActivateMigration,
  canCreateActivity,
  canCreateOutlet,
  canImportSalesActivity,
  canManagePool,
  compactVisitIndicator,
  isVisitActivityType,
  visitPhotoRequiredForType,
} from "../lib/salesActivity";
import { SaField, SaFormFooter, SaSection, SaTabHead, fmtSaDate } from "../components/SalesActivityUi";
import { VisitPhotoComposer } from "../components/SalesActivityVisitPhotos";

function errText(e) {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  return d?.message || e.message || "Gagal";
}

export default function SalesActivityPage() {
  const { hasPerm } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "outlets";
  const setTab = (next) => setParams({ tab: next });
  const [toast, setToast] = useState("");
  const notify = (m) => { setToast(m); setTimeout(() => setToast(""), 4000); };

  return (
    <div className="sa-page" data-testid="sales-activity-page">
      <div className="sa-page-chrome">
        <div className="sa-page-head page-heading">
          <div>
            <p className="eyebrow">Penjualan</p>
            <h1>Aktivitas Sales &amp; Kepemilikan Outlet</h1>
            <p className="muted">Outlet, aktivitas, pool, dan migrasi Excel. Automation diputuskan backend.</p>
          </div>
        </div>
        <div className="prediksi-tabs sa-tabs" role="tablist">
          <button className={tab === "outlets" ? "is-active" : ""} onClick={() => setTab("outlets")} data-testid="sa-tab-outlets">Outlet / Prospek</button>
          <button className={tab === "activity" ? "is-active" : ""} onClick={() => setTab("activity")} data-testid="sa-tab-activity">Activity</button>
          {canManagePool(hasPerm) && <button className={tab === "pool" ? "is-active" : ""} onClick={() => setTab("pool")} data-testid="sa-tab-pool">Pool</button>}
          {canImportSalesActivity(hasPerm) && <button className={tab === "import" ? "is-active" : ""} onClick={() => setTab("import")} data-testid="sa-tab-import">Import</button>}
        </div>
      </div>
      {tab === "outlets" && <OutletTab notify={notify} />}
      {tab === "activity" && <ActivityTab notify={notify} />}
      {tab === "pool" && canManagePool(hasPerm) && <PoolTab notify={notify} />}
      {tab === "import" && canImportSalesActivity(hasPerm) && <ImportTab notify={notify} />}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function defaultProspectOwnerId(pics, user) {
  if (!pics?.length) return "";
  if (pics.length === 1) return pics[0].id;
  const self = user?.id || user?.user_id;
  if (self && pics.some((p) => p.id === self)) return self;
  return "";
}

function OutletTab({ notify }) {
  const { hasPerm, user } = useAuth();
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [picUsers, setPicUsers] = useState([]);
  const [form, setForm] = useState({ name: "", address: "", whatsapp: "", category: "", lead_source: "", owner_id: "" });

  const load = () => {
    api.listSalesActivityOutlets({ q }).then((r) => setRows(r.items || [])).catch((e) => notify(errText(e)));
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  useEffect(() => {
    if (!creating || !canCreateOutlet(hasPerm)) return;
    api.salesActivityProspectPicUsers()
      .then((r) => {
        const pics = r.users || [];
        setPicUsers(pics);
        setForm((prev) => ({
          ...prev,
          owner_id: prev.owner_id && pics.some((p) => p.id === prev.owner_id)
            ? prev.owner_id
            : defaultProspectOwnerId(pics, user),
        }));
      })
      .catch((e) => notify(errText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [creating]);

  const resetForm = () => setForm({
    name: "",
    address: "",
    whatsapp: "",
    category: "",
    lead_source: "",
    owner_id: defaultProspectOwnerId(picUsers, user),
  });

  const picEmpty = creating && picUsers.length === 0;
  const canSubmit = Boolean(form.owner_id) && !picEmpty;

  const submit = async (e) => {
    e.preventDefault();
    if (!canSubmit) {
      notify("Belum ada PIC Sales Prospek yang diatur. Hubungi administrator.");
      return;
    }
    try {
      await api.createSalesActivityOutlet({
        name: form.name,
        address: form.address,
        whatsapp: form.whatsapp,
        category: form.category,
        lead_source: form.lead_source,
        owner_id: form.owner_id,
      });
      resetForm();
      setCreating(false);
      notify("Outlet dibuat. Automation aktif.");
      load();
    } catch (err) { notify(errText(err)); }
  };

  return (
    <div>
      <SaTabHead title="Outlet / Prospek" description="Daftar outlet yang masuk cakupan Anda.">
        {canCreateOutlet(hasPerm) && (
          <button
            type="button"
            className={creating ? "ghost" : "primary"}
            onClick={() => setCreating((v) => !v)}
            data-testid="sa-outlet-create-toggle"
          >
            {creating ? "Tutup" : "Tambah Outlet"}
          </button>
        )}
      </SaTabHead>

      <div className="sa-filters">
        <SaField label="Cari">
          <input className="sa-control" value={q} onChange={(e) => setQ(e.target.value)} data-testid="sa-outlet-search" placeholder="Nama outlet" />
        </SaField>
      </div>

      {creating && canCreateOutlet(hasPerm) && (
        <form className="sa-form" onSubmit={submit} data-testid="sa-outlet-create-form">
          <SaSection title="Informasi Outlet">
            <SaField label="Nama Outlet" required>
              <input className="sa-control" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </SaField>
            <SaField label="Kategori">
              <input className="sa-control" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
            </SaField>
            <SaField label="Alamat" wide>
              <input className="sa-control" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
            </SaField>
          </SaSection>
          <SaSection title="Kontak">
            <SaField label="WhatsApp">
              <input className="sa-control" value={form.whatsapp} onChange={(e) => setForm({ ...form, whatsapp: e.target.value })} />
            </SaField>
          </SaSection>
          <SaSection title="Sales">
            <SaField label="Sumber Lead">
              <input className="sa-control" value={form.lead_source} onChange={(e) => setForm({ ...form, lead_source: e.target.value })} />
            </SaField>
            <SaField label="Owner / PIC Sales" required>
              <select
                className="sa-control"
                required
                value={form.owner_id}
                onChange={(e) => setForm({ ...form, owner_id: e.target.value })}
                data-testid="sa-outlet-owner-select"
                disabled={picEmpty}
              >
                <option value="">{picEmpty ? "Tidak tersedia" : "Pilih PIC Sales"}</option>
                {picUsers.map((u) => (
                  <option key={u.id} value={u.id}>{u.name}{u.role ? ` · ${u.role}` : ""}</option>
                ))}
              </select>
            </SaField>
            {picEmpty && (
              <p className="error sa-field-wide" data-testid="sa-outlet-pic-empty">
                Belum ada PIC Sales Prospek yang diatur. Hubungi administrator.
              </p>
            )}
          </SaSection>
          <SaFormFooter
            onCancel={() => { setCreating(false); resetForm(); }}
            submitLabel="Simpan"
            submitDisabled={!canSubmit}
          />
        </form>
      )}

      <div className="sa-table-wrap table-wrap">
        <table className="ship-table" data-testid="sa-outlet-table">
          <thead>
            <tr>
              <th>Outlet</th><th>Owner</th><th>Status</th><th>Automation</th>
              <th>Aktivitas terakhir</th><th>Follow up</th><th>Mulai</th><th>Eligible</th><th>Order terakhir</th><th>Pool</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={10}><p className="sa-empty">Belum ada outlet.</p></td></tr>
            )}
            {rows.map((row) => {
              const own = row.ownership || {};
              return (
                <tr key={row.id}>
                  <td><Link className="linkish" to={`/penjualan/aktivitas-sales/outlet/${row.id}`}>{row.name}</Link></td>
                  <td>{own.current_owner_name || "-"}</td>
                  <td>{KIND_LABELS[own.customer_kind] || own.customer_kind || "-"}</td>
                  <td>
                    <span className="pill" data-testid={`sa-auto-${row.id}`}>{AUTOMATION_LABELS[own.automation_status] || own.automation_status}</span>
                    {automationPausedMigration(own.automation_status) && <div className="muted" data-testid="sa-import-paused">Import tidak menjalankan automation.</div>}
                  </td>
                  <td>{fmtSaDate(own.last_activity_at)}</td>
                  <td>{fmtSaDate(own.next_follow_up_at)}</td>
                  <td>{fmtSaDate(own.ownership_started_at)}</td>
                  <td>{fmtSaDate(own.eligible_at)}</td>
                  <td>{fmtSaDate(own.last_valid_sales_order_at)}</td>
                  <td>{own.in_pool ? "Ya" : "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ActivityTab({ notify }) {
  const { hasPerm } = useAuth();
  const [outlets, setOutlets] = useState([]);
  const [rows, setRows] = useState([]);
  const [settings, setSettings] = useState(null);
  const [creating, setCreating] = useState(false);
  const [photos, setPhotos] = useState([]);
  const [form, setForm] = useState({ outlet_id: "", activity_type: "visit", result: "", next_action: "", next_follow_up_at: "", notes: "" });

  useEffect(() => {
    api.listSalesActivityOutlets({}).then((r) => setOutlets(r.items || [])).catch((e) => notify(errText(e)));
    api.listSalesActivities({}).then((r) => setRows(r.items || [])).catch((e) => notify(errText(e)));
    api.salesActivitySettings().then((r) => setSettings(r.settings)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const resetForm = () => {
    setForm({ outlet_id: "", activity_type: "visit", result: "", next_action: "", next_follow_up_at: "", notes: "" });
    setPhotos([]);
  };

  const visitTypeCfg = (settings?.activity_types || []).find((t) => t.id === "visit") || { id: "visit" };
  const photoRequired = isVisitActivityType(form.activity_type) && visitPhotoRequiredForType(visitTypeCfg);

  const submit = async (e) => {
    e.preventDefault();
    if (isVisitActivityType(form.activity_type)) {
      if (photoRequired && photos.length === 0) {
        notify("Foto Visit wajib untuk jenis aktivitas ini");
        return;
      }
      if (photoRequired && !photos.some((p) => p.location_status === "OK")) {
        notify(GEO_UNAVAILABLE_MESSAGE);
        return;
      }
    }
    const payload = {
      outlet_id: form.outlet_id,
      activity_type: form.activity_type,
      result: form.result,
      next_action: form.next_action,
      next_follow_up_at: form.next_follow_up_at,
      notes: form.notes,
    };
    if (isVisitActivityType(form.activity_type)) {
      payload.visit_photo_ids = photos.map((p) => p.id).filter(Boolean);
    }
    try {
      await api.createSalesActivity(payload);
      notify("Aktivitas tersimpan");
      resetForm();
      setCreating(false);
      const r = await api.listSalesActivities({});
      setRows(r.items || []);
    } catch (err) { notify(errText(err)); }
  };

  return (
    <div>
      <SaTabHead title="Activity" description="Catat aktivitas sales pada outlet yang menjadi tanggung jawab Anda.">
        {canCreateActivity(hasPerm) && (
          <button
            type="button"
            className={creating ? "ghost" : "primary"}
            onClick={() => setCreating((v) => !v)}
          >
            {creating ? "Tutup" : "Tambah Activity"}
          </button>
        )}
      </SaTabHead>

      {creating && canCreateActivity(hasPerm) && (
        <form className="sa-form" onSubmit={submit} data-testid="sa-activity-form">
          <SaSection title="Aktivitas">
            <SaField label="Outlet" required>
              <select className="sa-control" required value={form.outlet_id} onChange={(e) => setForm({ ...form, outlet_id: e.target.value })} data-testid="sa-activity-outlet">
                <option value="">Pilih</option>
                {outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </SaField>
            <SaField label="Jenis Aktivitas" required>
              <select className="sa-control" required value={form.activity_type} onChange={(e) => setForm({ ...form, activity_type: e.target.value })} data-testid="sa-activity-type">
                <option value="visit">Visit</option>
                <option value="chat">Chat</option>
                <option value="closing">Closing</option>
              </select>
            </SaField>
            <SaField label="Hasil" wide>
              <input className="sa-control" value={form.result} onChange={(e) => setForm({ ...form, result: e.target.value })} />
            </SaField>
          </SaSection>
          <SaSection title="Tindak Lanjut">
            <SaField label="Next Action">
              <input className="sa-control" value={form.next_action} onChange={(e) => setForm({ ...form, next_action: e.target.value })} />
            </SaField>
            <SaField label="Next Follow Up">
              <input className="sa-control" type="datetime-local" value={form.next_follow_up_at} onChange={(e) => setForm({ ...form, next_follow_up_at: e.target.value })} />
            </SaField>
          </SaSection>
          <SaSection title="Catatan">
            <SaField label="Notes" wide>
              <textarea className="sa-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </SaField>
          </SaSection>
          {isVisitActivityType(form.activity_type) && (
            <SaSection title="Foto & Lokasi Visit">
              <div className="sa-field-wide">
                <VisitPhotoComposer
                  outletId={form.outlet_id}
                  required={photoRequired}
                  photos={photos}
                  setPhotos={setPhotos}
                  notify={notify}
                />
              </div>
            </SaSection>
          )}
          <SaFormFooter
            onCancel={() => { setCreating(false); resetForm(); }}
            submitLabel="Simpan"
            submitTestId="sa-activity-submit"
          />
        </form>
      )}

      <div className="sa-table-wrap table-wrap">
        <table className="ship-table">
          <thead><tr><th>Waktu</th><th>Sales</th><th>Jenis</th><th>Hasil</th><th>Notes</th><th>Visit</th></tr></thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={6}><p className="sa-empty">Belum ada aktivitas.</p></td></tr>
            )}
            {rows.map((r) => {
              const visit = compactVisitIndicator(r);
              return (
                <tr key={r.id}>
                  <td>{fmtSaDate(r.activity_at)}</td>
                  <td>{r.sales_name}</td>
                  <td>{r.activity_type}</td>
                  <td>{r.result}</td>
                  <td>{r.notes}</td>
                  <td data-testid={`sa-activity-visit-${r.id}`}>{isVisitActivityType(r.activity_type) ? `${visit.photoLabel} · ${visit.locationLabel}` : "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PoolTab({ notify }) {
  const { hasPerm } = useAuth();
  const [rows, setRows] = useState([]);
  const [users, setUsers] = useState([]);
  const [pick, setPick] = useState({});

  const load = () => api.listSalesActivityPool({}).then((r) => setRows(r.items || [])).catch((e) => notify(errText(e)));
  useEffect(() => {
    load();
    if (canManagePool(hasPerm)) api.salesActivityUsers().then((r) => setUsers(r.users || [])).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const assign = async (outletId) => {
    const sel = pick[outletId] || {};
    if (!sel.owner_id || !sel.reason) { notify("Sales dan alasan wajib"); return; }
    try {
      await api.salesActivityAssignFromPool(outletId, { owner_id: sel.owner_id, reason: sel.reason });
      notify("Assign dari Pool membuat siklus ownership baru");
      load();
    } catch (e) { notify(errText(e)); }
  };

  const userName = (id) => (users.find((u) => u.id === id) || {}).name || id || "-";

  return (
    <div>
      <SaTabHead title="Pool" description="Outlet yang menunggu assignment supervisor. Alasan wajib saat assign." />
      <div className="sa-table-wrap table-wrap" data-testid="sa-pool-table">
        <table className="ship-table">
          <thead>
            <tr>
              <th>Outlet</th>
              <th>Owner sebelumnya</th>
              <th>Alasan Pool</th>
              <th>Aktivitas terakhir</th>
              <th>Order terakhir</th>
              <th>Saran sales</th>
              <th>Status</th>
              <th>Hari di Pool</th>
              <th>Assign</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={9}><p className="sa-empty">Pool kosong.</p></td></tr>
            )}
            {rows.map((row) => (
              <tr key={row.id}>
                <td><Link className="linkish" to={`/penjualan/aktivitas-sales/outlet/${row.outlet_id}`}>{(row.outlet || {}).name || row.outlet_id}</Link></td>
                <td>{row.previous_owner_name || "-"}</td>
                <td>{row.reason}</td>
                <td>{fmtSaDate(row.last_activity_at)}</td>
                <td>{fmtSaDate(row.last_valid_sales_order_at)}</td>
                <td>{userName(row.suggested_next_owner_id)}</td>
                <td>{KIND_LABELS[row.customer_kind] || row.customer_kind || "Pool"}</td>
                <td>{row.days_in_pool}</td>
                <td className="sa-actions-col">
                  {canManagePool(hasPerm) && (
                    <div className="sa-pool-assign" data-testid={`sa-pool-assign-${row.outlet_id}`}>
                      <SaField label="Sales" required>
                        <select
                          className="sa-control"
                          value={(pick[row.outlet_id] || {}).owner_id || ""}
                          onChange={(e) => setPick({ ...pick, [row.outlet_id]: { ...(pick[row.outlet_id] || {}), owner_id: e.target.value } })}
                        >
                          <option value="">Pilih sales</option>
                          {users.filter((u) => u.status === "active").map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                        </select>
                      </SaField>
                      <SaField label="Alasan" required>
                        <input
                          className="sa-control"
                          placeholder="Alasan wajib"
                          value={(pick[row.outlet_id] || {}).reason || ""}
                          onChange={(e) => setPick({ ...pick, [row.outlet_id]: { ...(pick[row.outlet_id] || {}), reason: e.target.value } })}
                        />
                      </SaField>
                      <button className="primary" type="button" onClick={() => assign(row.outlet_id)}>Assign</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ImportTab({ notify }) {
  const { hasPerm } = useAuth();
  const [preview, setPreview] = useState(null);
  const [batch, setBatch] = useState(null);
  const [activation, setActivation] = useState(null);

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const res = await api.salesActivityImportPreview(file);
      setPreview(res.preview);
      setBatch(res.batch);
      setActivation(null);
      notify("Preview selesai. Belum ada perubahan data bisnis.");
    } catch (err) { notify(errText(err)); }
  };

  const confirm = async () => {
    if (!batch?.id) return;
    try {
      const res = await api.salesActivityImportConfirm(batch.id);
      setBatch(res.batch);
      notify(res.message || "Import selesai. Automation tetap paused.");
    } catch (err) { notify(errText(err)); }
  };

  const loadActivation = async () => {
    if (!batch?.id) return;
    try { setActivation(await api.salesActivityActivationPreview(batch.id)); }
    catch (err) { notify(errText(err)); }
  };

  const activate = async () => {
    if (!batch?.id) return;
    try {
      const res = await api.salesActivityActivateMigration(batch.id);
      setBatch(res.batch);
      notify(res.message);
    } catch (err) { notify(errText(err)); }
  };

  const confirmed = batch?.status === "CONFIRMED";
  const fileButton = (
    <label className={preview || confirmed ? "ghost sa-file-btn" : "primary sa-file-btn"}>
      {preview || confirmed ? "Upload ulang" : "Upload File"}
      <input type="file" accept=".xlsx" onChange={onFile} data-testid="sa-import-file" />
    </label>
  );

  return (
    <div data-testid="sa-import-panel">
      <SaTabHead title="Import Activity Sales" description="Upload Excel, tinjau matching dan konflik, lalu confirm. Aktivasi automation terpisah.">
        {!confirmed && preview && (
          <button className="primary" type="button" onClick={confirm} data-testid="sa-import-confirm">Confirm import</button>
        )}
        {confirmed && canActivateMigration(hasPerm) && !activation && (
          <button className="primary" type="button" onClick={loadActivation} data-testid="sa-activation-preview">Preview aktivasi</button>
        )}
        {confirmed && canActivateMigration(hasPerm) && activation && (
          <button className="primary" type="button" onClick={activate} data-testid="sa-activate-migration">Aktifkan automation data migrasi</button>
        )}
        {fileButton}
      </SaTabHead>

      <p className="sa-import-banner" data-testid="sa-import-paused-banner">
        IMPORT TIDAK MENJALANKAN AUTOMATION.
        <span className="sa-import-banner-sub">Import tidak menjalankan automation.</span>
      </p>
      <p className="sa-import-steps">Alur: Upload → Preview → Matching → Konflik → Confirm import → Imported / Automation paused → Aktifkan terpisah.</p>

      {preview && (
        <div className="sa-import-panel">
          <p>Outlet: {preview.outlets?.length || 0} · Aktivitas: {preview.activities?.length || 0} · Konflik: {preview.conflicts?.length || 0}</p>
          {(preview.conflicts || []).length > 0 && (
            <div data-testid="sa-import-conflicts">
              <h3>Konflik — tidak ditebak</h3>
              <ul>{preview.conflicts.map((c, i) => <li key={i}>{c.type} {c.sales_name || c.outlet_name || ""}</li>)}</ul>
            </div>
          )}
        </div>
      )}
      {confirmed && (
        <div className="sa-import-panel" data-testid="sa-imported-paused">
          <p>Imported. Automation: PAUSED_MIGRATION. Import tidak menjalankan automation.</p>
          {canActivateMigration(hasPerm) && activation && (
            <div data-testid="sa-activation-impact">
              <p>Prospek: {activation.prospects_to_activate} · Customer: {activation.existing_customers} · Unresolved: {activation.unresolved_ownerships} · Dormant: {activation.potential_dormant_customers} · ke Pool: {activation.records_that_will_enter_pool}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export { OutletTab, ActivityTab, PoolTab, ImportTab };
