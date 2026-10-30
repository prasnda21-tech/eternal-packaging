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
  bulkPermissionKeys,
  countRolePermissionDirty,
  countRolePermissionTotals,
  filterRolePermissionModules,
  isSensitivePermissionKey,
} from "../lib/settingsPermissionUi";
import {
  isPackagePermissionKey,
  packageKeysInGrant,
  packageKeysToActivate,
  roleMayReceivePackageAccess,
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
  const [reviewOpen, setReviewOpen] = useState(!!confirmPackageOptIn);
  const [descEditing, setDescEditing] = useState(false);

  useEffect(() => {
    const defaults = allModules.slice(0, DEFAULT_OPEN_COUNT).map(([mod]) => mod);
    setOpenMods(new Set(defaults));
    setMenuOpen(false);
    setReviewOpen(false);
    setDescEditing(false);
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

  const expandAllMods = () => {
    setOpenMods(new Set(visibleModules.map(([mod]) => mod)));
  };

  const collapseAllMods = () => {
    setOpenMods(new Set());
  };

  const packageAlreadyEffective = roleMayReceivePackageAccess(selected);

  const toggleCategoryAll = (mod, perms) => {
    // Package keys are chosen one by one, then approved. Select-all must not queue them.
    const keys = bulkPermissionKeys(perms).filter((k) => !isPackagePermissionKey(k));
    if (!keys.length) return;
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
  const labelByKey = useMemo(() => {
    const map = new Map();
    for (const rows of Object.values(permissionCatalog?.modules || {})) {
      for (const row of rows || []) {
        if (row?.key) map.set(row.key, row.label || row.key);
      }
    }
    return map;
  }, [permissionCatalog]);

  useEffect(() => {
    if (confirmPackageOptIn) setReviewOpen(true);
  }, [confirmPackageOptIn, selected]);

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
          </div>
        </div>
        {!lockedFullAccess && !descEditing && (
          <button
            type="button"
            className="role-desc-toggle"
            onClick={() => setDescEditing(true)}
            data-testid="role-description-toggle"
          >
            {descDraft ? descDraft : "Tambah deskripsi"}
          </button>
        )}
        {!lockedFullAccess && descEditing && (
          <textarea
            className="role-desc-input"
            value={descDraft}
            onChange={(e) => onDescChange(e.target.value)}
            rows={2}
            placeholder="Deskripsi role (opsional)"
            disabled={!canManagePerms}
            data-testid="role-description-input"
            onBlur={() => { if (!String(descDraft || "").trim()) setDescEditing(false); }}
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
            <div className="role-perm-accordion-tools" data-testid="role-perm-accordion-tools">
              <button
                type="button"
                className="ghost sm"
                onClick={expandAllMods}
                data-testid="role-perm-expand-all"
              >
                Buka semua
              </button>
              <button
                type="button"
                className="ghost sm"
                onClick={collapseAllMods}
                data-testid="role-perm-collapse-all"
              >
                Tutup semua
              </button>
            </div>
            <p className="role-perm-search-hint" data-testid="role-perm-search-hint" title="Izin khusus dan Pembelian/Persediaan/HPP dipilih satu per satu. Pilih semua tidak mengaktifkannya.">
              Izin khusus dan package dipilih satu per satu. Pilih semua tidak mengaktifkannya.
            </p>
          </div>
        )}
      </div>

      <div className="role-workspace-body" data-testid="role-workspace-body">
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

      {canEdit && !packageAlreadyEffective && draftPackageKeys.length > 0 && (
        <div className="role-package-summary" data-testid="role-package-confirm-panel">
          <strong>Pembelian / Persediaan / HPP</strong>
          <span className="role-package-summary-count" data-testid="role-package-confirm-meta">
            Berlaku {confirmedSet.size} · Menunggu {pendingUnconfirmed.length}
          </span>
          <button
            type="button"
            className="ghost sm"
            onClick={() => setReviewOpen(true)}
            data-testid="role-package-review-open"
          >
            Tinjau izin
          </button>
        </div>
      )}
      {canEdit && !packageAlreadyEffective && draftPackageKeys.length > 0 && reviewOpen && (
        <div className="role-review-backdrop" data-testid="role-package-review-dialog">
          <div className="role-review-dialog" role="dialog" aria-modal="true" aria-labelledby="role-package-review-title">
            <header className="role-review-head">
              <h3 id="role-package-review-title">Tinjau Pembelian / Persediaan / HPP</h3>
              <button type="button" className="icon-button" aria-label="Tutup" onClick={() => setReviewOpen(false)}>
                <X size={16}/>
              </button>
            </header>
            <p className="role-review-lead">
              Centang izin yang akan berlaku. Yang tidak dicentang tetap tersimpan dan tidak efektif. Tidak ada izin yang disetujui otomatis.
            </p>
            <div className="role-review-list" data-testid="role-package-confirm-list">
              {draftPackageKeys.map((key) => {
                const reviewed = (confirmReviewKeys || []).includes(key);
                const menuKey = String(key).startsWith("nav.");
                return (
                  <label key={key} className="role-review-row" data-testid={`role-package-review-${key}`}>
                    <input
                      type="checkbox"
                      checked={reviewed}
                      onChange={() => toggleReviewKey(key)}
                      data-testid={`role-package-review-check-${key}`}
                    />
                    <span className="role-review-copy">
                      <span className="role-review-label">{labelByKey.get(key) || key}</span>
                      <span className="role-perm-key">{key}</span>
                    </span>
                    <span className={"role-review-kind" + (menuKey ? " is-menu" : "")}>{menuKey ? "Menu" : "Tindakan"}</span>
                    <span className="role-review-effect">{reviewed ? "Akan berlaku" : "Tetap tersimpan, tidak efektif"}</span>
                  </label>
                );
              })}
            </div>
            <p className="role-review-preview" data-testid="role-package-activation-preview">
              {activationPreview.length
                ? `${activationPreview.length} izin akan berlaku. Izin di luar daftar tetap tersimpan dan tidak efektif.`
                : "Tidak ada izin yang disetujui. Izin package tersimpan tidak menjadi efektif."}
            </p>
            <label className="role-package-confirm-optin">
              <input
                type="checkbox"
                checked={!!confirmPackageOptIn}
                onChange={(e) => onConfirmPackageOptInChange?.(e.target.checked)}
                data-testid="role-package-confirm-optin"
              />
              <span>Setujui hanya izin yang dicentang pada simpan ini</span>
            </label>
            <div className="role-review-actions">
              <button type="button" className="ghost" onClick={() => setReviewOpen(false)}>Tutup</button>
            </div>
          </div>
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
              const visibleKeys = perms.filter((p) => !p.opt_in && !isPackagePermissionKey(p.key)).map((p) => p.key);
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
                            className={
                              "role-perm-cell"
                              + (checked ? " is-on" : "")
                              + (sensitive ? " is-sensitive" : "")
                            }
                            data-testid={`role-perm-${selected}-${p.key}`}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => onTogglePerm(p.key)}
                              disabled={!canEdit}
                            />
                            <span className="role-perm-copy" title={p.key}>
                              <span className="role-perm-label">
                                {p.label}
                                {p.opt_in && <span className="role-perm-sensitive">KHUSUS</span>}
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
      </div>

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
