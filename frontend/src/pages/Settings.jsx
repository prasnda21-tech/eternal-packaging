import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  Users, Shield, ScrollText, Plus, Edit3, Power, PowerOff, Key, Trash2, ShieldCheck, ShieldAlert, ArrowRightLeft,
  Save, X, RefreshCw, Search, AlertCircle, Info, Lock, Check, Filter, KeyRound, ClipboardCheck, Truck, Bell,
  Target as TargetIcon, PanelLeft, ClipboardList, Coins, Scale, IdCard, Wallet, Route, Tags,
  BookOpen,
  CreditCard,
  Package,
  Printer,
  CalendarClock,
  Settings as SettingsIcon,
} from "lucide-react";
import api from "../lib/api";
import { roleSidebarPreview } from "../lib/desktopSidebarModel";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { NAV_MENU_FALLBACK, settingsAliasFromPath, settingsTabFromPath } from "../lib/navAccess";
import PricingCatalogSettings from "./PricingCatalogSettings";
import { addedOptInPermissions, businessPermissionModules, isSidebarNavPermissionKey, roleBusinessSavePayload, expandLoadingDraftForEdit } from "../lib/settingsPermissionUi";
import {
  confirmPackageKeysForSave,
  isDirectorFullAccessRole,
  isPackagePermissionKey,
  packageKeysFromOverride,
  packageKeysToActivate,
  PACKAGE_PRODUCTION_LOCK_HINT,
  pruneConfirmReviewKeys,
  roleMayReceivePackageAccess,
  rolePermissionsPutBody,
  unconfirmedPackageKeys,
  userOverridePutBody,
} from "../lib/packagePermissions";
import RolePermissionWorkspace from "../components/RolePermissionWorkspace";
import OperatorWageSettings from "./OperatorWageSettings";
import HppSettings from "./HppSettings";
import PhysicalStockSettings from "./PhysicalStockSettings";
import DesignCetakanSettings from "./DesignCetakanSettings";
import SalesAccountingSettings from "./SalesAccountingSettings";
import PurchaseAccountingSettings from "./PurchaseAccountingSettings";
import PayrollAccountingSettings from "./PayrollAccountingSettings";
import FormAccessMatrix from "../components/FormAccessMatrix";
import OperationalHolidaySettings, { OperationalHolidayReadonly } from "../components/OperationalHolidaySettings";
import EmployeeAdminSettings from "../components/EmployeeAdminSettings";
import HrPayrollSettings from "../components/HrPayrollSettings";
import SalesActivitySettings from "../components/SalesActivitySettings";
import RouteOptimizerSettings from "../components/RouteOptimizerSettings";
import SalesUserMappingSettings from "../components/SalesUserMappingSettings";
import SalesMasterSettings from "../components/SalesMasterSettings";
import CrmDailyReportOptionsSettings from "../components/CrmDailyReportOptionsSettings";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";

const ROLE_LABELS = {
  admin: "Admin",
  head_production: "Kepala Produksi",
  head_operations: "Kepala Operasional",
  designer: "Designer",
  screener: "Screener",
  helper: "Helper",
  operator: "Operator",
  driver: "Driver",
  crm: "CRM",
  holding_team: "Holding",
  direktur_utama: "Direktur",
  sales_eternal: "Sales Eternal",
};
const ROLE_KEYS = Object.keys(ROLE_LABELS);

const ACTION_LABELS = {
  "user.create": "Tambah karyawan",
  "user.update": "Ubah karyawan",
  "user.deactivate": "Nonaktifkan karyawan",
  "user.activate": "Aktifkan karyawan",
  "user.password_reset": "Reset kata sandi",
  "role.create": "Buat role",
  "role.update": "Ubah role",
  "role.delete": "Hapus role",
  "role.activate": "Aktifkan role",
  "role.deactivate": "Nonaktifkan role",
  "role.permissions.update": "Ubah hak akses role",
  "super_admin.grant": "Jadikan Super Admin",
  "super_admin.revoke": "Cabut Super Admin",
  "super_admin.transfer": "Transfer Super Admin",
};

/* ---------------- Small helpers ---------------- */
function Toast({ msg }) {
  if (!msg) return null;
  return <div className="toast" data-testid="settings-toast"><Check size={16} /> {msg}</div>;
}

function InfoBanner({ children, testid = "settings-info" }) {
  return (
    <div className="settings-info" data-testid={testid}>
      <Info size={16} />
      <div>{children}</div>
    </div>
  );
}

/* ---------------- Employee create/edit form ---------------- */
function EmployeeForm({ initial, onSubmit, onCancel, mode, permissionCatalog, canManageRoles, canManagePerms, onResetPassword, roles = [] }) {
  const { user: actor } = useAuth();
  const canAssignBranches = ["direktur_utama", "holding_team"].includes(actor?.role);
  const [branchOptions, setBranchOptions] = useState([]);
  useEffect(() => {
    if (!canAssignBranches) return;
    let alive = true;
    api.operationalBranchOptions().then((d) => {
      if (!alive) return;
      setBranchOptions(d.branches || []);
      if (!initial && d.primary_branch_id) setForm((f) => ({...f,
        branch_ids: f.branch_ids.length ? f.branch_ids : [d.primary_branch_id],
        primary_branch_id: f.primary_branch_id || d.primary_branch_id}));
    })
      .catch((e) => { if (alive) setErr(e.response?.data?.detail || "Gagal memuat cabang"); });
    return () => { alive = false; };
  }, [canAssignBranches, initial]);
  const isEdit = !!initial;
  const showData = mode !== "perms";
  const showPerms = mode !== "edit" && !!permissionCatalog && (canManagePerms || !isEdit);

  const [form, setForm] = useState(() => ({
    branch_ids: initial?.branch_ids || [],
    primary_branch_id: initial?.primary_branch_id || initial?.branch_id || "",
    email: initial?.email || "",
    notification_email: initial?.notification_email || "",
    name: initial?.name || "",
    nickname: initial?.nickname || "",
    phone: initial?.phone || "",
    role: initial?.role || "helper",
    joined_at: initial?.joined_at || "",
    notes: initial?.notes || "",
    permissions_override: initial?.permissions_override || {},
    password: "",
  }));
  const [busy, setBusy] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [err, setErr] = useState("");
  const [permQuery, setPermQuery] = useState("");
  const [effective, setEffective] = useState(null); // {default_from_role, effective}
  const [userPkgConfirmOptIn, setUserPkgConfirmOptIn] = useState(false);
  const [userPkgReviewKeys, setUserPkgReviewKeys] = useState([]);
  const userConfirmedPackageKeys = initial?.package_production_confirmed_keys || [];
  const userOverridePackageKeys = packageKeysFromOverride(form.permissions_override);
  const userPendingPackage = unconfirmedPackageKeys(userOverridePackageKeys, userConfirmedPackageKeys);
  const userActivationPreview = packageKeysToActivate(userOverridePackageKeys, userPkgReviewKeys);
  const isDirectorEmployee = isDirectorFullAccessRole(form.role);

  // Req 15: fetch effective breakdown (default_from_role + effective) so UI
  // shows Default / Override / Effective triad instead of asking Admin to guess.
  useEffect(() => {
    let cancelled = false;
    if (isEdit && initial?.id && showPerms) {
      api.adminUserEffectivePerms(initial.id).then((d) => { if (!cancelled) setEffective(d); }).catch(() => {});
    }
    return () => { cancelled = true; };
  }, [isEdit, initial?.id, showPerms, form.permissions_override, form.role]);

  const roleDefaultSet = new Set(effective?.default_from_role || []);
  const effectiveSet = new Set(effective?.effective || []);

  // Drop carried confirm state when switching employee/target.
  useEffect(() => {
    setUserPkgConfirmOptIn(false);
    setUserPkgReviewKeys([]);
  }, [initial?.id]);

  // Unchecked / non-grant package overrides leave the reviewed confirm set.
  useEffect(() => {
    setUserPkgReviewKeys((prev) => pruneConfirmReviewKeys(prev, userOverridePackageKeys));
  }, [userOverridePackageKeys.join("|")]);

  const employeePackageAccessAllowed = roleMayReceivePackageAccess(form.role);

  const togglePerm = (key, value) => {
    if (isPackagePermissionKey(key) && value === "grant" && !employeePackageAccessAllowed) {
      // Non-Holding/Direktur cannot gain effective package access via override.
      return;
    }
    setForm((f) => {
      const next = { ...f.permissions_override };
      if (value === "inherit") delete next[key];
      else next[key] = value === "grant";
      return { ...f, permissions_override: next };
    });
    // Uncheck / deny / inherit cancels confirm for that package key.
    // Re-grant does NOT restore a previous confirm — must re-review.
    if (isPackagePermissionKey(key) && value !== "grant") {
      setUserPkgReviewKeys((prev) => prev.filter((k) => k !== key));
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const payload = {};
      if (showData) {
        if (canAssignBranches) {
          if (!form.branch_ids.length || !form.branch_ids.includes(form.primary_branch_id)) {
            setErr("Pilih akses cabang dan cabang utama karyawan.");
            return;
          }
          payload.branch_ids = form.branch_ids;
          payload.primary_branch_id = form.primary_branch_id;
        }
        payload.name = form.name;
        payload.nickname = form.nickname || null;
        payload.phone = form.phone || null;
        payload.role = form.role;
        payload.joined_at = form.joined_at || null;
        payload.notes = form.notes || null;
        const notifyRaw = (form.notification_email || "").trim().toLowerCase();
        if (notifyRaw) {
          // Lightweight client check; backend re-validates strictly.
          if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(notifyRaw)) {
            setErr("Format email notifikasi tidak valid");
            setBusy(false);
            return;
          }
          payload.notification_email = notifyRaw;
        } else {
          payload.notification_email = "";
        }
      }
      if (showPerms) {
        const confirmKeys = confirmPackageKeysForSave(
          userOverridePackageKeys,
          userPkgReviewKeys,
          { optIn: userPkgConfirmOptIn },
        );
        // Keep UI state aligned with payload before request.
        if (confirmKeys) setUserPkgReviewKeys(confirmKeys);
        const overrideBody = confirmKeys !== undefined
          ? userOverridePutBody(form.permissions_override, confirmKeys)
          : userOverridePutBody(form.permissions_override);
        Object.assign(payload, overrideBody);
      }
      if (!isEdit) {
        payload.email = form.email.trim().toLowerCase();
        if (form.password) payload.password = form.password;
      }
      await onSubmit(payload);
    } catch (ex) {
      setErr(ex.response?.data?.detail || "Gagal menyimpan");
    } finally {
      setBusy(false);
    }
  };

  const filteredModules = useMemo(
    () => businessPermissionModules(permissionCatalog, permQuery),
    [permissionCatalog, permQuery]
  );

  return (
    <form className="emp-form emp-modal-form" onSubmit={submit} data-testid="employee-form">
      <div className="emp-modal-body" data-testid="employee-modal-body">
        <FormProgress
        percentage={Math.round(([form.name, form.role, (isEdit ? true : form.email)].filter(Boolean).length / 3) * 100)}
        completed={[form.name, form.role, (isEdit ? true : form.email)].filter(Boolean).length}
        total={3}
        className="mb-4"
      />

      {showData && (
        <FormSection
          id="settings-emp-data"
          title="Data Akun & Karyawan"
          description="Informasi kredensial login dan profil pengguna."
          status={computeStatus([
            { value: form.email, isRequired: !isEdit },
            { value: form.name, isRequired: true },
            { value: form.role, isRequired: true },
          ])}
        >
          <div>
            <strong>Akses Cabang</strong>
            {canAssignBranches ? <>
              {branchOptions.map((b) => <label key={b.id}>
                <input type="checkbox" checked={form.branch_ids.includes(b.id)} disabled={busy}
                  onChange={(e) => setForm((f) => {
                    const ids = e.target.checked ? [...f.branch_ids, b.id] : f.branch_ids.filter((id) => id !== b.id);
                    return {...f, branch_ids: ids, primary_branch_id: ids.includes(f.primary_branch_id) ? f.primary_branch_id : ids[0] || ""};
                  })} />{b.name || b.code}
              </label>)}
              <label>Cabang utama<select value={form.primary_branch_id} disabled={busy}
                onChange={(e) => setForm((f) => ({...f, primary_branch_id: e.target.value}))}>
                <option value="">Pilih cabang utama</option>
                {branchOptions.filter((b) => form.branch_ids.includes(b.id)).map((b) => <option key={b.id} value={b.id}>{b.name || b.code}</option>)}
              </select></label>
            </> : <p>Pengaturan akses cabang dikelola Direktur/Holding.</p>}
          </div>
          <div className="emp-grid">
            <FormField id="emp-email-field" label="Email login" isRequired={!isEdit} value={form.email}>
              <input type="email" required disabled={isEdit || busy}
                value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}
                data-testid="emp-email"/>
            </FormField>
            <div className="emp-notify-email-block" data-testid="emp-notification-email-block">
              <FormField
                id="emp-notification-email-field"
                label="Email Notifikasi"
                isRequired={false}
                value={form.notification_email}
              >
                <input
                  type="text"
                  inputMode="email"
                  disabled={busy}
                  value={form.notification_email}
                  onChange={(e) => setForm((f) => ({ ...f, notification_email: e.target.value }))}
                  placeholder="opsional — gmail / yahoo / outlook / dll"
                  data-testid="emp-notification-email"
                  autoComplete="off"
                />
              </FormField>
              <p className="hint" style={{ margin: "2px 0 0" }} data-testid="emp-notification-email-hint">
                Digunakan untuk menerima pemberitahuan operasional. Tidak mengubah email login.
              </p>
              {!String(form.notification_email || "").trim() && (
                <p className="hint" style={{ margin: "2px 0 0" }} data-testid="emp-notification-email-empty-hint">
                  Kosong = tidak ada notifikasi email yang dikirim ke pengguna ini.
                </p>
              )}
            </div>
          <label>Role
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}
              disabled={busy || !canManageRoles} data-testid="emp-role">
              {(() => {
                // Bugfix: dropdown Role harus mengambil semua role active dari backend
                // (built-in + custom). Selalu include current form.role — meskipun inactive —
                // agar historis user existing tetap terbaca dan tidak berubah saat edit.
                const activeRoles = (roles || []).filter((r) => r.status !== "inactive");
                const currentSlug = form.role;
                const hasCurrent = activeRoles.some((r) => r.role === currentSlug);
                const list = hasCurrent ? activeRoles : [
                  ...activeRoles,
                  ...((roles || []).filter((r) => r.role === currentSlug)),
                ];
                // Fallback: kalau roles belum ter-load, tampilkan minimal current role
                if (list.length === 0 && currentSlug) {
                  return <option value={currentSlug}>{ROLE_LABELS[currentSlug] || currentSlug}</option>;
                }
                return list.map((r) => (
                  <option key={r.role} value={r.role} data-testid={`emp-role-option-${r.role}`}>
                    {r.label || ROLE_LABELS[r.role] || r.role}{r.status === "inactive" ? " (nonaktif)" : ""}
                  </option>
                ));
              })()}
            </select>
            {!canManageRoles && <span className="hint">Butuh permission <b>roles.manage</b> untuk mengubah role.</span>}
          </label>
          <label>Nama lengkap
            <input type="text" required disabled={busy}
              value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              data-testid="emp-name"/>
          </label>
          <label>Nama panggilan
            <input type="text" disabled={busy}
              value={form.nickname} onChange={(e) => setForm({ ...form, nickname: e.target.value })}
              data-testid="emp-nickname"/>
          </label>
          <label>Nomor HP
            <input type="tel" disabled={busy}
              value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })}
              data-testid="emp-phone"/>
          </label>
          <label>Tanggal bergabung
            <input type="date" disabled={busy}
              value={(form.joined_at || "").slice(0, 10)}
              onChange={(e) => setForm({ ...form, joined_at: e.target.value })}
              data-testid="emp-joined"/>
          </label>
          {!isEdit && (
            <label>Password awal
              <input type="password" disabled={busy}
                placeholder="Kosongkan untuk default"
                value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })}
                data-testid="emp-password"/>
            </label>
          )}
          <label className="emp-notes">Catatan
            <textarea rows="2" disabled={busy}
              value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })}
              data-testid="emp-notes"/>
          </label>
          </div>
        </FormSection>
      )}

      {showPerms && isDirectorEmployee && (
        <FormSection
          id="settings-emp-director-lock"
          title="Akses penuh sistem"
          description="Role Direktur mengabaikan override deny/grant pendek untuk akses efektif."
          status="neutral"
        >
          <div className="settings-info" data-testid="emp-director-lock">
            Karyawan dengan role <b>Direktur</b> mendapat akses penuh sistem. Override di bawah tidak mengurangi
            akses efektif; simpan override tetap diperbolehkan untuk audit dokumen saja.
          </div>
        </FormSection>
      )}

      {showPerms && (
        <FormSection
          id="settings-emp-perms"
          title="Hak Akses Khusus (Override)"
          description="Default = ikut role. Grant menambah, Deny menghilangkan untuk user ini saja."
          status="neutral"
        >
          <div className="emp-perms">
          {!employeePackageAccessAllowed && (
            <div className="settings-info" data-testid="emp-package-lock-banner" style={{ marginBottom: 12 }}>
              <strong>Package Pembelian / Persediaan / HPP</strong>
              <p className="hint" style={{ margin: "4px 0 0" }}>{PACKAGE_PRODUCTION_LOCK_HINT}</p>
              <p className="muted small" style={{ marginTop: 6 }}>
                Tombol Grant untuk izin package dinonaktifkan pada role ini. Kolom Efektif mengikuti backend
                (sama dengan /auth/me).
              </p>
            </div>
          )}
          {employeePackageAccessAllowed && userOverridePackageKeys.length > 0 && (
            <div className="settings-info" data-testid="emp-package-confirm-panel" style={{ marginBottom: 12 }}>
              <strong>Package override (Holding/Direktur)</strong>
              <p className="hint">
                Stamp konfirmasi opsional untuk audit override package. Akses efektif Holding/Direktur
                tetap mengikuti kebijakan package runtime.
              </p>
              <label style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 8 }}>
                <input
                  type="checkbox"
                  checked={userPkgConfirmOptIn}
                  onChange={(e) => {
                    const on = e.target.checked;
                    setUserPkgConfirmOptIn(on);
                    if (on && !userPkgReviewKeys.length) {
                      setUserPkgReviewKeys(
                        packageKeysToActivate(userOverridePackageKeys, userConfirmedPackageKeys),
                      );
                    }
                  }}
                  data-testid="emp-package-confirm-optin"
                />
                <span>Catat stamp konfirmasi package override untuk daftar yang ditinjau</span>
              </label>
              {userPkgConfirmOptIn && (
                <ul data-testid="emp-package-confirm-list" style={{ marginTop: 8, paddingLeft: 18 }}>
                  {userOverridePackageKeys.map((key) => (
                    <li key={key}>
                      <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={userPkgReviewKeys.includes(key)}
                          onChange={() => {
                            setUserPkgReviewKeys((prev) => {
                              const next = new Set(prev);
                              if (next.has(key)) next.delete(key);
                              else next.add(key);
                              return [...next].sort();
                            });
                          }}
                          data-testid={`emp-package-review-check-${key}`}
                        />
                        <code>{key}</code>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              {userPkgConfirmOptIn && (
                <p className="muted small" data-testid="emp-package-activation-preview">
                  {userActivationPreview.length
                    ? `${userActivationPreview.length} key override akan dikonfirmasi.`
                    : "Tidak ada key override yang dikonfirmasi pada simpan ini."}
                </p>
              )}
            </div>
          )}
          <div className="emp-perms-head">
            <div>
              <h4>Hak akses khusus (override)</h4>
              <p className="hint">Default = ikut role. Grant menambah, Deny menghilangkan untuk user ini saja. Kolom <strong>Efektif</strong> menunjukkan hasil akhir (termasuk filter package production).</p>
            </div>
            <div className="perm-search">
              <Search size={14}/>
              <input placeholder="Cari permission..." value={permQuery} onChange={(e) => setPermQuery(e.target.value)} data-testid="emp-perm-search"/>
            </div>
            {isEdit && Object.keys(form.permissions_override || {}).length > 0 && (
              <button
                type="button"
                className="ghost sm"
                data-testid="emp-reset-override"
                disabled={resetBusy || busy}
                onClick={async () => {
                  if (!window.confirm("Hapus semua override dan kembalikan ke default role?")) return;
                  setResetBusy(true);
                  try {
                    await api.adminResetUserOverride(initial.id);
                    setForm((f) => ({ ...f, permissions_override: {} }));
                    if (initial?.id) {
                      const d = await api.adminUserEffectivePerms(initial.id);
                      setEffective(d);
                    }
                  } catch (ex) {
                    setErr(ex.response?.data?.detail || "Gagal reset override");
                  } finally {
                    setResetBusy(false);
                  }
                }}
              >Reset Semua Override</button>
            )}
          </div>
          <div className="perm-modules">
            {filteredModules.map(([mod, perms]) => (
              <div key={mod} className="perm-module-card">
                <div className="perm-module-title">{mod}</div>
                <div className="perm-module-list">
                  {perms.map((p) => {
                    const cur = form.permissions_override[p.key];
                    const state = cur === true ? "grant" : cur === false ? "deny" : "inherit";
                    const inDefault = roleDefaultSet.has(p.key);
                    const inEffective = effectiveSet.has(p.key);
                    return (
                      <div key={p.key} className={`perm-item perm-${state}`} data-testid={`perm-row-${p.key}`}>
                        <div className="perm-item-label">
                          <span>{p.label}</span>
                          <span className="mono muted">{p.key}</span>
                          {effective && (
                            <span className="perm-triad" data-testid={`perm-triad-${p.key}`} style={{display:"flex",gap:6,marginTop:4,flexWrap:"wrap"}}>
                              <span className={`pill xs ${inDefault ? "pill-ok" : "pill-muted"}`} data-testid={`perm-default-${p.key}`}>
                                Default: {inDefault ? "Diizinkan" : "Tidak"}
                              </span>
                              <span className={`pill xs ${state === "inherit" ? "pill-muted" : state === "grant" ? "pill-ok" : "pill-warn"}`} data-testid={`perm-override-${p.key}`}>
                                Override: {state === "inherit" ? "Ikut role" : state === "grant" ? "Allow" : "Deny"}
                              </span>
                              <span className={`pill xs ${inEffective ? "pill-ok" : "pill-warn"}`} data-testid={`perm-effective-${p.key}`}>
                                Efektif: {inEffective ? "Diizinkan" : "Tidak Diizinkan"}
                              </span>
                            </span>
                          )}
                        </div>
                        <div className="perm-seg">
                          <button type="button" className={state === "inherit" ? "on" : ""} onClick={() => togglePerm(p.key, "inherit")} data-testid={`perm-inherit-${p.key}`}>Ikut role</button>
                          <button
                            type="button"
                            className={state === "grant" ? "on" : ""}
                            onClick={() => togglePerm(p.key, "grant")}
                            data-testid={`perm-grant-${p.key}`}
                            disabled={isPackagePermissionKey(p.key) && !employeePackageAccessAllowed}
                            title={
                              isPackagePermissionKey(p.key) && !employeePackageAccessAllowed
                                ? PACKAGE_PRODUCTION_LOCK_HINT
                                : undefined
                            }
                          >
                            Grant
                          </button>
                          <button type="button" className={state === "deny" ? "on" : ""} onClick={() => togglePerm(p.key, "deny")} data-testid={`perm-deny-${p.key}`}>Deny</button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
            {!filteredModules.length && <p className="muted small">Tidak ada permission cocok.</p>}
          </div>
        </div>
      </FormSection>
    )}

        {err && <div className="error" data-testid="employee-form-error">{err}</div>}
      </div>

      <footer className="emp-modal-foot" data-testid="employee-modal-footer">
        <div className="emp-foot-secondary">
          {isEdit && showData && onResetPassword && (
            <button type="button" className="ghost sm" onClick={onResetPassword} disabled={busy} data-testid="emp-reset-password">
              <KeyRound size={13}/> Reset password
            </button>
          )}
        </div>
        <div className="emp-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={busy} data-testid="employee-form-cancel"><X size={14}/> Batal</button>
          <button type="submit" className="primary" disabled={busy} data-testid="employee-form-submit"><Save size={14}/> {isEdit ? "Simpan perubahan" : "Buat karyawan"}</button>
        </div>
      </footer>
    </form>
  );
}

/* ---------------- Karyawan tab ---------------- */
function EmployeesTab({ permissionCatalog, canManage, canManageRoles, canManagePerms, currentUser, notify }) {
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]); // Bugfix: single source of truth utk dropdown role
  const [statusFilter, setStatusFilter] = useState("active");
  const [roleFilter, setRoleFilter] = useState("all");
  const [q, setQ] = useState("");
  const [modal, setModal] = useState(null); // {type: 'create'|'edit'|'perms'|'transfer-sa'|'reset-password', user?}
  const [transferTo, setTransferTo] = useState("");
  const [transferReason, setTransferReason] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [resetPasswordConfirm, setResetPasswordConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const isSuperAdmin = !!currentUser?.is_super_admin;

  // Bugfix: build a label map dari backend roles (built-in + custom) untuk render
  // di seluruh table/filter. Fallback ke ROLE_LABELS untuk kompatibilitas.
  const roleLabelOf = (slug) => {
    const found = (roles || []).find((r) => r.role === slug);
    return found?.label || ROLE_LABELS[slug] || slug;
  };

  const load = async () => {
    setBusy(true);
    try {
      const [du, dr] = await Promise.all([
        api.adminListUsers(),
        api.adminListRoles().catch(() => ({ roles: [] })),
      ]);
      setUsers(du.users || []);
      setRoles(dr.roles || []);
    } catch (e) { setErr(e.response?.data?.detail || "Gagal memuat"); }
    finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []);

  const visible = useMemo(() => users.filter((u) => {
    if (statusFilter !== "all" && u.status !== statusFilter) return false;
    if (roleFilter !== "all" && u.role !== roleFilter) return false;
    if (!q) return true;
    const t = q.toLowerCase();
    return (u.name || "").toLowerCase().includes(t) || (u.email || "").toLowerCase().includes(t) || (u.employee_id || "").toLowerCase().includes(t);
  }), [users, statusFilter, roleFilter, q]);

  const superAdminCount = useMemo(() => users.filter((u) => u.is_super_admin && u.status === "active").length, [users]);

  const close = () => {
    setModal(null);
    setTransferTo("");
    setTransferReason("");
    setResetPassword("");
    setResetPasswordConfirm("");
    setErr("");
  };
  const doCreate = async (payload) => { await api.adminCreateUser(payload); close(); notify("Karyawan berhasil ditambahkan"); await load(); };
  const doEdit = async (payload) => { await api.adminUpdateUser(modal.user.id, payload); close(); notify("Data karyawan disimpan"); await load(); };
  const doPerms = async (payload) => { await api.adminUpdateUser(modal.user.id, payload); close(); notify("Hak akses berhasil disimpan"); await load(); };
  const doDeactivate = async (u) => {
    if (!window.confirm(`Nonaktifkan ${u.name}? Histori PO lama tetap aman.`)) return;
    try {
      await api.adminDeactivateUser(u.id);
      notify(`${u.name} dinonaktifkan`);
      await load();
    } catch (e) { alert(e.response?.data?.detail?.message || e.response?.data?.detail || "Gagal nonaktifkan"); }
  };
  const doActivate = async (u) => {
    await api.adminActivateUser(u.id);
    notify(`${u.name} diaktifkan kembali`);
    await load();
  };
  const openResetPassword = (u) => {
    setErr("");
    setResetPassword("");
    setResetPasswordConfirm("");
    setModal({ type: "reset-password", user: u });
  };
  const doResetPassword = async () => {
    const pwd = (resetPassword || "").trim();
    const confirm = (resetPasswordConfirm || "").trim();
    if (pwd.length < 6) {
      setErr("Password minimal 6 karakter");
      return;
    }
    if (pwd !== confirm) {
      setErr("Konfirmasi password tidak sama");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await api.adminResetPassword(modal.user.id, pwd);
      close();
      notify("Password berhasil direset");
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal reset password");
    } finally {
      setBusy(false);
    }
  };
  const doGrantSA = async (u) => {
    if (!window.confirm(`Jadikan ${u.name} sebagai Super Admin? Super Admin memiliki seluruh hak akses.`)) return;
    try {
      await api.adminGrantSuperAdmin(u.id);
      notify(`${u.name} sekarang Super Admin`);
      await load();
    } catch (e) { alert(e.response?.data?.detail?.message || e.response?.data?.detail || "Gagal"); }
  };
  const doRevokeSA = async (u) => {
    if (!window.confirm(`Cabut status Super Admin dari ${u.name}?`)) return;
    try {
      await api.adminRevokeSuperAdmin(u.id);
      notify(`Super Admin ${u.name} dicabut`);
      await load();
    } catch (e) { alert(e.response?.data?.detail?.message || e.response?.data?.detail || "Gagal"); }
  };
  const doTransferSA = async () => {
    if (!transferTo) { setErr("Pilih karyawan tujuan."); return; }
    try {
      await api.adminTransferSuperAdmin(modal.user.id, transferTo, transferReason);
      notify("Super Admin ditransfer");
      close();
      await load();
    } catch (e) { setErr(e.response?.data?.detail?.message || e.response?.data?.detail || "Gagal transfer"); }
  };

  return (
    <div className="settings-body" data-testid="employees-tab">
      <div className="emp-toolbar">
        <div className="search-inline">
          <Search size={14}/>
          <input placeholder="Cari nama, email, atau employee ID" value={q} onChange={(e) => setQ(e.target.value)} data-testid="employee-search"/>
        </div>
        <div className="emp-filter">
          <Filter size={13}/>
          <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} data-testid="employee-filter-role">
            <option value="all">Semua role</option>
            {/* Bugfix: filter role juga pakai backend sources (built-in + custom + inactive utk historis) */}
            {(roles || []).map((r) => (
              <option key={r.role} value={r.role}>{r.label || ROLE_LABELS[r.role] || r.role}{r.status === "inactive" ? " (nonaktif)" : ""}</option>
            ))}
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} data-testid="employee-filter-status">
            <option value="active">Aktif</option>
            <option value="inactive">Nonaktif</option>
            <option value="all">Semua status</option>
          </select>
        </div>
        <span className="muted small" data-testid="super-admin-count">{superAdminCount} Super Admin aktif</span>
        <button className="icon-button" onClick={load} title="Muat ulang" data-testid="employee-refresh"><RefreshCw size={16} className={busy ? "spin" : ""}/></button>
        {canManage && <button className="primary" onClick={() => setModal({ type: "create" })} data-testid="employee-add-button"><Plus size={14}/> Tambah Karyawan</button>}
      </div>

      {err && <div className="error" data-testid="employees-error">{typeof err === "object" ? err.message : err}</div>}

      <div className="emp-table-wrap" data-testid="employees-table">
        <div className="emp-thead">
          <span>Employee ID</span>
          <span>Nama</span>
          <span>Role</span>
          <span>Status</span>
          <span>Email</span>
          <span className="emp-th-actions">Aksi</span>
        </div>
        {visible.map((u) => (
          <div key={u.id} className={`emp-trow emp-${u.status}`} data-testid={`employee-row-${u.email}`}>
            <span className="mono muted">{u.employee_id || "-"}</span>
            <span className="emp-name-cell">
              <strong>{u.name}</strong>
              {u.nickname && <span className="muted small"> · {u.nickname}</span>}
              {u.is_super_admin && <span className="pill sm pill-sa" data-testid={`super-admin-badge-${u.email}`} title="Super Admin"><ShieldCheck size={10}/> Super Admin</span>}
            </span>
            <span><span className="role-badge">{roleLabelOf(u.role)}</span></span>
            <span>
              {u.status === "active"
                ? <span className="status-dot status-ok"><span/>Aktif</span>
                : <span className="status-dot status-off"><span/>Nonaktif</span>}
            </span>
            <span className="mono ellipsis" title={u.email}>{u.email}</span>
            <span className="emp-row-actions">
              {canManage && (
                <>
                  <button className="ghost sm" onClick={() => setModal({ type: "edit", user: u })} data-testid={`employee-edit-${u.email}`}><Edit3 size={12}/> Edit</button>
                  <button className="ghost sm" onClick={() => setModal({ type: "perms", user: u })} data-testid={`employee-perms-${u.email}`}><Shield size={12}/> Atur Hak Akses</button>
                  {u.status === "active"
                    ? <button className="ghost sm danger" onClick={() => doDeactivate(u)} data-testid={`employee-deactivate-${u.email}`}><PowerOff size={12}/> Nonaktifkan</button>
                    : <button className="ghost sm" onClick={() => doActivate(u)} data-testid={`employee-activate-${u.email}`}><Power size={12}/> Aktifkan</button>
                  }
                </>
              )}
              {isSuperAdmin && u.status === "active" && (
                u.is_super_admin ? (
                  <>
                    <button className="ghost sm" onClick={() => setModal({ type: "transfer-sa", user: u })} data-testid={`employee-transfer-sa-${u.email}`}><ArrowRightLeft size={12}/> Transfer SA</button>
                    <button className="ghost sm danger" onClick={() => doRevokeSA(u)} data-testid={`employee-revoke-sa-${u.email}`}><ShieldAlert size={12}/> Cabut SA</button>
                  </>
                ) : (
                  <button className="ghost sm" onClick={() => doGrantSA(u)} data-testid={`employee-grant-sa-${u.email}`}><ShieldCheck size={12}/> Jadikan SA</button>
                )
              )}
              {!canManage && !isSuperAdmin && <span className="muted small">read-only</span>}
            </span>
          </div>
        ))}
        {!visible.length && !busy && <div className="empty-state">Tidak ada karyawan sesuai filter.</div>}
      </div>

      {modal && modal.type === "transfer-sa" && (
        <div className="modal" data-testid="transfer-sa-modal" onClick={close}>
          <div className="modal-inner" style={{maxWidth:520}} onClick={(e) => e.stopPropagation()}>
            <header className="modal-head"><h3>Transfer Super Admin</h3><button onClick={close} className="icon-button"><X size={16}/></button></header>
            <div className="emp-form">
              <p className="muted small">Pindahkan status Super Admin dari <b>{modal.user.name}</b> ke karyawan lain. Sumber akan otomatis kehilangan status Super Admin setelah transfer selesai.</p>
              <div className="emp-grid">
                <label className="emp-notes">Karyawan tujuan
                  <select value={transferTo} onChange={(e) => setTransferTo(e.target.value)} data-testid="transfer-sa-target">
                    <option value="">— Pilih karyawan aktif —</option>
                    {users.filter((x) => x.status === "active" && x.id !== modal.user.id && !x.is_super_admin).map((x) => (
                      <option key={x.id} value={x.id}>{x.name} · {x.email} ({ROLE_LABELS[x.role] || x.role})</option>
                    ))}
                  </select>
                </label>
                <label className="emp-notes">Alasan transfer
                  <textarea rows={2} value={transferReason} onChange={(e) => setTransferReason(e.target.value)} placeholder="Contoh: rotasi jabatan / cuti panjang" data-testid="transfer-sa-reason"/>
                </label>
              </div>
              {err && <div className="error">{typeof err === "object" ? err.message : err}</div>}
              <div className="emp-actions">
                <button className="ghost" onClick={close}>Batal</button>
                <button className="primary" onClick={doTransferSA} disabled={!transferTo} data-testid="transfer-sa-submit"><ArrowRightLeft size={14}/> Transfer</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {modal && ["create","edit","perms"].includes(modal.type) && canManage && (
        <div className="modal modal-viewport-safe-wrap" data-testid="employee-modal" onClick={(e) => e.target === e.currentTarget && close()}>
          <div className={"modal-inner modal-viewport-safe " + (modal.type === "perms" ? "modal-wide" : "")}>
            <header className="modal-head">
              <h3>
                {modal.type === "create" && "Tambah Karyawan"}
                {modal.type === "edit" && `Edit ${modal.user.name}`}
                {modal.type === "perms" && `Hak akses · ${modal.user.name}`}
              </h3>
              <button onClick={close} className="icon-button" aria-label="Tutup"><X size={16}/></button>
            </header>
            <EmployeeForm
              initial={modal.user}
              mode={modal.type === "edit" ? "edit" : modal.type === "perms" ? "perms" : "create"}
              permissionCatalog={permissionCatalog}
              canManageRoles={canManageRoles}
              canManagePerms={canManagePerms}
              roles={roles}
              onResetPassword={modal.type === "edit" ? () => openResetPassword(modal.user) : null}
              onCancel={close}
              onSubmit={modal.type === "create" ? doCreate : modal.type === "edit" ? doEdit : doPerms}
            />
          </div>
        </div>
      )}

      {modal && modal.type === "reset-password" && canManage && (
        <div className="modal" data-testid="reset-password-modal" onClick={(e) => e.target === e.currentTarget && close()}>
          <div className="modal-inner" style={{ maxWidth: 480 }} onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h3>Reset password · {modal.user.name}</h3>
              <button type="button" onClick={close} className="icon-button" aria-label="Tutup"><X size={16}/></button>
            </header>
            <div className="emp-form">
              <p className="muted small">Password baru untuk <b>{modal.user.email}</b>. Minimal 6 karakter. Hash disimpan di server — plain-text tidak pernah disimpan.</p>
              <div className="emp-grid">
                <label>Password baru
                  <input
                    type="password"
                    value={resetPassword}
                    onChange={(e) => setResetPassword(e.target.value)}
                    autoComplete="new-password"
                    disabled={busy}
                    data-testid="reset-password-input"
                  />
                </label>
                <label>Konfirmasi password baru
                  <input
                    type="password"
                    value={resetPasswordConfirm}
                    onChange={(e) => setResetPasswordConfirm(e.target.value)}
                    autoComplete="new-password"
                    disabled={busy}
                    data-testid="reset-password-confirm"
                  />
                </label>
              </div>
              {err && <div className="error" data-testid="reset-password-error">{typeof err === "object" ? err.message : err}</div>}
              <div className="emp-actions">
                <button type="button" className="ghost" onClick={close} disabled={busy}>Batal</button>
                <button type="button" className="primary" onClick={doResetPassword} disabled={busy} data-testid="reset-password-submit">
                  <KeyRound size={14}/> Reset password
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- Sidebar access matrix ---------------- */
function navKeysOf(permissions) {
  return new Set((permissions || []).filter((p) => p.startsWith("nav.") && p !== "nav.configured"));
}

function NavAccessMatrix({ roles, permissionCatalog, canManagePerms, notify, onSaved, currentUserRole, onRefreshMe }) {
  const menus = (permissionCatalog?.nav_menu?.length ? permissionCatalog.nav_menu : NAV_MENU_FALLBACK);
  const visibleRoles = (roles || []).filter((r) => r.status !== "inactive");
  const [draft, setDraft] = useState({});
  const [initial, setInitial] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const next = {};
    (roles || []).forEach((r) => { next[r.role] = navKeysOf(r.permissions); });
    setDraft(next);
    setInitial(next);
  }, [roles]);

  const parentOf = useMemo(() => {
    const map = {};
    menus.forEach((m) => { if (m.parent) map[m.key] = m.parent; });
    return map;
  }, [menus]);
  const childrenOf = useMemo(() => {
    const map = {};
    menus.forEach((m) => {
      if (!m.parent) return;
      if (!map[m.parent]) map[m.parent] = [];
      map[m.parent].push(m.key);
    });
    return map;
  }, [menus]);
  const descendantsOf = (key) => {
    const out = [];
    for (const child of (childrenOf[key] || [])) {
      out.push(child, ...descendantsOf(child));
    }
    return out;
  };
  const depthOf = (key) => {
    let d = 0;
    let p = parentOf[key];
    while (p) {
      d += 1;
      p = parentOf[p];
    }
    return d;
  };

  const dirty = useMemo(() => {
    return visibleRoles.some((r) => {
      if (r.role === "admin" || isDirectorFullAccessRole(r.role)) return false;
      const a = draft[r.role] || new Set();
      const b = initial[r.role] || new Set();
      if (a.size !== b.size) return true;
      for (const k of a) if (!b.has(k)) return true;
      return false;
    });
  }, [visibleRoles, draft, initial]);

  const hasNav = (slug, key) => (draft[slug] || new Set()).has(key);

  const toggle = (roleDoc, key) => {
    if (!canManagePerms || roleDoc.role === "admin" || isDirectorFullAccessRole(roleDoc.role)) return;
    const current = new Set(draft[roleDoc.role] || []);
    const turningOff = current.has(key);
    if (turningOff && key === "nav.settings") {
      const roleHasPermManage = (roleDoc.permissions || []).includes("permissions.manage") || roleDoc.role === currentUserRole;
      const isSelf = roleDoc.role === currentUserRole;
      if (roleHasPermManage || isSelf) {
        const ok = window.confirm(
          isSelf
            ? "Anda akan menyembunyikan menu Pengaturan untuk role Anda sendiri. Lanjutkan?"
            : `Role "${roleDoc.label}" dapat mengelola hak akses. Menyembunyikan Pengaturan akan mempersulit pengelolaan permission. Lanjutkan?`
        );
        if (!ok) return;
      }
    }
    if (turningOff) {
      current.delete(key);
      descendantsOf(key).forEach((child) => current.delete(child));
    } else {
      current.add(key);
      let p = parentOf[key];
      while (p) {
        current.add(p);
        p = parentOf[p];
      }
    }
    setDraft((prev) => ({ ...prev, [roleDoc.role]: current }));
  };

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const changed = visibleRoles.filter((r) => {
        if (r.role === "admin" || isDirectorFullAccessRole(r.role)) return false;
        const a = draft[r.role] || new Set();
        const b = initial[r.role] || new Set();
        if (a.size !== b.size) return true;
        for (const k of a) if (!b.has(k)) return true;
        return false;
      });
      for (const r of changed) {
        const nonNav = (r.permissions || []).filter((p) => !String(p).startsWith("nav."));
        const nav = [...(draft[r.role] || [])];
        // Sidebar-only save: omit confirm_package_keys (never activates legacy package grants).
        await api.adminUpdateRolePermissions(r.role, [...nonNav, ...nav, "nav.configured"]);
      }
      setInitial(draft);
      notify("Akses menu disimpan");
      if (changed.some((r) => r.role === currentUserRole)) await onRefreshMe?.();
      await onSaved?.();
    } catch (e) {
      setErr(e.response?.data?.detail?.message || e.response?.data?.detail || "Gagal menyimpan akses menu");
    } finally {
      setBusy(false);
    }
  };

  if (!visibleRoles.length) return null;

  return (
    <div className="nav-access" data-testid="nav-access-matrix">
      <div className="nav-access-head">
        <div>
          <h3><PanelLeft size={16} /> Akses Menu / Sidebar</h3>
          <p className="hint">Centang role mana yang boleh melihat masing-masing menu. Parent tanpa child yang tampil tidak akan muncul di sidebar.</p>
        </div>
        {canManagePerms && (
          <button className="primary sm" onClick={save} disabled={busy || !dirty} data-testid="nav-access-save">
            <Save size={13} /> Simpan akses menu
          </button>
        )}
      </div>
      {err && <div className="error" data-testid="nav-access-error">{typeof err === "object" ? err.message : err}</div>}
      <div className="nav-access-wrap">
        <table className="nav-access-table">
          <thead>
            <tr>
              <th scope="col">Menu</th>
              {visibleRoles.map((r) => (
                <th key={r.role} scope="col" data-testid={`nav-access-role-${r.role}`}>
                  {r.label || r.role}
                  {r.role === "admin" && <Lock size={10} />}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {menus.map((m) => {
              const depth = depthOf(m.key);
              const rowClass = depth >= 2 ? "nav-access-grandchild" : (depth === 1 ? "nav-access-child" : "");
              return (
              <tr key={m.key} data-testid={`nav-access-row-${m.key}`}>
                <th scope="row" className={rowClass}>{m.label}</th>
                {visibleRoles.map((r) => {
                  const director = isDirectorFullAccessRole(r.role);
                  const locked = r.role === "admin" || director || !canManagePerms;
                  const checked = r.role === "admin" || director ? true : hasNav(r.role, m.key);
                  return (
                    <td key={r.role}>
                      <label className={"nav-access-cell" + (checked ? " on" : "") + (locked ? " locked" : "")}>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={locked}
                          onChange={() => toggle(r, m.key)}
                          data-testid={`nav-access-${r.role}-${m.key}`}
                        />
                        <span>{checked ? "✓" : "–"}</span>
                      </label>
                    </td>
                  );
                })}
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------------- Roles tab ---------------- */
function RolesTab({ permissionCatalog, canManagePerms, notify }) {
  const { user, refresh } = useAuth();
  const [roles, setRoles] = useState([]);
  const [selected, setSelected] = useState(null);
  const [permsDraft, setPermsDraft] = useState(new Set());
  const [initial, setInitial] = useState(new Set());
  const [labelDraft, setLabelDraft] = useState("");
  const [descDraft, setDescDraft] = useState("");
  const [metaInitial, setMetaInitial] = useState({ label: "", description: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [permQuery, setPermQuery] = useState("");
  const [permStateFilter, setPermStateFilter] = useState("all");
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ role: "", label: "", description: "" });
  const [confirmPackageOptIn, setConfirmPackageOptIn] = useState(false);
  const [confirmReviewKeys, setConfirmReviewKeys] = useState([]);

  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const resetPackageConfirmUi = () => {
    setConfirmPackageOptIn(false);
    setConfirmReviewKeys([]);
  };

  const load = useCallback(async (keepSelected = true) => {
    setBusy(true);
    try {
      const d = await api.adminListRoles();
      setRoles(d.roles || []);
      const current = selectedRef.current;
      const target = keepSelected && current ? d.roles.find((r) => r.role === current) : null;
      if (target) {
        setPermsDraft(expandLoadingDraftForEdit(target.permissions));
        setInitial(expandLoadingDraftForEdit(target.permissions));
        setLabelDraft(target.label || "");
        setDescDraft(target.description || "");
        setMetaInitial({ label: target.label || "", description: target.description || "" });
        resetPackageConfirmUi();
      } else if (d.roles?.length) {
        const first = d.roles.find((r) => r.role !== "admin" && !isDirectorFullAccessRole(r.role)) || d.roles.find((r) => r.role !== "admin") || d.roles[0];
        setSelected(first.role);
        setPermsDraft(expandLoadingDraftForEdit(first.permissions));
        setInitial(expandLoadingDraftForEdit(first.permissions));
        setLabelDraft(first.label || "");
        setDescDraft(first.description || "");
        setMetaInitial({ label: first.label || "", description: first.description || "" });
        resetPackageConfirmUi();
      }
    } catch (e) { setErr(e.response?.data?.detail || "Gagal memuat"); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { load(false); }, [load]);

  const selectRole = (r) => {
    setSelected(r.role);
    setPermsDraft(expandLoadingDraftForEdit(r.permissions));
    setInitial(expandLoadingDraftForEdit(r.permissions));
    setLabelDraft(r.label || "");
    setDescDraft(r.description || "");
    setMetaInitial({ label: r.label || "", description: r.description || "" });
    setPermQuery("");
    setPermStateFilter("all");
    setErr("");
    resetPackageConfirmUi();
  };

  // Keep reviewed confirm keys ⊆ current draft grants (stale keys cannot be sent).
  useEffect(() => {
    setConfirmReviewKeys((prev) => pruneConfirmReviewKeys(prev, permsDraft));
  }, [permsDraft]);

  const togglePerm = (key) => {
    setPermsDraft((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
        if (isPackagePermissionKey(key)) {
          setConfirmReviewKeys((rev) => rev.filter((k) => k !== key));
        }
      } else {
        next.add(key);
        // Re-check does not restore prior confirm — must review again.
      }
      return next;
    });
  };

  const toggleCategoryKeys = (keys, enable) => {
    setPermsDraft((prev) => {
      const next = new Set(prev);
      const touchedPkg = [];
      for (const key of keys || []) {
        if (enable) next.add(key);
        else {
          next.delete(key);
          if (isPackagePermissionKey(key)) touchedPkg.push(key);
        }
      }
      if (!enable && touchedPkg.length) {
        const drop = new Set(touchedPkg);
        setConfirmReviewKeys((rev) => rev.filter((k) => !drop.has(k)));
      }
      return next;
    });
  };

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      if (isDirectorFullAccessRole(selected)) {
        setErr("Permission role Direktur tidak dapat diubah (akses penuh sistem)");
        return;
      }
      const optInRows = addedOptInPermissions(permissionCatalog, permsDraft, initial);
      if (optInRows.length && !window.confirm(
        `Aktifkan ${optInRows.length} izin khusus berikut?\n\n${optInRows.map((p) => `${p.label} (${p.key})`).join("\n")}`
      )) return;
      const confirmOptInKeys = optInRows.map((p) => p.key);
      // Save label/description if changed and non-admin
      if (selected !== "admin" && (labelDraft !== metaInitial.label || descDraft !== metaInitial.description)) {
        await api.adminUpdateRole(selected, { label: labelDraft, description: descDraft });
      }
      if (selected !== "admin") {
        const roleDoc = roles.find((r) => r.role === selected);
        const permissions = roleBusinessSavePayload(permsDraft, roleDoc?.permissions);
        const confirmKeys = confirmPackageKeysForSave(permissions, confirmReviewKeys, {
          optIn: confirmPackageOptIn,
        });
        if (confirmPackageOptIn) {
          const preview = confirmKeys || [];
          const ok = window.confirm(
            preview.length
              ? `Konfirmasi aktivasi ${preview.length} izin package production?\n\n${preview.join("\n")}\n\nStamp hanya berlaku untuk himpunan ini.`
              : "Konfirmasi package tanpa key? Stamp package untuk grant saat ini akan dikosongkan."
          );
          if (!ok) return;
          // UI list and request payload must be identical.
          setConfirmReviewKeys(preview);
          await api.adminUpdateRolePermissions(
            selected,
            { ...rolePermissionsPutBody(permissions, preview, roleDoc?.permissions || []), confirm_opt_in_keys: confirmOptInKeys },
          );
        } else {
          // Business-only delta: backend CAS preserves unrelated grants and nav.*
          // from concurrent edits, rejecting stale role snapshots with HTTP 409.
          const baseline = new Set(initial);
          const current = new Set(permsDraft);
          const add = [...current].filter((k) => !baseline.has(k) && !k.startsWith("nav."));
          const remove = [...baseline].filter((k) => !current.has(k) && !k.startsWith("nav."));
          if (add.length || remove.length) {
            await api.adminPatchRoleBusinessPermissions(selected, {
              expected_permissions: roleDoc?.permissions || [],
              add,
              remove,
              confirm_opt_in_keys: confirmOptInKeys,
            });
          }
        }
      }
      setInitial(new Set(permsDraft));
      setMetaInitial({ label: labelDraft, description: descDraft });
      // Refresh own session so sidebar/API.me reflect the new effective set immediately.
      if (selected === user?.role) {
        await refresh();
        notify("Perubahan role disimpan. Sesi Anda sudah disegarkan.");
      } else {
        notify(
          "Perubahan role disimpan. Pengguna dengan role ini perlu refresh halaman atau login ulang agar hak akses baru berlaku.",
        );
      }
      await load(true);
    } catch (e) {
      // Keep draft + confirm UI; do not notify success or reload-away the draft.
      setErr(e.response?.data?.detail?.message || e.response?.data?.detail || "Gagal menyimpan");
    }
    finally { setBusy(false); }
  };

  const doCreateRole = async () => {
    setErr("");
    try {
      await api.adminCreateRole({ role: createForm.role.trim().toLowerCase(), label: createForm.label.trim() || createForm.role.trim(), description: createForm.description.trim(), permissions: [] });
      setShowCreate(false);
      setCreateForm({ role: "", label: "", description: "" });
      setSelected(createForm.role.trim().toLowerCase());
      notify("Role baru ditambahkan");
      await load(true);
    } catch (e) { setErr(e.response?.data?.detail?.message || e.response?.data?.detail || "Gagal membuat role"); }
  };

  const doToggleActive = async (r) => {
    try {
      if (r.status === "active") {
        if (!window.confirm(`Nonaktifkan role "${r.label}"? User dengan role ini akan kehilangan permission default sampai role diaktifkan kembali.`)) return;
        await api.adminDeactivateRole(r.role);
        notify("Role dinonaktifkan");
      } else {
        await api.adminActivateRole(r.role);
        notify("Role diaktifkan");
      }
      await load(true);
    } catch (e) { setErr(e.response?.data?.detail?.message || e.response?.data?.detail || "Gagal"); }
  };

  const doDelete = async (r) => {
    try {
      if (!window.confirm(`Hapus permanen role "${r.label}"? Tindakan ini tidak dapat dibatalkan.`)) return;
      await api.adminDeleteRole(r.role);
      notify(`Role "${r.label}" dihapus`);
      setSelected(null);
      await load(false);
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "object" ? d.message : (d || "Gagal menghapus role"));
    }
  };

  const currentRole = roles.find((r) => r.role === selected);
  const isAdminRole = selected === "admin";
  const isDirectorRole = isDirectorFullAccessRole(selected);

  return (
    <div className="settings-body" data-testid="roles-tab">
      {currentRole?.is_customized && !(currentRole.permissions || []).some((key) => String(key).startsWith("spk.")) && (
        <div className="banner" data-testid="spk-role-gap" style={{marginBottom:12}}>
          Role ini disimpan tanpa izin SPK. Modul SPK tidak muncul sampai admin mengaktifkan izin secara manual. Sistem tidak menambahkan izin SPK secara otomatis.
        </div>
      )}
      <div className="roles-layout">
        <aside className="role-sidebar" data-testid="role-sidebar">
          <div className="role-sidebar-title" style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
            <span>Pilih role</span>
            {canManagePerms && (
              <button className="ghost sm" onClick={() => setShowCreate(true)} data-testid="role-add-button" title="Tambah role baru">
                <Plus size={12}/> Tambah
              </button>
            )}
          </div>
          {roles.map((r) => {
            const active = selected === r.role;
            const built = permissionCatalog ? Object.values(permissionCatalog.modules || {}).flat().length : 0;
            const director = isDirectorFullAccessRole(r.role);
            return (
              <button
                key={r.role}
                type="button"
                className={"role-card" + (active ? " active" : "") + (r.status === "inactive" ? " role-inactive" : "")}
                onClick={() => selectRole(r)}
                data-testid={`role-pick-${r.role}`}
              >
                <div className="role-card-top">
                  <span className="role-card-name">{r.label}</span>
                  {r.status === "inactive" && <span className="pill sm" data-testid={`role-status-${r.role}`}>Nonaktif</span>}
                  {r.is_customized && r.status === "active" && !director && <span className="pill sm">disesuaikan</span>}
                  {r.role === "admin" && <span className="pill sm pill-lock"><Lock size={10}/> Terkunci</span>}
                  {director && <span className="pill sm pill-lock" data-testid="role-director-badge"><Lock size={10}/> Akses penuh</span>}
                  {!r.is_builtin && <span className="pill sm" data-testid={`role-custom-${r.role}`}>custom</span>}
                </div>
                <div className="role-card-meta">
                  {director
                    ? "Akses penuh sistem"
                    : <>{r.permissions.length} / {built || r.permissions.length} hak akses · <span data-testid={`role-user-count-${r.role}`}>{r.user_count} user</span></>}
                </div>
              </button>
            );
          })}
        </aside>

        <RolePermissionWorkspace
          currentRole={currentRole}
          selected={selected}
          isAdminRole={isAdminRole}
          isDirectorRole={isDirectorRole}
          canManagePerms={canManagePerms}
          permissionCatalog={permissionCatalog}
          permsDraft={permsDraft}
          initialPerms={initial}
          confirmedPackageKeys={currentRole?.package_production_confirmed_keys || []}
          confirmPackageOptIn={confirmPackageOptIn}
          onConfirmPackageOptInChange={setConfirmPackageOptIn}
          confirmReviewKeys={confirmReviewKeys}
          onConfirmReviewKeysChange={setConfirmReviewKeys}
          labelDraft={labelDraft}
          descDraft={descDraft}
          onLabelChange={setLabelDraft}
          onDescChange={setDescDraft}
          onTogglePerm={togglePerm}
          onToggleCategoryKeys={toggleCategoryKeys}
          permQuery={permQuery}
          onPermQueryChange={setPermQuery}
          stateFilter={permStateFilter}
          onStateFilterChange={setPermStateFilter}
          metaDirty={labelDraft !== metaInitial.label || descDraft !== metaInitial.description}
          busy={busy}
          err={err}
          onSave={save}
          onDiscard={() => {
            setPermsDraft(new Set(initial));
            setLabelDraft(metaInitial.label);
            setDescDraft(metaInitial.description);
            resetPackageConfirmUi();
          }}
          onToggleActive={doToggleActive}
          onDelete={doDelete}
        />
      </div>

      {showCreate && (
        <div className="modal" role="dialog" onClick={() => setShowCreate(false)} data-testid="role-create-modal">
          <div className="modal-inner" style={{maxWidth:520}} onClick={(e)=>e.stopPropagation()}>
            <div className="modal-head"><h3>Tambah Role Baru</h3><button className="icon-button" onClick={() => setShowCreate(false)}><X size={18}/></button></div>
            <div className="emp-form">
              <div className="emp-grid">
                <label>Slug (huruf kecil, unik)
                  <input value={createForm.role} onChange={(e) => setCreateForm({...createForm, role: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_")})} placeholder="contoh: supervisor_qc" data-testid="role-create-slug"/>
                </label>
                <label>Nama Tampilan
                  <input value={createForm.label} onChange={(e) => setCreateForm({...createForm, label: e.target.value})} placeholder="Supervisor QC" data-testid="role-create-label"/>
                </label>
                <label className="emp-notes">Deskripsi
                  <textarea rows={2} value={createForm.description} onChange={(e) => setCreateForm({...createForm, description: e.target.value})} placeholder="Deskripsi role (opsional)" data-testid="role-create-description"/>
                </label>
              </div>
              {err && <div className="error">{typeof err === "object" ? err.message : err}</div>}
              <div className="emp-actions">
                <button className="ghost" onClick={() => setShowCreate(false)}>Batal</button>
                <button className="primary" onClick={doCreateRole} disabled={!createForm.role.trim() || !createForm.label.trim()} data-testid="role-create-submit"><Plus size={14}/> Buat Role</button>
              </div>
              <p className="muted small">Role baru dibuat kosong. Atur permission setelah role dibuat.</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- Audit tab ---------------- */
function AuditTab() {
  const [logs, setLogs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [actionFilter, setActionFilter] = useState("all");
  const [q, setQ] = useState("");

  const load = async () => {
    setBusy(true);
    try {
      const d = await api.adminAuditLogs({ limit: 300 });
      setLogs(d.logs || []);
    } catch (e) { setErr(e.response?.data?.detail || "Gagal memuat"); }
    finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []);

  const actions = useMemo(() => {
    const s = new Set(logs.map((l) => l.action));
    return ["all", ...Array.from(s).sort()];
  }, [logs]);

  const visible = useMemo(() => logs.filter((l) => {
    if (actionFilter !== "all" && l.action !== actionFilter) return false;
    if (!q) return true;
    const t = q.toLowerCase();
    return (l.actor_email || "").toLowerCase().includes(t)
      || (l.target_email || "").toLowerCase().includes(t)
      || (l.note || "").toLowerCase().includes(t);
  }), [logs, actionFilter, q]);

  const detailOf = (l) => {
    if (l.note) return l.note;
    const before = l.before || {};
    const after = l.after || {};
    if (Object.keys(after).length) {
      const keys = Object.keys(after).filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]));
      if (keys.length) return keys.map((k) => `${k}: ${JSON.stringify(after[k])}`).join(" · ");
    }
    return "-";
  };

  return (
    <div className="settings-body" data-testid="audit-tab">
      <div className="emp-toolbar">
        <div className="search-inline">
          <Search size={14}/>
          <input placeholder="Cari pelaku, target, atau catatan" value={q} onChange={(e) => setQ(e.target.value)} data-testid="audit-search"/>
        </div>
        <div className="emp-filter">
          <Filter size={13}/>
          <select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)} data-testid="audit-filter-action">
            {actions.map((a) => <option key={a} value={a}>{a === "all" ? "Semua aksi" : (ACTION_LABELS[a] || a)}</option>)}
          </select>
        </div>
        <span className="muted small">{visible.length} entri</span>
        <button className="icon-button" onClick={load} data-testid="audit-refresh"><RefreshCw size={16} className={busy ? "spin" : ""}/></button>
      </div>

      {err && <div className="error">{err}</div>}

      <div className="audit-table" data-testid="audit-list">
        <div className="audit-thead">
          <span>Waktu</span><span>Pelaku</span><span>Target</span><span>Aksi</span><span>Detail</span>
        </div>
        {visible.map((l) => (
          <div key={l.id} className="audit-trow" data-testid={`audit-row-${l.id}`}>
            <span className="mono small">{formatDate(l.at)}</span>
            <span>
              <strong>{l.actor_name || l.actor_email}</strong>
              <span className="muted small mono"> · {ROLE_LABELS[l.actor_role] || l.actor_role}</span>
            </span>
            <span className="mono small">{l.target_email || "-"}</span>
            <span><span className="pill sm">{ACTION_LABELS[l.action] || l.action}</span></span>
            <span className="muted small ellipsis" title={detailOf(l)}>{detailOf(l)}</span>
          </div>
        ))}
        {!visible.length && !busy && <div className="empty-state">Belum ada audit log.</div>}
      </div>
    </div>
  );
}

/* ---------------- Root ---------------- */
function CustomerApprovalSettingsTab({ notify, canEdit }) {
  const [state, setState] = useState(null);
  const [choices, setChoices] = useState([1, 2, 4, 6, 12, 24]);
  const [customHours, setCustomHours] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.customerApprovalSettings().then((d) => {
      setState(d.settings);
      if (d.expiry_choices) setChoices(d.expiry_choices);
    }).catch((e) => setErr(e?.response?.data?.detail || "Gagal memuat settings"));
  }, []);

  if (!state) {
    return (
      <div data-testid="ca-settings-tab" className="settings-card">
        <p className="muted">{err || "Memuat..."}</p>
      </div>
    );
  }

  const isPreset = choices.includes(state.expiry_hours);
  const set = (k, v) => setState((s) => ({ ...s, [k]: v }));

  const save = async () => {
    setSaving(true);
    setErr("");
    try {
      let payload = { ...state };
      if (!isPreset) {
        const n = parseInt(customHours, 10);
        if (Number.isFinite(n) && n >= 1) payload.expiry_hours = n;
      }
      const r = await api.updateCustomerApprovalSettings(payload);
      setState(r.settings);
      notify?.("Pengaturan Customer Approval disimpan");
    } catch (e) {
      setErr(e?.response?.data?.detail || "Gagal menyimpan");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="ca-settings-tab" style={{ display: "grid", gap: 16, maxWidth: 620 }}>
      <div className="settings-card" style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10, background: "var(--surface)" }}>
        <h3 style={{ marginTop: 0 }}>Masa Berlaku Link Approval</h3>
        <p className="muted small" style={{ marginTop: 0 }}>Default {choices.includes(4) ? 4 : choices[0]} jam. Diukur dalam jam sejak link dibuat.</p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }} data-testid="ca-expiry-choices">
          {choices.map((h) => (
            <button
              key={h}
              type="button"
              className={state.expiry_hours === h && isPreset ? "primary sm" : "ghost sm"}
              onClick={() => { set("expiry_hours", h); setCustomHours(""); }}
              disabled={!canEdit || saving}
              data-testid={`ca-expiry-choice-${h}`}
            >
              {h} jam
            </button>
          ))}
          <button
            type="button"
            className={!isPreset ? "primary sm" : "ghost sm"}
            onClick={() => { setCustomHours(String(state.expiry_hours)); }}
            disabled={!canEdit || saving}
            data-testid="ca-expiry-custom-toggle"
          >
            Custom
          </button>
          {!isPreset && (
            <input
              type="number"
              min="1"
              max="168"
              placeholder="jumlah jam"
              value={customHours}
              onChange={(e) => setCustomHours(e.target.value)}
              onBlur={() => { const n = parseInt(customHours, 10); if (n >= 1) set("expiry_hours", n); }}
              style={{ width: 120 }}
              disabled={!canEdit || saving}
              data-testid="ca-expiry-custom-input"
            />
          )}
        </div>
      </div>

      <div className="settings-card" style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10, background: "var(--surface)" }}>
        <h3 style={{ marginTop: 0 }}>Aksi Customer</h3>
        {[
          ["allow_approve", "Izinkan Approve"],
          ["allow_reject", "Izinkan Reject"],
          ["signature_enabled", "Aktifkan Tanda Tangan"],
          ["signature_required_approve", "Tanda tangan wajib saat Approve"],
          ["signature_required_reject", "Tanda tangan wajib saat Reject"],
          ["reject_reason_required", "Alasan wajib saat Reject"],
        ].map(([k, label]) => (
          <label key={k} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }} data-testid={`ca-toggle-${k}`}>
            <input
              type="checkbox"
              checked={!!state[k]}
              onChange={(e) => set(k, e.target.checked)}
              disabled={!canEdit || saving}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>

      {err && <p style={{ color: "#8f1f1f" }} data-testid="ca-settings-error">{err}</p>}

      {canEdit && (
        <div>
          <button className="primary" onClick={save} disabled={saving} data-testid="ca-settings-save">
            <Save size={14} /> Simpan
          </button>
        </div>
      )}
      {!canEdit && (
        <p className="muted small">Anda hanya bisa melihat. Butuh permission <code>customer_approval.settings</code> untuk mengubah.</p>
      )}
    </div>
  );
}

/* ---------------- Root ---------------- */

const CRM_PROGRESS_EVENT_META = [
  { key: "pra_po_returned", module: "Pra PO", label: "Pra PO dikembalikan", category: "Perlu Tindakan", recipients: "CRM creator saja" },
  { key: "sales_order_verified", module: "Pesanan Penjualan", label: "Pesanan Penjualan disetujui", category: "Informasi", recipients: "CRM creator saja" },
  { key: "sales_invoice_created", module: "Faktur Penjualan", label: "Faktur Penjualan dibuat", category: "Informasi", recipients: "CRM creator saja" },
  { key: "crm_progress_customer_design_approved", module: "Approval Design Customer", label: "Customer menyetujui design (progres CRM)", category: "Informasi", recipients: "CRM creator saja" },
  { key: "payment_approved", module: "Approval Pembayaran", label: "Pembayaran disetujui", category: "Informasi", recipients: "CRM creator saja" },
  { key: "payment_bypassed", module: "Approval Pembayaran", label: "Pembayaran di-bypass", category: "Informasi", recipients: "CRM creator saja" },
  { key: "crm_progress_surat_jalan_created", module: "Pengiriman Pesanan", label: "Surat Jalan dibuat (progres CRM)", category: "Informasi", recipients: "CRM creator saja" },
];

function NotificationsSettingsTab({ notify, canEdit }) {
  const { hasPerm } = useAuth();
  const canSearchUsers = hasPerm("users.view") || hasPerm("users.manage");
  const [state, setState] = useState(null);
  const [progress, setProgress] = useState(null);
  const [saving, setSaving] = useState(false);
  const [savingProgress, setSavingProgress] = useState(false);
  const [err, setErr] = useState("");
  const [memberQuery, setMemberQuery] = useState("");
  const [directory, setDirectory] = useState([]);
  const [directoryErr, setDirectoryErr] = useState("");
  const [directoryLoading, setDirectoryLoading] = useState(false);

  useEffect(() => {
    api.customerDesignApprovalNotificationSettings()
      .then((d) => {
        const s = d.settings || {};
        setState({
          ...s,
          additional_recipient_user_ids: Array.isArray(s.additional_recipient_user_ids)
            ? s.additional_recipient_user_ids
            : [],
        });
      })
      .catch((e) => setErr(e?.response?.data?.detail || "Gagal memuat pengaturan notifikasi"));
    api.crmProgressNotificationSettings()
      .then((d) => setProgress(d.settings || { events: {} }))
      .catch(() => setProgress({ events: {} }));
  }, []);

  useEffect(() => {
    if (!canSearchUsers) return undefined;
    let cancelled = false;
    setDirectoryLoading(true);
    setDirectoryErr("");
    api.adminListUsers()
      .then((d) => {
        if (cancelled) return;
        setDirectory(Array.isArray(d?.users) ? d.users : []);
      })
      .catch((e) => {
        if (cancelled) return;
        setDirectory([]);
        setDirectoryErr(e?.response?.data?.detail || "Gagal memuat daftar anggota");
      })
      .finally(() => {
        if (!cancelled) setDirectoryLoading(false);
      });
    return () => { cancelled = true; };
  }, [canSearchUsers]);

  const selectedIds = Array.isArray(state?.additional_recipient_user_ids)
    ? state.additional_recipient_user_ids
    : [];
  const selectedKey = selectedIds.join("|");

  const userById = useMemo(() => {
    const map = new Map();
    for (const u of directory) {
      const id = String(u?.id || "").trim();
      if (id) map.set(id, u);
    }
    return map;
  }, [directory]);

  const searchHits = useMemo(() => {
    const selectedSet = new Set(selectedIds.map((id) => String(id)));
    const t = memberQuery.trim().toLowerCase();
    if (!t) return [];
    return directory
      .filter((u) => {
        const id = String(u?.id || "").trim();
        if (!id || selectedSet.has(id)) return false;
        if (u?.status === "inactive") return false;
        return (
          (u.name || "").toLowerCase().includes(t) ||
          (u.email || "").toLowerCase().includes(t) ||
          (u.employee_id || "").toLowerCase().includes(t) ||
          (u.role || "").toLowerCase().includes(t)
        );
      })
      .slice(0, 8);
    // selectedKey tracks selectedIds membership without depending on array identity
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [directory, memberQuery, selectedKey]);

  if (!state) {
    return (
      <div data-testid="notifications-settings-tab" className="settings-card">
        <p className="muted">{err || "Memuat..."}</p>
      </div>
    );
  }

  const set = (k, v) => setState((s) => ({ ...s, [k]: v }));

  const addMember = (user) => {
    const id = String(user?.id || "").trim();
    if (!id) return;
    setState((s) => {
      const cur = Array.isArray(s.additional_recipient_user_ids) ? s.additional_recipient_user_ids : [];
      if (cur.includes(id)) return s;
      return { ...s, additional_recipient_user_ids: [...cur, id] };
    });
    setMemberQuery("");
  };

  const removeMember = (id) => {
    const uid = String(id || "").trim();
    setState((s) => ({
      ...s,
      additional_recipient_user_ids: (Array.isArray(s.additional_recipient_user_ids) ? s.additional_recipient_user_ids : [])
        .filter((x) => x !== uid),
    }));
  };

  const save = async () => {
    setSaving(true);
    setErr("");
    try {
      const payload = {
        enabled: !!state.enabled,
        notify_designer: !!state.notify_designer,
        notify_crm: !!state.notify_crm,
        additional_recipient_user_ids: selectedIds.map((id) => String(id)),
      };
      const r = await api.updateCustomerDesignApprovalNotificationSettings(payload);
      const next = r.settings || {};
      setState({
        ...next,
        additional_recipient_user_ids: Array.isArray(next.additional_recipient_user_ids)
          ? next.additional_recipient_user_ids
          : [],
      });
      notify?.("Pengaturan notifikasi disimpan");
    } catch (e) {
      setErr(e?.response?.data?.detail || "Gagal menyimpan");
    } finally {
      setSaving(false);
    }
  };

  const roleLabel = (slug) => ROLE_LABELS[slug] || slug || "";

  return (
    <div data-testid="notifications-settings-tab" style={{ display: "grid", gap: 16, maxWidth: 620 }}>
      <div className="settings-card" style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10, background: "var(--surface)" }}>
        <h3 style={{ marginTop: 0 }}>Approval Design Customer</h3>
        <p className="muted small" style={{ marginTop: 0 }}>
          Notifikasi ke Designer, CRM, dan anggota tambahan ketika customer menyetujui atau menolak design.
        </p>
        <label style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }} data-testid="notif-toggle-enabled">
          <input
            type="checkbox"
            checked={!!state.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
            disabled={!canEdit || saving}
          />
          <span>Aktif</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }} data-testid="notif-toggle-designer">
          <input
            type="checkbox"
            checked={!!state.notify_designer}
            onChange={(e) => set("notify_designer", e.target.checked)}
            disabled={!canEdit || saving}
          />
          <span>Designer</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }} data-testid="notif-toggle-crm">
          <input
            type="checkbox"
            checked={!!state.notify_crm}
            onChange={(e) => set("notify_crm", e.target.checked)}
            disabled={!canEdit || saving}
          />
          <span>CRM</span>
        </label>

        <div
          data-testid="notif-additional-recipients"
          style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)" }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Anggota Penerima Tambahan</div>
          <p className="muted small" style={{ marginTop: 0, marginBottom: 8 }}>
            Ditambahkan ke penerima role (Designer/CRM). Satu user hanya menerima satu notifikasi.
          </p>
          {canEdit && canSearchUsers && (
            <div style={{ position: "relative" }} data-testid="notif-member-picker">
              <div className="search-inline" style={{ marginBottom: 6 }}>
                <Search size={14} />
                <input
                  placeholder="Cari anggota..."
                  value={memberQuery}
                  onChange={(e) => setMemberQuery(e.target.value)}
                  disabled={saving || directoryLoading}
                  data-testid="notif-member-search"
                  autoComplete="off"
                />
              </div>
              {directoryErr && (
                <p className="muted small" style={{ color: "#8f1f1f" }} data-testid="notif-member-search-error">
                  {typeof directoryErr === "string" ? directoryErr : "Gagal memuat daftar anggota"}
                </p>
              )}
              {memberQuery.trim() && !directoryErr && (
                <ul
                  data-testid="notif-member-search-results"
                  style={{
                    listStyle: "none",
                    margin: "0 0 8px",
                    padding: 0,
                    border: "1px solid var(--line)",
                    borderRadius: 8,
                    maxHeight: 180,
                    overflow: "auto",
                    background: "var(--surface)",
                  }}
                >
                  {searchHits.length === 0 ? (
                    <li className="muted small" style={{ padding: "8px 10px" }}>Tidak ada anggota</li>
                  ) : (
                    searchHits.map((u) => (
                      <li key={u.id}>
                        <button
                          type="button"
                          className="ghost"
                          style={{
                            width: "100%",
                            textAlign: "left",
                            padding: "8px 10px",
                            borderRadius: 0,
                            borderBottom: "1px solid var(--line)",
                          }}
                          onClick={() => addMember(u)}
                          data-testid={`notif-member-option-${u.id}`}
                        >
                          <strong>{u.name || u.email || u.id}</strong>
                          <span className="muted small" style={{ marginLeft: 8 }}>
                            {roleLabel(u.role)}
                          </span>
                        </button>
                      </li>
                    ))
                  )}
                </ul>
              )}
            </div>
          )}
          {canEdit && !canSearchUsers && (
            <p className="muted small" data-testid="notif-member-need-users-view">
              Butuh permission <code>users.view</code> untuk mencari anggota.
            </p>
          )}
          <div
            data-testid="notif-member-selected"
            style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}
          >
            {selectedIds.length === 0 ? (
              <span className="muted small">Belum ada anggota tambahan.</span>
            ) : (
              selectedIds.map((id) => {
                const u = userById.get(String(id));
                const label = u?.name || u?.email || id;
                const role = u?.role ? roleLabel(u.role) : "";
                const inactive = u?.status === "inactive";
                return (
                  <span
                    key={id}
                    data-testid={`notif-member-chip-${id}`}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "4px 8px",
                      border: "1px solid var(--line)",
                      borderRadius: 8,
                      background: "var(--bg)",
                      fontSize: 13,
                    }}
                  >
                    <span>
                      {label}
                      {role ? ` · ${role}` : ""}
                      {inactive ? " · tidak aktif" : ""}
                    </span>
                    {canEdit && (
                      <button
                        type="button"
                        className="ghost sm"
                        aria-label={`Hapus ${label}`}
                        onClick={() => removeMember(id)}
                        disabled={saving}
                        data-testid={`notif-member-remove-${id}`}
                        style={{ padding: "0 4px", minWidth: 0, lineHeight: 1 }}
                      >
                        <X size={14} />
                      </button>
                    )}
                  </span>
                );
              })
            )}
          </div>
        </div>
      </div>
      {err && <p style={{ color: "#8f1f1f" }} data-testid="notifications-settings-error">{err}</p>}
      {canEdit && (
        <div>
          <button className="primary" onClick={save} disabled={saving} data-testid="notifications-settings-save">
            <Save size={14} /> Simpan
          </button>
        </div>
      )}
      {!canEdit && (
        <p className="muted small">Anda hanya bisa melihat. Butuh permission <code>customer_approval.settings</code> atau <code>notifications.settings.manage</code> untuk mengubah.</p>
      )}

      <div
        className="settings-card"
        data-testid="notifications-crm-progress-settings"
        style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10, background: "var(--surface)" }}
      >
        <h3 style={{ marginTop: 0 }}>Progres CRM (creator saja)</h3>
        <p className="muted small" style={{ marginTop: 0 }}>
          Penerima wajib akun CRM pembuat Pra PO / Pesanan Penjualan. Tidak ada opsi blast ke semua CRM.
          Saluran: notifikasi aplikasi. Kategori Informasi kecuali Pra PO dikembalikan (Perlu Tindakan).
        </p>
        <p className="muted small">
          Approval Design Customer role-blast (Designer/CRM/anggota) tetap di kartu di atas — tidak diganti diam-diam.
        </p>
        {(progress?.events ? CRM_PROGRESS_EVENT_META : []).map((ev) => (
          <label
            key={ev.key}
            style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 10px", padding: "8px 0", borderTop: "1px solid var(--line)" }}
            data-testid={`notif-progress-${ev.key}`}
          >
            <input
              type="checkbox"
              checked={!!progress?.events?.[ev.key]}
              disabled={!canEdit || savingProgress || !progress}
              onChange={(e) => setProgress((p) => ({
                ...p,
                events: { ...(p?.events || {}), [ev.key]: e.target.checked },
              }))}
            />
            <span>
              <strong>{ev.module}</strong> — {ev.label}
              <div className="muted small">{ev.category} · {ev.recipients} · Pemicu: transaksi terkait</div>
            </span>
          </label>
        ))}
        {canEdit && (
          <div style={{ marginTop: 12 }}>
            <button
              className="primary"
              disabled={savingProgress || !progress}
              data-testid="notifications-crm-progress-save"
              onClick={async () => {
                setSavingProgress(true);
                setErr("");
                try {
                  const payload = { events: {} };
                  for (const ev of CRM_PROGRESS_EVENT_META) {
                    payload.events[ev.key] = !!(progress?.events?.[ev.key]);
                  }
                  const r = await api.updateCrmProgressNotificationSettings(payload);
                  setProgress(r.settings || progress);
                  notify?.("Pengaturan progres CRM disimpan");
                } catch (e) {
                  setErr(e?.response?.data?.detail || "Gagal menyimpan progres CRM");
                } finally {
                  setSavingProgress(false);
                }
              }}
            >
              <Save size={14} /> Simpan Progres CRM
            </button>
          </div>
        )}
      </div>

      <div className="settings-card" style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10 }}>
        <h3 style={{ marginTop: 0 }}>Notifikasi operasional existing</h3>
        <p className="muted small" style={{ margin: 0 }}>
          Loading qty mismatch (CRM cabang), Surat Jalan ke Helper/Driver/Kepala Produksi, SPK, Urgent PO, dan Permintaan Produk
          tetap berjalan sesuai emitter masing-masing. SJ progress CRM di atas bersifat tambahan dan tidak mengganti notifikasi operasional SJ.
        </p>
      </div>
    </div>
  );
}

function DeliveryDeadlineSettingsTab({ notify, canEdit }) {
  const [state, setState] = useState(null);
  const [choices, setChoices] = useState([1, 3, 5]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.deliveryDeadlineSettings().then((d) => {
      setState(d.settings);
      if (d.allowed_lead_time_days) setChoices(d.allowed_lead_time_days);
    }).catch((e) => setErr(e?.response?.data?.detail || "Gagal memuat settings"));
  }, []);

  if (!state) return <div data-testid="dd-settings-tab" style={{maxWidth:560}}><p className="muted">{err || "Memuat..."}</p></div>;

  const save = async (days) => {
    setSaving(true); setErr("");
    try {
      const r = await api.updateDeliveryDeadlineSettings({ lead_time_days: days });
      setState(r.settings);
      notify?.(`Estimasi PO Normal diatur ke ${days} hari kerja`);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Gagal menyimpan");
    } finally { setSaving(false); }
  };

  return (
    <div data-testid="dd-settings-tab" style={{ display: "grid", gap: 12, maxWidth: 560 }}>
      <div className="settings-card" style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10, background: "var(--surface)" }}>
        <h3 style={{ marginTop: 0 }}>Estimasi Pengiriman PO Normal</h3>
        <p className="muted small" style={{ marginTop: 0 }} data-testid="dd-unit-copy">
          Durasi dihitung sebagai <b>hari kerja</b> setelah kedua approval Design dan Payment selesai.
          Senin–Sabtu dihitung. Minggu dan tanggal pada Kalender Libur Operasional tidak dihitung.
          Pilihan: 1, 3, atau 5 hari kerja. Default 3. Tanggal yang sudah dikunci pada PO tidak berubah
          saat pengaturan ini diganti. Tanggal manual tetap lebih dulu.
        </p>
        <div style={{ display: "flex", gap: 8, marginTop: 8 }} data-testid="dd-choices">
          {choices.map((d) => (
            <button
              key={d}
              type="button"
              className={state.lead_time_days === d ? "primary sm" : "ghost sm"}
              onClick={() => save(d)}
              disabled={!canEdit || saving}
              data-testid={`dd-choice-${d}`}
            >
              {d} hari kerja
            </button>
          ))}
        </div>
        {state.legacy?.unit === "calendar_days" && (
          <p className="muted small" data-testid="dd-legacy-note" style={{ marginTop: 8 }}>
            Pengaturan lama: {state.legacy.lead_time_days} hari kalender. Dipakai hanya untuk PO tanpa stempel SLA. Tidak mengubah PO yang sudah dikunci.
          </p>
        )}
        {err && <p style={{ color: "#8f1f1f", marginTop: 8 }} data-testid="dd-settings-error">{err}</p>}
        {!canEdit && <p className="muted small" style={{ marginTop: 8 }}>Butuh permission <code>delivery.deadline.settings</code>.</p>}
      </div>
    </div>
  );
}

function OperatorTargetSettingsTab({ notify, canEdit }) {
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");

  const load = () => {
    setBusy(true);
    api.analyticsOperatorTargets().then((d) => {
      setRows((d.targets || []).map((t) => ({ ...t, qty_per_day: t.qty_per_day || 0 })));
      setNote(d.limitation || "");
    }).catch((e) => setErr(e?.response?.data?.detail || "Gagal memuat target")).finally(() => setBusy(false));
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true); setErr("");
    try {
      await api.updateOperatorTargets({
        targets: rows.map((r) => ({
          operator_id: r.operator_id,
          email: r.email,
          qty_per_day: Number(r.qty_per_day) || 0,
        })),
      });
      notify?.("Target output operator disimpan");
      load();
    } catch (e) {
      setErr(e?.response?.data?.detail || "Gagal menyimpan target");
    } finally { setSaving(false); }
  };

  return (
    <div data-testid="op-target-settings-tab" className="op-target-settings">
      <div className="op-target-card">
        <h3>Target Output Operator</h3>
        <p className="muted small">Target qty cetak per hari aktif, keyed by operator ID (bukan nama). Achievement analytics membandingkan avg/hari aktif vs target ini. Tidak ada kalender kerja — target mingguan tidak dikarang.</p>
        {note && <p className="muted small">{note}</p>}
        {busy && <p className="muted">Memuat…</p>}
        <div className="op-target-table-wrap">
          <table className="op-target-table">
            <thead>
              <tr>
                <th className="op-target-name">Operator</th>
                <th className="op-target-id">ID</th>
                <th className="num op-target-qty">Target qty / hari</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.operator_id || r.email}>
                  <td className="op-target-name"><strong>{r.name}</strong><div className="muted small">{r.email}</div></td>
                  <td className="mono muted small op-target-id">{r.operator_id}</td>
                  <td className="num op-target-qty">
                    <input
                      type="number"
                      min="0"
                      className="op-target-qty-input"
                      value={r.qty_per_day}
                      disabled={!canEdit}
                      onChange={(e) => {
                        const next = [...rows];
                        next[i] = { ...next[i], qty_per_day: e.target.value };
                        setRows(next);
                      }}
                      data-testid={`op-target-${r.email}`}
                    />
                  </td>
                </tr>
              ))}
              {!rows.length && !busy && <tr><td colSpan={3} className="empty-state">Tidak ada operator aktif.</td></tr>}
            </tbody>
          </table>
        </div>
        <div style={{ marginTop: 12 }}>
          <button className="primary sm" onClick={save} disabled={!canEdit || saving} data-testid="op-target-save">Simpan Target</button>
        </div>
        {err && <p style={{ color: "#8f1f1f", marginTop: 8 }}>{err}</p>}
        {!canEdit && <p className="muted small" style={{ marginTop: 8 }}>Butuh permission <code>schedule.edit</code>.</p>}
      </div>
    </div>
  );
}

function AccurateCreatorSettingsTab({ notify, canEdit }) {
  const [mappings, setMappings] = useState([]);
  const [scan, setScan] = useState(null);
  const [scanLimit, setScanLimit] = useState(100);
  const [nameDraft, setNameDraft] = useState({});
  const [scanning, setScanning] = useState(false);
  const [verifying, setVerifying] = useState("");
  const [err, setErr] = useState("");
  const [manualId, setManualId] = useState("");
  const [manualName, setManualName] = useState("");

  const loadMappings = async () => {
    const d = await api.accurateCreatorSettings();
    setMappings((d.settings?.mappings || []).map((m) => ({ ...m })));
  };

  useEffect(() => {
    loadMappings().catch((e) => setErr(e?.response?.data?.detail || "Gagal memuat mapping"));
  }, []);

  const scanNow = async () => {
    setScanning(true); setErr("");
    try {
      const r = await api.scanAccurateCreators(scanLimit);
      setScan(r);
      notify?.(`Scan ${r.scanned || 0} SO Accurate selesai`);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Scan gagal");
    } finally { setScanning(false); }
  };

  const verifyOne = async (cid, name) => {
    const display = String(name || "").trim();
    if (!display) { setErr("Nama pembuat PO wajib diisi"); return; }
    setVerifying(String(cid)); setErr("");
    try {
      const r = await api.verifyAccurateCreator({
        accurate_created_by_id: String(cid),
        display_name: display,
      });
      setMappings(r.settings?.mappings || []);
      setScan((prev) => {
        if (!prev) return prev;
        const moved = (prev.unknown || []).find((x) => String(x.accurate_created_by_id) === String(cid));
        return {
          ...prev,
          unknown: (prev.unknown || []).filter((x) => String(x.accurate_created_by_id) !== String(cid)),
          verified: [
            {
              ...(moved || { accurate_created_by_id: String(cid), so_count: 0, sample_po_numbers: [] }),
              display_name: display,
              verified: true,
              status: "verified",
            },
            ...(prev.verified || []).filter((x) => String(x.accurate_created_by_id) !== String(cid)),
          ],
        };
      });
      setNameDraft((d) => {
        const next = { ...d };
        delete next[String(cid)];
        return next;
      });
      setManualId("");
      setManualName("");
      notify?.("Pembuat PO diverifikasi");
    } catch (e) {
      setErr(e?.response?.data?.detail || "Gagal verify");
    } finally { setVerifying(""); }
  };

  const unknown = scan?.unknown || [];
  const verified = scan?.verified || mappings.filter((m) => m.verified !== false);

  return (
    <div data-testid="accurate-creator-settings-tab" style={{ display: "grid", gap: 16, maxWidth: 960 }}>
      <div className="settings-card" style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10, background: "var(--surface)" }}>
        <h3 style={{ marginTop: 0 }}>Pembuat PO Accurate</h3>
        <p className="muted small" style={{ marginTop: 0 }}>
          Accurate hanya mengirim ID user (<code>createdBy</code>). Scan SO terbaru untuk mengumpulkan ID unik.
          Hasil scan tidak otomatis verified — isi nama dari UI Accurate lalu klik Verify.
        </p>
        {canEdit && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select
              value={scanLimit}
              onChange={(e) => setScanLimit(Number(e.target.value))}
              disabled={scanning}
              data-testid="accurate-creator-scan-limit"
            >
              <option value={100}>100 SO terbaru</option>
              <option value={250}>250 SO terbaru</option>
              <option value={500}>500 SO terbaru</option>
            </select>
            <button type="button" className="primary sm" onClick={scanNow} disabled={scanning} data-testid="accurate-creator-scan">
              {scanning ? "Memindai…" : "Scan Pembuat PO dari Accurate"}
            </button>
          </div>
        )}
        {scan && (
          <p className="muted small" style={{ marginBottom: 0 }}>
            Dipindai {scan.scanned || 0} SO, gagal {scan.failed || 0}, limit {scan.limit}. Mapping tidak ditulis oleh scan.
          </p>
        )}
        {err && <p style={{ color: "#8f1f1f", marginBottom: 0 }}>{err}</p>}
        {!canEdit && <p className="muted small" style={{ marginBottom: 0 }}>Butuh permission <code>users.manage</code> atau <code>accurate.import</code>.</p>}
      </div>

      <div className="settings-card" style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10, background: "var(--surface)" }} data-testid="accurate-creator-unknown">
        <h3 style={{ marginTop: 0 }}>Belum dikenal</h3>
        {!scan && <p className="muted small">Klik scan untuk menemukan createdBy dari Sales Order terbaru.</p>}
        {scan && unknown.length === 0 && <p className="muted small">Tidak ada createdBy baru pada scan ini.</p>}
        {unknown.length > 0 && (
          <div className="table-wrap">
            <table className="ship-table">
              <thead>
                <tr>
                  <th>createdBy</th>
                  <th>Jumlah PO</th>
                  <th>Contoh PO</th>
                  <th>SO terakhir</th>
                  <th>Nama</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {unknown.map((row) => (
                  <tr key={row.accurate_created_by_id} data-testid={`accurate-creator-unknown-${row.accurate_created_by_id}`}>
                    <td><code>{row.accurate_created_by_id}</code></td>
                    <td>{row.so_count}</td>
                    <td className="muted small">{(row.sample_po_numbers || []).join(", ") || "—"}</td>
                    <td className="muted small">{row.last_so_date || "—"}</td>
                    <td>
                      <input
                        value={nameDraft[row.accurate_created_by_id] || ""}
                        onChange={(e) => setNameDraft((d) => ({ ...d, [row.accurate_created_by_id]: e.target.value }))}
                        placeholder="Nama pembuat PO"
                        disabled={!canEdit || verifying === String(row.accurate_created_by_id)}
                        data-testid={`accurate-creator-unknown-name-${row.accurate_created_by_id}`}
                      />
                    </td>
                    <td>
                      {canEdit && (
                        <button
                          type="button"
                          className="primary sm"
                          disabled={verifying === String(row.accurate_created_by_id)}
                          onClick={() => verifyOne(row.accurate_created_by_id, nameDraft[row.accurate_created_by_id])}
                          data-testid={`accurate-creator-verify-${row.accurate_created_by_id}`}
                        >
                          Verify
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="settings-card" style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 10, background: "var(--surface)" }} data-testid="accurate-creator-verified">
        <h3 style={{ marginTop: 0 }}>Sudah diverifikasi</h3>
        {verified.length === 0 && <p className="muted small">Belum ada mapping verified.</p>}
        {verified.length > 0 && (
          <div className="table-wrap">
            <table className="ship-table">
              <thead>
                <tr>
                  <th>createdBy</th>
                  <th>Jumlah PO</th>
                  <th>Contoh PO</th>
                  <th>SO terakhir</th>
                  <th>Nama</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {verified.map((row) => (
                  <tr key={row.accurate_created_by_id} data-testid={`accurate-creator-verified-${row.accurate_created_by_id}`}>
                    <td><code>{row.accurate_created_by_id}</code></td>
                    <td>{row.so_count ?? "—"}</td>
                    <td className="muted small">{(row.sample_po_numbers || []).join(", ") || "—"}</td>
                    <td className="muted small">{row.last_so_date || "—"}</td>
                    <td>{row.display_name}</td>
                    <td>Verified</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {canEdit && (
          <div style={{ display: "grid", gridTemplateColumns: "160px 1fr auto", gap: 8, marginTop: 12, alignItems: "center" }}>
            <input value={manualId} onChange={(e) => setManualId(e.target.value)} placeholder="createdBy ID" data-testid="accurate-creator-manual-id"/>
            <input value={manualName} onChange={(e) => setManualName(e.target.value)} placeholder="Nama pembuat PO" data-testid="accurate-creator-manual-name"/>
            <button type="button" className="ghost sm" onClick={() => verifyOne(manualId, manualName)} data-testid="accurate-creator-save">Verify</button>
          </div>
        )}
      </div>
    </div>
  );
}

function WorkPanelSettingsTab({ notify, canEdit, canManagePerms, canListRoles }) {
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = async () => {
    setErr("");
    try {
      const d = await api.workPanelSettings();
      setRows((d.settings || d).divisions || []);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat");
    }
  };
  useEffect(() => { load(); }, []);

  const patchRow = (id, field, value) => {
    setRows((cur) => cur.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
  };

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.updateWorkPanelSettings({
        divisions: rows.map((r) => ({
          id: r.id,
          active: r.active,
          input_label: r.input_label,
          submit_perm: r.submit_perm,
          target_source: r.target_source,
          helper_text: r.helper_text,
          sort_order: r.sort_order,
          unit: r.unit,
        })),
      });
      setRows((d.settings || d).divisions || []);
      notify?.("Form Pekerjaan Divisi disimpan");
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal simpan");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card" data-testid="work-panel-settings">
      {canListRoles ? (
        <FormAccessMatrix notify={notify} canEdit={!!canManagePerms} />
      ) : (
        <p className="muted small">Matriks akses form per role membutuhkan permission users.view.</p>
      )}
      <h3 style={{ marginTop: 0 }}>Tampilan Form per Divisi</h3>
      <p className="muted small">
        Mengatur tampilan Work Panel per divisi. Tidak dapat menonaktifkan konfirmasi, append-only, audit actor, RBAC, atau validasi server.
      </p>
      {err && <div className="error">{err}</div>}
      <div className="table-wrap">
        <table className="ship-table work-settings-table" data-testid="work-panel-settings-table">
          <thead>
            <tr>
              <th>Divisi</th>
              <th>Aktif</th>
              <th>Permission</th>
              <th>Label Input</th>
              <th>Sumber Target</th>
              <th>Unit</th>
              <th>Konfirmasi</th>
              <th>Mode Entry</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} data-testid={`work-settings-row-${r.id}`}>
                <td>
                  <strong>{r.label}</strong>
                  {!r.implemented && <div className="muted small">Belum diimplementasi</div>}
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={!!r.active}
                    disabled={!canEdit}
                    onChange={(e) => patchRow(r.id, "active", e.target.checked)}
                    data-testid={`work-settings-active-${r.id}`}
                  />
                </td>
                <td>
                  <select
                    value={r.submit_perm}
                    disabled={!canEdit}
                    onChange={(e) => patchRow(r.id, "submit_perm", e.target.value)}
                    data-testid={`work-settings-perm-${r.id}`}
                  >
                    {(r.allowed_submit_perms || [r.submit_perm]).map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </td>
                <td>
                  {r.input_kind === "evidence" ? (
                    <span className="muted small" data-testid={`work-settings-label-${r.id}`}>
                      Aksi / dokumen existing
                    </span>
                  ) : (
                    <input
                      className={canEdit ? "manual-input" : ""}
                      value={r.input_label || ""}
                      disabled={!canEdit}
                      onChange={(e) => patchRow(r.id, "input_label", e.target.value)}
                      data-testid={`work-settings-label-${r.id}`}
                    />
                  )}
                </td>
                <td>
                  <select
                    value={r.target_source}
                    disabled={!canEdit}
                    onChange={(e) => patchRow(r.id, "target_source", e.target.value)}
                    data-testid={`work-settings-target-${r.id}`}
                  >
                    {(r.allowed_target_sources || [r.target_source]).map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </td>
                <td className="muted small">{r.input_kind === "evidence" ? "Dokumen" : (r.unit === "item" ? "Satuan item" : r.unit)}</td>
                <td className="muted small">Wajib ON</td>
                <td className="muted small">Append-only</td>
                <td className="muted small">{r.implemented ? "Aktif" : "Config saja"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canEdit && (
        <div style={{ marginTop: 12 }}>
          <button type="button" className="primary" onClick={save} disabled={busy} data-testid="work-settings-save">
            {busy ? "Menyimpan…" : "Simpan"}
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------------- Sidebar access tab ---------------- */
function SidebarAccessTab() {
  const [roles, setRoles] = useState([]);
  const [error, setError] = useState("");
  useEffect(() => {
    api.adminListRoles().then((d) => setRoles(d.roles || []))
      .catch(() => setError("Gagal memuat pratinjau menu"));
  }, []);
  const { columns, rows } = useMemo(() => roleSidebarPreview(roles), [roles]);
  return (
    <div data-testid="settings-sidebar-access">
      <div className="nav-access-head">
        <div>
          <h3><PanelLeft size={16} /> Pratinjau Sidebar per Role</h3>
          <p className="hint">Menu mengikuti izin efektif role. Override akun dan status Super Admin dapat berbeda. Ubah izin melalui Role &amp; Hak Akses.</p>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {columns.some((role) => !role.known) && <div className="error">Pratinjau izin efektif belum tersedia. Muat ulang halaman.</div>}
      <div className="nav-access-wrap">
        <table className="nav-access-table">
          <thead><tr><th scope="col">Menu</th>{columns.map((role) => <th scope="col" key={role.role}>{role.label || role.role}</th>)}</tr></thead>
          <tbody>
            {rows.map((menu) => (
              <tr key={menu.to}>
                <th scope="row">{menu.group} · {menu.label}</th>
                {columns.map((role) => (
                  <td key={role.role} title={!role.known ? "Izin efektif belum tersedia." : role.paths.has(menu.to) ? "Tampil pada sidebar role ini." : "Tidak tampil pada sidebar role ini."}>
                    <span>{!role.known ? "?" : role.paths.has(menu.to) ? "✓" : "–"}</span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!error && columns.length > 0 && rows.length === 0 && <p className="hint">Tidak ada menu yang tersedia pada izin efektif role.</p>}
      </div>
    </div>
  );
}

/* ---------------- Operator Wage settings ---------------- */
function OperatorWageTab(props) {
  return <OperatorWageSettings {...props} />;
}

/** Category navigation cards — UI only; section ids must stay stable for deep links / tests. */
const SETTINGS_NAV_ITEMS = [
  { id: "employees", label: "Karyawan", description: "Kelola data user", Icon: Users, tone: "cyan", testid: "settings-tab-employees" },
  { id: "roles", label: "Role & Hak Akses", description: "Kelola permission", Icon: Shield, tone: "indigo", testid: "settings-tab-roles" },
  { id: "sidebar-access", label: "Akses Menu Sidebar", description: "Atur menu pengguna", Icon: PanelLeft, tone: "sky", testid: "settings-tab-sidebar-access" },
  { id: "operator-wage", label: "Sistem Upah Operator", description: "Atur skema upah", Icon: Coins, tone: "emerald", testid: "settings-tab-operator-wage" },
  { id: "hpp", label: "Pengaturan HPP", description: "Biaya & harga pokok", Icon: Scale, tone: "amber", testid: "settings-tab-hpp" },
  { id: "physical-stock", label: "Stok Fisik", description: "Otomatisasi & trigger stok", Icon: Package, tone: "emerald", testid: "settings-tab-physical-stock" },
  { id: "design-cetakan", label: "Design Cetakan", description: "Letterhead PDF penjualan", Icon: Printer, tone: "rose", testid: "settings-tab-design-cetakan" },
  { id: "sales-accounting", label: "Akuntansi Penjualan", description: "Pemetaan akun penjualan", Icon: BookOpen, tone: "violet", testid: "settings-tab-sales-accounting" },
  { id: "purchase-accounting", label: "Akuntansi Pembelian", description: "Pemetaan akun pembelian", Icon: CreditCard, tone: "rose", testid: "settings-tab-purchase-accounting" },
  { id: "payroll-accounting", label: "Akuntansi Payroll", description: "Pemetaan akun penggajian", Icon: Wallet, tone: "indigo", testid: "settings-tab-payroll-accounting" },
  { id: "pricing-catalog", label: "Harga & Katalog", description: "Harga customer & katalog publik", Icon: Tags, tone: "emerald", testid: "settings-tab-pricing-catalog" },
  { id: "customer-approval", label: "Customer Approval", description: "Alur persetujuan", Icon: ClipboardCheck, tone: "teal", testid: "settings-tab-customer-approval" },
  { id: "notifications", label: "Notifikasi", description: "Notifikasi operasional", Icon: Bell, tone: "cyan", testid: "settings-tab-notifications" },
  { id: "delivery-deadline", label: "Estimasi Pengiriman", description: "1, 3, atau 5 hari kerja", Icon: Truck, tone: "rose", testid: "settings-tab-delivery-deadline" },
  { id: "operational-calendar", label: "Kalender Libur Operasional", description: "Libur manual, Minggu tetap libur", Icon: CalendarClock, tone: "rose", testid: "settings-tab-operational-calendar" },
  { id: "operator-target", label: "Target Output Operator", description: "Target harian/mingguan", Icon: TargetIcon, tone: "cyan", testid: "settings-tab-operator-target" },
  { id: "work-panel", label: "Form Pekerjaan Divisi", description: "Form & akses divisi", Icon: ClipboardList, tone: "violet", testid: "settings-tab-work-panel" },
  { id: "employee-admin", label: "Administrasi Karyawan", description: "Data administrasi", Icon: IdCard, tone: "fuchsia", testid: "settings-tab-employee-admin" },
  { id: "hr-payroll", label: "HR & Payroll", description: "Payroll & kehadiran", Icon: Wallet, tone: "indigo", testid: "settings-tab-hr-payroll" },
  { id: "sales-activity", label: "Sales Activity & Ownership", description: "Aktivitas & ownership", Icon: ClipboardList, tone: "teal", testid: "settings-tab-sales-activity" },
  { id: "route-optimizer", label: "Optimasi Rute", description: "Pengaturan rute", Icon: Route, tone: "blue", testid: "settings-tab-route-optimizer" },
  { id: "accurate-creators", label: "Pembuat PO Accurate", description: "Mapping createdBy", Icon: Users, tone: "sky", testid: "settings-tab-accurate-creators" },
  { id: "sales-user-mapping", label: "Mapping Sales Accurate", description: "Label Accurate → users.id", Icon: Users, tone: "teal", testid: "settings-tab-sales-user-mapping" },
  { id: "sales-master", label: "Master Sales", description: "Katalog Sales operasional multi-cabang", Icon: Users, tone: "teal", testid: "settings-tab-sales-master" },
  { id: "crm-daily-report-options", label: "Dropdown Laporan Harian CRM", description: "Alasan / komplain / kendala", Icon: ClipboardList, tone: "cyan", testid: "settings-tab-crm-daily-report-options" },
  { id: "audit", label: "Audit Log", description: "Riwayat perubahan", Icon: ScrollText, tone: "slate", testid: "settings-tab-audit" },
];

function SettingsCategoryNav({ items, activeId, onSelect }) {
  return (
    <section className="settings-cat-nav" data-testid="settings-category-nav" aria-labelledby="settings-cat-nav-heading">
      <h2 id="settings-cat-nav-heading" className="settings-cat-nav-heading">Pengaturan</h2>
      <div className="settings-cat-grid" role="tablist" aria-label="Kategori pengaturan">
        {items.map((item) => {
          const Icon = item.Icon;
          const isActive = activeId === item.id;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-current={isActive ? "page" : undefined}
              className={`settings-cat-card${isActive ? " is-active" : ""}`}
              data-tone={item.tone}
              data-testid={item.testid}
              onClick={() => onSelect(item.id)}
            >
              <span className="settings-cat-card-icon" aria-hidden="true">
                <Icon size={22} strokeWidth={1.75} />
              </span>
              <span className="settings-cat-card-body">
                <span className="settings-cat-card-title-row">
                  <span className="settings-cat-card-title">{item.label}</span>
                  {isActive && <span className="settings-cat-card-badge">AKTIF</span>}
                </span>
                {item.description ? (
                  <span className="settings-cat-card-desc">{item.description}</span>
                ) : null}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

/* ---------------- Root ---------------- */
export default function Settings() {
  const { user, hasPerm } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const canViewUsers = hasPerm("users.view") || hasPerm("users.manage");
  const canManageUsers = hasPerm("users.manage");
  const canManageRoles = hasPerm("roles.manage");
  const canManagePerms = hasPerm("permissions.manage");
  const canViewAudit = hasPerm("audit.view");
  const canViewCaSettings = hasPerm("customer_approval.view") || hasPerm("customer_approval.settings")
    || hasPerm("notifications.settings.view") || hasPerm("notifications.settings.manage");
  const canEditCaSettings = hasPerm("customer_approval.settings") || hasPerm("notifications.settings.manage");
  const canViewDdSettings = hasPerm("delivery.deadline.settings") || hasPerm("delivery.deadline.edit") || hasPerm("dashboard.production.view");
  const canEditDdSettings = hasPerm("delivery.deadline.settings");
  const canViewOpTarget = hasPerm("schedule.edit") || hasPerm("users.manage");
  const canEditOpTarget = hasPerm("schedule.edit");
  const canViewWorkPanel = hasPerm("settings.work_panel.view") || hasPerm("settings.work_panel.manage") || hasPerm("permissions.manage");
  const canEditWorkPanel = hasPerm("settings.work_panel.manage");
  const canViewAccurateCreators = canViewUsers || hasPerm("accurate.import") || hasPerm("permissions.manage");
  const canEditAccurateCreators = canManageUsers || hasPerm("accurate.import") || hasPerm("permissions.manage");
  const canViewSalesUserMapping = hasPerm("settings.sales_user_mapping.view") || hasPerm("settings.sales_user_mapping.manage") || hasPerm("permissions.manage");
  const canEditSalesUserMapping = hasPerm("settings.sales_user_mapping.manage") || hasPerm("permissions.manage");
  const canViewSalesMaster = hasPerm("settings.sales_master.view") || hasPerm("settings.sales_master.manage") || hasPerm("permissions.manage");
  const canEditSalesMaster = hasPerm("settings.sales_master.manage") || hasPerm("permissions.manage");
  const canViewCrmDailyReportOptions = hasPerm("settings.crm_daily_report.view") || hasPerm("settings.crm_daily_report.manage") || hasPerm("permissions.manage");
  const canEditCrmDailyReportOptions = hasPerm("settings.crm_daily_report.manage") || hasPerm("permissions.manage");
  const canViewSidebarAccess = canManagePerms;
  const canViewWage = hasPerm("settings.operator_wage.view") || hasPerm("settings.operator_wage.manage") || hasPerm("reports.gaji_operator.view");
  const canEditWage = hasPerm("settings.operator_wage.manage");
  const canViewHpp = hasPerm("settings.hpp.view") || hasPerm("settings.hpp.manage") || hasPerm("permissions.manage");
  const canEditHpp = hasPerm("settings.hpp.manage");
  const canViewPhysicalStock = hasPerm("current_stock.view") || hasPerm("current_stock.update") || hasPerm("permissions.manage");
  const canEditPhysicalStock = hasPerm("current_stock.update") || hasPerm("permissions.manage");
  const canViewDesignCetakan = hasPerm("settings.design_cetakan.view") || hasPerm("settings.design_cetakan.manage") || hasPerm("permissions.manage");
  const canEditDesignCetakan = hasPerm("settings.design_cetakan.manage") || hasPerm("permissions.manage");
  const canViewSalesAccounting = hasPerm("settings.sales_accounting.view") || hasPerm("settings.sales_accounting.manage") || hasPerm("permissions.manage");
  const canEditSalesAccounting = hasPerm("settings.sales_accounting.manage");
  const canViewPurchaseAccounting = hasPerm("settings.purchase_accounting.view") || hasPerm("settings.purchase_accounting.manage") || hasPerm("permissions.manage");
  const canEditPurchaseAccounting = hasPerm("settings.purchase_accounting.manage");
  const canViewPayrollAccounting = hasPerm("payroll_accounting.view") || hasPerm("payroll_accounting.manage") || hasPerm("permissions.manage");
  const canEditPayrollAccounting = hasPerm("payroll_accounting.manage");
  const canViewCustomerPricing = hasPerm("settings.customer_pricing.view") || hasPerm("settings.customer_pricing.manage") || hasPerm("settings.customer_pricing.publish") || hasPerm("permissions.manage");
  const canEditCustomerPricing = hasPerm("settings.customer_pricing.manage");
  const canPublishCustomerPricing = hasPerm("settings.customer_pricing.publish");
  const canViewPublicCatalog = hasPerm("settings.public_catalog.view") || hasPerm("settings.public_catalog.manage") || hasPerm("permissions.manage");
  const canEditPublicCatalog = hasPerm("settings.public_catalog.manage");
  const canViewEmployeeAdmin = hasPerm("employee_admin.settings");
  const canEditEmployeeAdmin = hasPerm("employee_admin.settings");
  const canViewHrPayroll = hasPerm("payroll.settings");
  const canViewSalesActivity = hasPerm("settings.sales_activity.view") || hasPerm("settings.sales_activity.manage") || hasPerm("permissions.manage");
  const canEditSalesActivity = hasPerm("settings.sales_activity.manage");
  const canViewRouteOptimizer = hasPerm("settings.route_optimizer.view") || hasPerm("settings.route_optimizer.manage") || hasPerm("permissions.manage");
  const canEditRouteOptimizer = hasPerm("settings.route_optimizer.manage");

  const navVisibility = {
    employees: canViewUsers,
    roles: canManagePerms,
    "sidebar-access": canViewSidebarAccess,
    "operator-wage": canViewWage,
    hpp: canViewHpp,
    "physical-stock": canViewPhysicalStock,
    "design-cetakan": canViewDesignCetakan,
    "sales-accounting": canViewSalesAccounting,
    "purchase-accounting": canViewPurchaseAccounting,
    "payroll-accounting": canViewPayrollAccounting,
    "pricing-catalog": canViewCustomerPricing || canViewPublicCatalog,
    "customer-approval": canViewCaSettings,
    notifications: canViewCaSettings,
    "delivery-deadline": canViewDdSettings,
    "operational-calendar": hasPerm("settings.operational_calendar.manage"),
    "operator-target": canViewOpTarget,
    "work-panel": canViewWorkPanel,
    "employee-admin": canViewEmployeeAdmin,
    "hr-payroll": canViewHrPayroll,
    "sales-activity": canViewSalesActivity,
    "route-optimizer": canViewRouteOptimizer,
    "accurate-creators": canViewAccurateCreators,
    "sales-user-mapping": canViewSalesUserMapping,
    "sales-master": canViewSalesMaster,
    "crm-daily-report-options": canViewCrmDailyReportOptions,
    audit: canViewAudit,
  };

  const visibleNavItems = SETTINGS_NAV_ITEMS.filter((item) => navVisibility[item.id]);

  const urlTab = settingsTabFromPath(location.pathname, location.search);
  // Bare /settings keeps the existing hub. Child routes (/settings/:tab or ?tab=) show content only —
  // Pengaturan flyout is now the navigation, so the in-page card grid must not duplicate it.
  const showCategoryNav = location.pathname === "/settings" && !urlTab;
  const initialTab = urlTab || (canViewUsers ? "employees" : canManagePerms ? "roles" : canViewSidebarAccess ? "sidebar-access" : canViewWage ? "operator-wage" : canViewHpp ? "hpp" : canViewPhysicalStock ? "physical-stock" : canViewHrPayroll ? "hr-payroll" : canViewCaSettings ? "customer-approval" : canViewDdSettings ? "delivery-deadline" : canViewAudit ? "audit" : "employees");
  const [tab, setTab] = useState(initialTab);
  const activeNavItem = SETTINGS_NAV_ITEMS.find((item) => item.id === tab);
  const [catalog, setCatalog] = useState(null);
  const [toast, setToast] = useState("");

  useEffect(() => {
    const alias = settingsAliasFromPath(location.pathname);
    if (alias) {
      const params = new URLSearchParams(location.search);
      if (!params.get("section")) params.set("section", alias.section);
      const rest = params.toString();
      navigate(`/settings/${alias.tab}${rest ? `?${rest}` : ""}`, { replace: true });
      return;
    }
    const next = settingsTabFromPath(location.pathname, location.search);
    if (next) setTab(next);
  }, [location.pathname, location.search, navigate]);

  useEffect(() => {
    const q = searchParams.get("tab");
    if (location.pathname !== "/settings" || !q || !settingsTabFromPath("/settings", location.search)) return;
    const params = new URLSearchParams(location.search);
    params.delete("tab");
    // Legacy ?tab=customer-pricing|public-catalog → canonical pricing-catalog + section
    const alias = settingsAliasFromPath(`/settings/${q}`);
    if (alias) {
      if (!params.get("section")) params.set("section", alias.section);
      const rest = params.toString();
      navigate(`/settings/${alias.tab}${rest ? `?${rest}` : ""}`, { replace: true });
      return;
    }
    const rest = params.toString();
    navigate(`/settings/${q}${rest ? `?${rest}` : ""}`, { replace: true });
  }, [location.pathname, location.search, navigate, searchParams]);

  useEffect(() => {
    api.adminPermissionCatalog().then(setCatalog).catch(() => {});
  }, []);

  const selectTab = (id) => {
    setTab(id);
    if (location.pathname !== `/settings/${id}`) navigate(`/settings/${id}`);
  };

  const notify = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2800);
  };

  const designCetakanPage = !showCategoryNav && tab === "design-cetakan";
  const pricingCatalogPage = !showCategoryNav && tab === "pricing-catalog";
  const hideSharedSettingsHeading = designCetakanPage || pricingCatalogPage;

  return (
    <div data-testid="settings-page" className="settings-page">
      {!hideSharedSettingsHeading ? (
      <div className="page-heading">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
          <span className="page-heading-icon" aria-hidden="true"><SettingsIcon size={22} /></span>
          <div>
          {showCategoryNav ? (
            <>
              <p className="eyebrow">Pengaturan · Karyawan &amp; Hak Akses</p>
              <h1>Karyawan &amp; Hak Akses</h1>
              <p className="subhead">Kelola karyawan, atur role default, dan pantau audit log</p>
            </>
          ) : (
            <>
              <p className="eyebrow">Pengaturan</p>
              <h1>{activeNavItem?.label || "Pengaturan"}</h1>
            </>
          )}
          </div>
        </div>
      </div>
      ) : null}

      {showCategoryNav && (
        <InfoBanner testid="settings-intro">
          <span><b>Role</b> menentukan hak akses default seorang karyawan. Bila ada kekhususan (mis. helper A hanya boleh Persiapan, helper B hanya Packing), atur <b>Hak akses per karyawan</b> di tab Karyawan sebagai override.</span>
        </InfoBanner>
      )}

      {showCategoryNav && (
        <SettingsCategoryNav items={visibleNavItems} activeId={tab} onSelect={selectTab} />
      )}

      <div className="settings-active-panel" data-testid="settings-active-panel">
      {canViewUsers && !canManageUsers && tab === "employees" && (
        <div className="settings-info warn" data-testid="employees-readonly-note">
          <AlertCircle size={14}/><div>Mode read-only. Butuh <b>users.manage</b> untuk menambah/mengubah karyawan.</div>
        </div>
      )}

      {tab === "employees" && canViewUsers && (
        <EmployeesTab
          permissionCatalog={catalog}
          canManage={canManageUsers}
          canManageRoles={canManageRoles}
          canManagePerms={canManagePerms}
          currentUser={user}
          notify={notify}
        />
      )}
      {tab === "roles" && canManagePerms && <RolesTab permissionCatalog={catalog} canManagePerms={canManagePerms} notify={notify}/>}
      {tab === "sidebar-access" && canViewSidebarAccess && (
        <SidebarAccessTab permissionCatalog={catalog} canManagePerms={canManagePerms} notify={notify}/>
      )}
      {tab === "operator-wage" && canViewWage && <OperatorWageTab notify={notify} canEdit={canEditWage}/>}
      {tab === "hpp" && canViewHpp && <HppSettings notify={notify} canEdit={canEditHpp}/>}
      {tab === "physical-stock" && canViewPhysicalStock && (
        <PhysicalStockSettings notify={notify} canEdit={canEditPhysicalStock}/>
      )}
      {tab === "design-cetakan" && canViewDesignCetakan && (
        <DesignCetakanSettings notify={notify} canEdit={canEditDesignCetakan}/>
      )}
      {tab === "sales-accounting" && canViewSalesAccounting && (
        <SalesAccountingSettings notify={notify} canEdit={canEditSalesAccounting}/>
      )}
      {tab === "purchase-accounting" && canViewPurchaseAccounting && (
        <PurchaseAccountingSettings notify={notify} canEdit={canEditPurchaseAccounting}/>
      )}
      {tab === "payroll-accounting" && canViewPayrollAccounting && (
        <PayrollAccountingSettings notify={notify} canEdit={canEditPayrollAccounting}/>
      )}
      {tab === "pricing-catalog" && (canViewCustomerPricing || canViewPublicCatalog) && (
        <PricingCatalogSettings
          notify={notify}
          canViewPricing={canViewCustomerPricing}
          canEditPricing={canEditCustomerPricing}
          canPublishPricing={canPublishCustomerPricing}
          canViewCatalog={canViewPublicCatalog}
          canEditCatalog={canEditPublicCatalog}
        />
      )}
      {tab === "customer-approval" && canViewCaSettings && <CustomerApprovalSettingsTab notify={notify} canEdit={canEditCaSettings}/>}
      {tab === "notifications" && canViewCaSettings && <NotificationsSettingsTab notify={notify} canEdit={canEditCaSettings}/>}
      {tab === "delivery-deadline" && canViewDdSettings && <DeliveryDeadlineSettingsTab notify={notify} canEdit={canEditDdSettings}/>}
      {tab === "operational-calendar" && hasPerm("settings.operational_calendar.manage") && <OperationalHolidaySettings canManage />}
      {tab === "operational-calendar" && !hasPerm("settings.operational_calendar.manage") && <OperationalHolidayReadonly />}
      {tab === "operator-target" && canViewOpTarget && <OperatorTargetSettingsTab notify={notify} canEdit={canEditOpTarget}/>}
      {tab === "work-panel" && canViewWorkPanel && (
        <WorkPanelSettingsTab notify={notify} canEdit={canEditWorkPanel} canManagePerms={canManagePerms} canListRoles={canViewUsers}/>
      )}
      {tab === "employee-admin" && canViewEmployeeAdmin && (
        <EmployeeAdminSettings notify={notify} canEdit={canEditEmployeeAdmin}/>
      )}
      {tab === "sales-activity" && canViewSalesActivity && (
        <SalesActivitySettings notify={notify} canEdit={canEditSalesActivity}/>
      )}
      {tab === "route-optimizer" && canViewRouteOptimizer && (
        <RouteOptimizerSettings notify={notify} canEdit={canEditRouteOptimizer}/>
      )}
      {tab === "hr-payroll" && canViewHrPayroll && (
        <HrPayrollSettings notify={notify} canEdit={canViewHrPayroll}/>
      )}
      {tab === "accurate-creators" && canViewAccurateCreators && <AccurateCreatorSettingsTab notify={notify} canEdit={canEditAccurateCreators}/>}
      {tab === "sales-user-mapping" && canViewSalesUserMapping && (
        <SalesUserMappingSettings notify={notify} canEdit={canEditSalesUserMapping}/>
      )}
      {tab === "sales-master" && canViewSalesMaster && (
        <SalesMasterSettings notify={notify} canEdit={canEditSalesMaster}/>
      )}
      {tab === "crm-daily-report-options" && canViewCrmDailyReportOptions && (
        <CrmDailyReportOptionsSettings notify={notify} canEdit={canEditCrmDailyReportOptions}/>
      )}
      {tab === "audit" && canViewAudit && <AuditTab/>}
      </div>

      <Toast msg={toast}/>
    </div>
  );
}
