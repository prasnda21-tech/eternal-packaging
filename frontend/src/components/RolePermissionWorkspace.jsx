import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  Check,
  ChevronDown,
  Lock,
  MoreVertical,
  Power,
  PowerOff,
  Save,
  Search,
  Trash2,
  X,
} from "lucide-react";
import {
  businessPermissionModules,
  countRolePermissionDirty,
  countRolePermissionTotals,
  filterRolePermissionModules,
  isSensitivePermissionKey,
} from "../lib/settingsPermissionUi";
import {
  isPackagePermissionKey,
  packageKeysInGrant,
  packageKeysToActivate,
  unconfirmedPackageKeys,
} from "../lib/packagePermissions";

const DEFAULT_OPEN_COUNT = 2;

function moduleSlug(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "kategori";
}

export default function RolePermissionWorkspace({
  currentRole,
  selected,
  isAdminRole,
  isDirectorRole = false,
  canManagePerms,
  permissionCatalog,
  permsDraft,
  initialPerms,
  confirmedPackageKeys = [],
  confirmPackageOptIn = false,
  onConfirmPackageOptInChange,
  confirmReviewKeys = [],
  onConfirmReviewKeysChange,
  labelDraft,
  descDraft,
  onLabelChange,
  onDescChange,
  onTogglePerm,
  onToggleCategoryKeys,
  permQuery,
  onPermQueryChange,
  stateFilter,
  onStateFilterChange,
  metaDirty = false,
  busy = false,
  err = "",
  onSave,
  onDiscard,
  onToggleActive,
  onDelete,
}) {
  const allModules = useMemo(
    () => businessPermissionModules(permissionCatalog, ""),
    [permissionCatalog]
  );
  const visibleModules = useMemo(
    () => filterRolePermissionModules(allModules, { query: permQuery, stateFilter, draftSet: permsDraft }),
    [allModules, permQuery, stateFilter, permsDraft]
  );
  const totals = useMemo(
    () => countRolePermissionTotals(allModules, permsDraft),
    [allModules, permsDraft]
  );
  const permDirtyCount = useMemo(
    () => countRolePermissionDirty(permsDraft, initialPerms),
    [permsDraft, initialPerms]
  );
  const packageConfirmDirty = confirmPackageOptIn ? 1 : 0;
  const dirtyCount = permDirtyCount + (metaDirty ? 1 : 0) + packageConfirmDirty;
  const dirty = dirtyCount > 0;
  const filtering = Boolean(String(permQuery || "").trim()) || stateFilter !== "all";

  const [openMods, setOpenMods] = useState(() => new Set());
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const defaults = allModules.slice(0, DEFAULT_OPEN_COUNT).map(([mod]) => mod);
    setOpenMods(new Set(defaults));
    setMenuOpen(false);
  }, [selected, allModules]);

  useEffect(() => {
    if (!filtering) return;
    setOpenMods(new Set(visibleModules.map(([mod]) => mod)));
  }, [filtering, visibleModules]);

  const toggleMod = (mod) => {
    setOpenMods((prev) => {
      const next = new Set(prev);
      if (next.has(mod)) next.delete(mod);
      else next.add(mod);
      return next;
    });
  };

  const toggleCategoryAll = (mod, perms) => {
    const keys = perms.map((p) => p.key);
    const allOn = keys.every((k) => permsDraft.has(k));
    onToggleCategoryKeys(keys, !allOn);
  };

  const draftPackageKeys = useMemo(() => packageKeysInGrant(permsDraft), [permsDraft]);
  const confirmedSet = useMemo(
    () => new Set(packageKeysInGrant(confirmedPackageKeys)),
    [confirmedPackageKeys]
  );
  const pendingUnconfirmed = useMemo(
    () => unconfirmedPackageKeys(permsDraft, confirmedPackageKeys),
    [permsDraft, confirmedPackageKeys]
  );
  const activationPreview = useMemo(
    () => packageKeysToActivate(permsDraft, confirmReviewKeys),
    [permsDraft, confirmReviewKeys]
  );

  if (!currentRole) {
    return (
      <div className="role-detail" data-testid="role-detail">
        <p className="muted">Pilih role di sebelah kiri untuk melihat hak akses default-nya.</p>
      </div>
    );
  }

  const statusActive = currentRole.status !== "inactive";
  const lockedFullAccess = isAdminRole || isDirectorRole;
  const canEdit = canManagePerms && !lockedFullAccess;

  const toggleReviewKey = (key) => {
    if (!onConfirmReviewKeysChange || !isPackagePermissionKey(key)) return;
    const next = new Set(confirmReviewKeys || []);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onConfirmReviewKeysChange([...next].sort());
  };

  return (
    <div className="role-detail role-workspace" data-testid="role-detail">
      <div className="role-workspace-head" data-testid="role-workspace-head">
        <div className="role-workspace-head-row">
          <div className="role-workspace-identity">
            {lockedFullAccess ? (
              <h3 className="role-workspace-title" data-testid="role-detail-title">{currentRole.label}</h3>
            ) : (
              <input
                className="role-label-input"
                value={labelDraft}
                onChange={(e) => onLabelChange(e.target.value)}
                placeholder="Nama Role"
                disabled={!canManagePerms}
                data-testid="role-label-input"
              />
            )}
            <div className="role-workspace-meta">
              <span
                className={"role-status-pill" + (statusActive ? " is-active" : " is-inactive")}
                data-testid="role-workspace-status"
              >
                {statusActive ? "Aktif" : "Nonaktif"}
              </span>
              {lockedFullAccess && (
                <span className="pill sm pill-lock"><Lock size={10}/> Terkunci</span>
              )}
              <span className="role-perm-tally" data-testid="role-perm-count">
                <b>{isDirectorRole ? totals.total : totals.active}</b> / {totals.total} aktif
              </span>
              <span className="muted small role-workspace-slug">
                {isAdminRole
                  ? "Seluruh hak akses · tidak dapat diubah"
                  : isDirectorRole
                    ? "Akses penuh sistem · tidak dapat diubah"
                    : `${currentRole.role} · ${currentRole.is_builtin ? "built-in" : "custom"} · ${currentRole.user_count} user`}
              </span>
            </div>
          </div>
          <div className="role-workspace-tools">
            {canEdit && (
              <div className="role-overflow" data-testid="role-overflow-menu">
                <button
                  type="button"
                  className="ghost sm role-overflow-btn"
                  aria-expanded={menuOpen}
                  onClick={() => setMenuOpen((v) => !v)}
                  data-testid="role-overflow-toggle"
                  title="Aksi role"
                >
                  <MoreVertical size={14}/>
                </button>
                {menuOpen && (
                  <div className="role-overflow-panel" role="menu">
                    <button
                      type="button"
                      className="role-overflow-item danger"
                      onClick={() => { setMenuOpen(false); onToggleActive(currentRole); }}
                      data-testid="role-toggle-active"
                    >
                      {statusActive ? <><PowerOff size={12}/> Nonaktifkan Role</> : <><Power size={12}/> Aktifkan Role</>}
                    </button>
                    {!currentRole.is_builtin && (
                      <button
                        type="button"
                        className="role-overflow-item danger"
                        onClick={() => { setMenuOpen(false); onDelete(currentRole); }}
                        disabled={currentRole.user_count > 0}
                        title={currentRole.user_count > 0 ? "Role masih digunakan karyawan" : "Hapus permanen"}
                        data-testid="role-delete-button"
                      >
                        <Trash2 size={12}/> Hapus Role
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
            {canEdit && (
              <button
                className="primary sm"
                onClick={onSave}
                disabled={busy || !dirty}
                data-testid="role-save-permissions-header"
              >
                <Save size={13}/> Simpan Perubahan
              </button>
            )}
          </div>
        </div>
        {!lockedFullAccess && (
          <textarea
            className="role-desc-input"
            value={descDraft}
            onChange={(e) => onDescChange(e.target.value)}
            rows={2}
            placeholder="Deskripsi role (opsional)"
            disabled={!canManagePerms}
            data-testid="role-description-input"
          />
        )}
        {!lockedFullAccess && (
          <div className="role-workspace-controls">
            <div className="perm-search role-perm-search">
              <Search size={14}/>
              <input
                placeholder="Cari label, key, atau kategori..."
                value={permQuery}
                onChange={(e) => onPermQueryChange(e.target.value)}
                data-testid="role-perm-search"
              />
            </div>
            <div className="role-perm-filters" data-testid="role-perm-filters" role="tablist">
              {[
                ["all", "Semua"],
                ["active", "Aktif"],
                ["inactive", "Tidak Aktif"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  className={"role-perm-filter" + (stateFilter === id ? " on" : "")}
                  onClick={() => onStateFilterChange(id)}
                  data-testid={`role-perm-filter-${id}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {isAdminRole && (
        <div className="settings-info" data-testid="role-admin-lock">
          <span>Role <b>Admin</b> memiliki seluruh hak akses dan tidak dapat diubah. Buat role lain atau atur override per karyawan bila perlu.</span>
        </div>
      )}

      {isDirectorRole && (
        <div className="settings-info" data-testid="role-director-lock">
          <span>
            Role <b>Direktur</b> memiliki <b>Akses penuh sistem</b>. Permission tersimpan atau override deny lama
            tidak mengurangi akses efektif; dokumen tidak diubah dari editor ini.
          </span>
        </div>
      )}

      {err && <div className="error" data-testid="role-error">{typeof err === "object" ? err.message : err}</div>}

      {canEdit && draftPackageKeys.length > 0 && (
        <div className="settings-info" data-testid="role-package-confirm-panel" style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 8 }}>
            <strong>Package Pembelian / Persediaan / HPP (production)</strong>
            <p className="hint" style={{ margin: "4px 0 0" }}>
              Grant tersimpan belum otomatis aktif di production. Simpan biasa / sidebar / revoke tidak mengaktifkan
              grant lama. Centang konfirmasi eksplisit hanya untuk himpunan yang ditinjau di bawah.
            </p>
            <p className="muted small" data-testid="role-package-confirm-meta">
              Sudah dikonfirmasi: {confirmedSet.size} · Belum aktif: {pendingUnconfirmed.length}
              {currentRole.package_production_confirmed_by
                ? ` · terakhir oleh ${currentRole.package_production_confirmed_by}`
                : ""}
            </p>
          </div>
          <label className="role-package-confirm-optin" style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
            <input
              type="checkbox"
              checked={!!confirmPackageOptIn}
              onChange={(e) => {
                const on = e.target.checked;
                onConfirmPackageOptInChange?.(on);
                if (on && !(confirmReviewKeys || []).length) {
                  // Seed with already-confirmed keys still granted; new grants stay unchecked.
                  onConfirmReviewKeysChange?.(
                    packageKeysToActivate(permsDraft, confirmedPackageKeys),
                  );
                }
              }}
              data-testid="role-package-confirm-optin"
            />
            <span>Konfirmasi aktivasi package production untuk daftar yang ditinjau</span>
          </label>
          {confirmPackageOptIn && (
            <div data-testid="role-package-confirm-list" style={{ marginTop: 8 }}>
              <p className="hint">Izin yang akan diaktifkan jika Anda simpan sekarang:</p>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18, maxHeight: 160, overflow: "auto" }}>
                {draftPackageKeys.map((key) => {
                  const reviewed = (confirmReviewKeys || []).includes(key);
                  const already = confirmedSet.has(key);
                  return (
                    <li key={key} data-testid={`role-package-review-${key}`}>
                      <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={reviewed}
                          onChange={() => toggleReviewKey(key)}
                          data-testid={`role-package-review-check-${key}`}
                        />
                        <code>{key}</code>
                        <span className="muted small">{already ? "(sudah aktif)" : "(baru)"}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
              <p className="muted small" data-testid="role-package-activation-preview">
                {activationPreview.length
                  ? `${activationPreview.length} key akan menjadi stamp konfirmasi (mengganti stamp sebelumnya untuk himpunan ini).`
                  : "Tidak ada key yang akan dikonfirmasi — stamp package akan dikosongkan untuk grant saat ini."}
              </p>
            </div>
          )}
          {!confirmPackageOptIn && pendingUnconfirmed.length > 0 && (
            <p className="muted small" data-testid="role-package-unconfirmed-hint" style={{ marginTop: 8 }}>
              {pendingUnconfirmed.length} izin package tersimpan tetapi belum aktif di production sampai dikonfirmasi.
            </p>
          )}
        </div>
      )}

      <div className="role-perm-flow" data-testid={`role-perms-${selected}`}>
        {lockedFullAccess ? (
          <p className="muted" data-testid="role-full-access-notice">
            {isDirectorRole
              ? "Akses penuh sistem — editor permission tidak tersedia untuk role Direktur."
              : "Seluruh hak akses — editor permission tidak tersedia untuk role Admin."}
          </p>
        ) : (
          <>
            {visibleModules.map(([mod, perms]) => {
              const catalogPerms = (allModules.find(([name]) => name === mod) || [mod, perms])[1];
              const catTotals = countRolePermissionTotals([[mod, catalogPerms]], permsDraft);
              const open = openMods.has(mod);
              const slug = moduleSlug(mod);
              const visibleKeys = perms.map((p) => p.key);
              const allVisibleOn = visibleKeys.length > 0 && visibleKeys.every((k) => permsDraft.has(k));
              return (
                <section
                  key={mod}
                  className={"role-cat" + (open ? " is-open" : "")}
                  data-testid={`role-cat-${slug}`}
                >
                  <div className="role-cat-head">
                    <button
                      type="button"
                      className="role-cat-toggle"
                      aria-expanded={open}
                      onClick={() => toggleMod(mod)}
                      data-testid={`role-cat-toggle-${slug}`}
                    >
                      <ChevronDown size={14} className={"role-cat-chevron" + (open ? " is-open" : "")} />
                      <span className="role-cat-name">{mod}</span>
                      <span className="role-cat-count" data-testid={`role-cat-count-${slug}`}>
                        {catTotals.active} / {catTotals.total} aktif
                      </span>
                    </button>
                    {canEdit && (
                      <label className="role-cat-select-all">
                        <input
                          type="checkbox"
                          checked={allVisibleOn}
                          onChange={() => toggleCategoryAll(mod, perms)}
                          data-testid={`role-cat-select-all-${slug}`}
                        />
                        Pilih semua
                      </label>
                    )}
                  </div>
                  {open && (
                    <div className="role-perm-grid" data-testid={`role-cat-grid-${slug}`}>
                      {perms.map((p) => {
                        const checked = permsDraft.has(p.key);
                        const sensitive = isSensitivePermissionKey(p.key);
                        return (
                          <label
                            key={p.key}
                            className={"role-perm-cell" + (checked ? " is-on" : "") + (sensitive ? " is-sensitive" : "")}
                            data-testid={`role-perm-${selected}-${p.key}`}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => onTogglePerm(p.key)}
                              disabled={!canEdit}
                            />
                            <span className="role-perm-copy">
                              <span className="role-perm-label">
                                {p.label}
                                {sensitive && (
                                  <span className="role-perm-sensitive" data-testid={`role-perm-sensitive-${p.key}`}>
                                    <AlertTriangle size={10}/> Sensitif
                                  </span>
                                )}
                              </span>
                              <span className="role-perm-key">{p.key}</span>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </section>
              );
            })}
            {!visibleModules.length && <p className="muted small" data-testid="role-perm-empty">Tidak ada permission cocok.</p>}
          </>
        )}
      </div>

      {canEdit && (
        <div className="role-danger-zone" data-testid="role-danger-zone">
          <div>
            <div className="role-danger-title">Danger Zone</div>
            <p className="muted small">Aksi tingkat role. Tidak mengubah permission yang dipilih di atas.</p>
          </div>
          <button className="ghost sm danger" onClick={() => onToggleActive(currentRole)} data-testid="role-toggle-active-footer">
            {statusActive ? <><PowerOff size={12}/> Nonaktifkan Role</> : <><Power size={12}/> Aktifkan Role</>}
          </button>
        </div>
      )}

      {canEdit && (
        <div className={"role-save-bar" + (dirty ? " show" : "")} data-testid="role-save-bar">
          <span className="role-save-status" data-testid="role-unsaved-indicator">
            {dirty
              ? <><AlertCircle size={14}/> {dirtyCount} perubahan belum disimpan</>
              : <><Check size={14}/> Tersimpan</>}
          </span>
          <div className="role-save-actions">
            {dirty && (
              <button className="ghost" onClick={onDiscard} data-testid="role-discard">
                <X size={13}/> Batal
              </button>
            )}
            <button className="primary" onClick={onSave} disabled={busy || !dirty} data-testid="role-save-permissions">
              <Save size={14}/> Simpan Perubahan
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
