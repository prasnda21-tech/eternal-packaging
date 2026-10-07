import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, ImageIcon, Plus, Search, X } from "lucide-react";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";
import "./PublicCatalogSettings.css";

const SECTIONS = [
  { id: "umum", label: "Informasi Umum", testid: "pcs-nav-umum" },
  { id: "kategori", label: "Kategori", testid: "pcs-nav-kategori" },
];

const IMAGE_ACCEPT = "image/jpeg,image/png,image/webp,image/jpg";

export function slugifyCategoryName(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-")
    .slice(0, 80);
}

function emptyGlobal() {
  return {
    catalog_name: "",
    hero_title: "",
    hero_subtitle: "",
    public_whatsapp_phone: "",
    catalog_enabled: false,
  };
}

function emptyEditor() {
  return {
    id: null,
    name: "",
    slug: "",
    display_order: 0,
    enabled: true,
    image: null,
    slugTouched: false,
  };
}

export default function PublicCatalogSettings({ notify, canEdit }) {
  const [section, setSection] = useState("umum");
  const [settings, setSettings] = useState(emptyGlobal);
  const [categories, setCategories] = useState([]);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editor, setEditor] = useState(null);
  const [pendingFile, setPendingFile] = useState(null);
  const [removeImage, setRemoveImage] = useState(false);
  const closeRef = useRef(null);
  const fileRef = useRef(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [settingsRes, categoriesRes] = await Promise.all([
        api.publicCatalogSettings(),
        api.publicCatalogCategories(query ? { q: query } : {}),
      ]);
      setSettings({
        ...emptyGlobal(),
        ...(settingsRes.settings || {}),
      });
      setCategories(categoriesRes.categories || []);
    } catch (err) {
      setError(errDetail(err) || "Gagal memuat Katalog Publik.");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (!editor) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") {
        setEditor(null);
        setPendingFile(null);
        setRemoveImage(false);
      }
    };
    window.addEventListener("keydown", onKey);
    closeRef.current?.focus?.();
    return () => window.removeEventListener("keydown", onKey);
  }, [editor]);

  const filteredHint = useMemo(
    () => (query ? `Hasil pencarian “${query}”` : null),
    [query]
  );

  const openCreate = () => {
    setPendingFile(null);
    setRemoveImage(false);
    setEditor(emptyEditor());
  };

  const openEdit = (category) => {
    setPendingFile(null);
    setRemoveImage(false);
    setEditor({
      id: category.id,
      name: category.name || "",
      slug: category.slug || "",
      display_order: category.display_order ?? 0,
      enabled: Boolean(category.enabled),
      image: category.image || null,
      slugTouched: true,
    });
  };

  const closeEditor = () => {
    setEditor(null);
    setPendingFile(null);
    setRemoveImage(false);
  };

  const saveGlobal = async () => {
    if (!canEdit || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.updatePublicCatalogSettings({
        catalog_name: settings.catalog_name,
        hero_title: settings.hero_title,
        hero_subtitle: settings.hero_subtitle,
        public_whatsapp_phone: settings.public_whatsapp_phone,
        catalog_enabled: Boolean(settings.catalog_enabled),
      });
      setSettings({ ...emptyGlobal(), ...(response.settings || {}) });
      notify?.(response.message || "Pengaturan Katalog Publik disimpan");
    } catch (err) {
      setError(errDetail(err) || "Gagal menyimpan informasi umum.");
    } finally {
      setBusy(false);
    }
  };

  const saveCategory = async () => {
    if (!canEdit || busy || !editor) return;
    setBusy(true);
    setError("");
    try {
      const payload = {
        name: editor.name,
        slug: editor.slug,
        display_order: Number(editor.display_order) || 0,
        enabled: Boolean(editor.enabled),
      };
      let category;
      if (editor.id) {
        const response = await api.patchPublicCatalogCategory(editor.id, payload);
        category = response.category;
        notify?.(response.message || "Kategori diperbarui");
      } else {
        const response = await api.createPublicCatalogCategory(payload);
        category = response.category;
        notify?.(response.message || "Kategori dibuat");
      }
      if (category?.id && pendingFile) {
        const uploaded = await api.uploadPublicCatalogCategoryImage(category.id, pendingFile);
        category = uploaded.category;
      } else if (category?.id && removeImage && editor.id) {
        const removed = await api.deletePublicCatalogCategoryImage(category.id);
        category = removed.category;
      }
      closeEditor();
      await loadAll();
      if (category) {
        setCategories((current) => {
          const rest = current.filter((row) => row.id !== category.id);
          return [...rest, category].sort((a, b) => {
            const order = (a.display_order || 0) - (b.display_order || 0);
            if (order !== 0) return order;
            return String(a.name || "").localeCompare(String(b.name || ""), "id");
          });
        });
      }
    } catch (err) {
      setError(errDetail(err) || "Gagal menyimpan kategori.");
    } finally {
      setBusy(false);
    }
  };

  const previewUrl = useMemo(() => {
    if (pendingFile) return URL.createObjectURL(pendingFile);
    if (removeImage) return null;
    if (editor?.image?.file_id) {
      return api.publicCatalogCategoryImageUrl(editor.id, editor.image.file_id);
    }
    return null;
  }, [editor, pendingFile, removeImage]);

  useEffect(() => {
    if (!pendingFile || !previewUrl) return undefined;
    return () => URL.revokeObjectURL(previewUrl);
  }, [pendingFile, previewUrl]);

  return (
    <div className="settings-card pcs-page" data-testid="public-catalog-settings">
      <div className="pcs-head">
        <div>
          <h2>Katalog Publik</h2>
          <p className="pcs-subhead">
            Atur tampilan, struktur produk, gambar, dan URL katalog customer.
          </p>
        </div>
        <div className="pcs-meta-strip" data-testid="pcs-meta-strip">
          <div className="pcs-meta-item">
            <span className="pcs-meta-label">Public URL</span>
            <span className="pcs-meta-value">katalog.eternalpackaging.id</span>
          </div>
          <div className="pcs-meta-item">
            <span className="pcs-meta-label">Status</span>
            <span className="pcs-meta-badge">Draft Konfigurasi</span>
          </div>
        </div>
      </div>

      <nav className="pcs-section-nav" aria-label="Bagian Katalog Publik" data-testid="pcs-section-nav">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={section === item.id ? "is-active" : ""}
            data-testid={item.testid}
            aria-current={section === item.id ? "page" : undefined}
            onClick={() => setSection(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {error && (
        <div className="signal-strip" role="alert" data-testid="pcs-error">
          <AlertCircle size={16} /><span>{error}</span>
        </div>
      )}

      {loading ? (
        <p className="muted small" data-testid="pcs-loading">Memuat…</p>
      ) : null}

      {!loading && section === "umum" && (
        <section className="pcs-section" data-testid="pcs-general">
          <div className="pcs-surface">
            <div className="pcs-section-head">
              <div>
                <h3>Informasi Umum</h3>
                <p className="pcs-section-desc">
                  Konfigurasi dasar tampilan katalog publik.
                </p>
              </div>
            </div>
            <div className="pcs-grid-2">
              <label className="pcs-field">Nama Katalog
                <input
                  value={settings.catalog_name}
                  onChange={(event) =>
                    setSettings((current) => ({ ...current, catalog_name: event.target.value }))
                  }
                  disabled={!canEdit || busy}
                  data-testid="pcs-catalog-name"
                />
              </label>
              <label className="pcs-field">Nomor WhatsApp Publik
                <input
                  value={settings.public_whatsapp_phone}
                  onChange={(event) =>
                    setSettings((current) => ({
                      ...current,
                      public_whatsapp_phone: event.target.value,
                    }))
                  }
                  disabled={!canEdit || busy}
                  data-testid="pcs-public-whatsapp"
                />
              </label>
              <label className="pcs-field">Judul Hero
                <input
                  value={settings.hero_title}
                  onChange={(event) =>
                    setSettings((current) => ({ ...current, hero_title: event.target.value }))
                  }
                  disabled={!canEdit || busy}
                  data-testid="pcs-hero-title"
                />
              </label>
              <label className="pcs-field" data-testid="pcs-catalog-enabled-wrap">
                Status Katalog
                <select
                  value={settings.catalog_enabled ? "1" : "0"}
                  onChange={(event) =>
                    setSettings((current) => ({
                      ...current,
                      catalog_enabled: event.target.value === "1",
                    }))
                  }
                  disabled={!canEdit || busy}
                  data-testid="pcs-catalog-enabled"
                >
                  <option value="1">Aktif</option>
                  <option value="0">Nonaktif</option>
                </select>
              </label>
              <label className="pcs-field pcs-span-2">Subjudul Hero
                <input
                  value={settings.hero_subtitle}
                  onChange={(event) =>
                    setSettings((current) => ({ ...current, hero_subtitle: event.target.value }))
                  }
                  disabled={!canEdit || busy}
                  data-testid="pcs-hero-subtitle"
                />
              </label>
            </div>
            {canEdit ? (
              <div className="pcs-actions pcs-actions-end">
                <button
                  type="button"
                  className="primary sm"
                  disabled={busy}
                  onClick={saveGlobal}
                  data-testid="pcs-save-general"
                >
                  Simpan Informasi
                </button>
              </div>
            ) : null}
          </div>
        </section>
      )}

      {!loading && section === "kategori" && (
        <section className="pcs-section" data-testid="pcs-categories">
          <div className="pcs-surface">
            <div className="pcs-section-head">
              <h3>Kategori Produk</h3>
              <div className="pcs-toolbar">
                <label className="pcs-search">
                  <span className="sr-only">Cari kategori</span>
                  <Search size={14} className="pcs-search-icon" aria-hidden="true" />
                  <input
                    value={queryInput}
                    placeholder="Cari kategori..."
                    onChange={(event) => setQueryInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        setQuery(queryInput.trim());
                      }
                    }}
                    data-testid="pcs-category-search"
                  />
                </label>
                {canEdit ? (
                  <button
                    type="button"
                    className="primary sm"
                    disabled={busy}
                    onClick={openCreate}
                    data-testid="pcs-add-category"
                  >
                    <Plus size={14} /> Tambah Kategori
                  </button>
                ) : null}
              </div>
            </div>

            {filteredHint ? (
              <p className="muted small" data-testid="pcs-search-hint">{filteredHint}</p>
            ) : null}

            {categories.length === 0 ? (
              <div className="pcs-empty" data-testid="pcs-category-empty">
                <p>Belum ada kategori katalog.</p>
                <p className="pcs-empty-hint">
                  Tambahkan kategori untuk mengelompokkan produk di katalog publik.
                </p>
                {canEdit ? (
                  <button
                    type="button"
                    className="primary sm"
                    onClick={openCreate}
                    data-testid="pcs-empty-add-category"
                  >
                    Tambah Kategori
                  </button>
                ) : null}
              </div>
            ) : (
              <div className="pcs-list" data-testid="pcs-category-list">
                {categories.map((category) => (
                  <article
                    key={category.id}
                    className="pcs-row"
                    data-testid="pcs-category-row"
                    data-category-id={category.id}
                  >
                    <div className="pcs-thumb" aria-hidden="true">
                      {category.image?.file_id ? (
                        <img
                          src={api.publicCatalogCategoryImageUrl(
                            category.id,
                            category.image.file_id
                          )}
                          alt=""
                        />
                      ) : (
                        <ImageIcon size={16} />
                      )}
                    </div>
                    <div className="pcs-row-main">
                      <div className="pcs-row-title">{category.name}</div>
                      <div className="pcs-row-slug">{category.slug}</div>
                    </div>
                    <div className="pcs-row-order">Urutan {category.display_order ?? 0}</div>
                    <div className="pcs-row-status">
                      <span
                        className={`pcs-badge ${category.enabled ? "is-on" : "is-off"}`}
                        data-testid="pcs-category-status"
                      >
                        {category.enabled ? "Aktif" : "Nonaktif"}
                      </span>
                    </div>
                    <div className="pcs-row-action">
                      {canEdit ? (
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => openEdit(category)}
                          data-testid="pcs-edit-category"
                        >
                          Edit
                        </button>
                      ) : null}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {editor && createPortal(
        <div
          className="drawer pcs-drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="pcs-category-drawer-title"
          data-testid="pcs-category-drawer"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeEditor();
          }}
        >
          <div className="drawer-panel pcs-drawer-panel" onClick={(event) => event.stopPropagation()}>
            <header className="drawer-head">
              <div>
                <h3 id="pcs-category-drawer-title">
                  {editor.id ? "Edit Kategori" : "Tambah Kategori"}
                </h3>
                <p className="muted small">Nama, slug, urutan, status, dan gambar.</p>
              </div>
              <button
                ref={closeRef}
                type="button"
                className="ghost sm"
                onClick={closeEditor}
                data-testid="pcs-drawer-close"
                aria-label="Tutup"
              >
                <X size={16} />
              </button>
            </header>
            <div className="drawer-body pcs-drawer-body">
              <div className="pcs-drawer-fields">
                <label className="pcs-field">Nama Kategori
                  <input
                    value={editor.name}
                    onChange={(event) => {
                      const name = event.target.value;
                      setEditor((current) => ({
                        ...current,
                        name,
                        slug: current.slugTouched
                          ? current.slug
                          : slugifyCategoryName(name),
                      }));
                    }}
                    disabled={!canEdit || busy}
                    data-testid="pcs-editor-name"
                  />
                </label>
                <label className="pcs-field">Slug
                  <input
                    value={editor.slug}
                    onChange={(event) =>
                      setEditor((current) => ({
                        ...current,
                        slug: event.target.value,
                        slugTouched: true,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-editor-slug"
                  />
                </label>
                <div className="pcs-drawer-pair">
                  <label className="pcs-field">Urutan Tampil
                    <input
                      type="number"
                      min={0}
                      value={editor.display_order}
                      onChange={(event) =>
                        setEditor((current) => ({
                          ...current,
                          display_order: event.target.value,
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-editor-order"
                    />
                  </label>
                  <label className="pcs-field">Status
                    <select
                      value={editor.enabled ? "1" : "0"}
                      onChange={(event) =>
                        setEditor((current) => ({
                          ...current,
                          enabled: event.target.value === "1",
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-editor-enabled"
                    >
                      <option value="1">Aktif</option>
                      <option value="0">Nonaktif</option>
                    </select>
                  </label>
                </div>
                <div className="pcs-image-box" data-testid="pcs-editor-image">
                  <span className="pcs-field">Gambar Kategori</span>
                  <div className="pcs-image-row">
                    <div className={`pcs-image-preview${previewUrl ? " has-image" : ""}`}>
                      {previewUrl ? (
                        <img src={previewUrl} alt="" />
                      ) : (
                        <>
                          <ImageIcon size={22} />
                          <span className="pcs-image-placeholder-label">Belum ada gambar</span>
                        </>
                      )}
                    </div>
                    {canEdit ? (
                      <div className="pcs-image-actions">
                        <input
                          ref={fileRef}
                          type="file"
                          accept={IMAGE_ACCEPT}
                          className="pcs-file-input"
                          data-testid="pcs-editor-image-input"
                          onChange={(event) => {
                            const file = event.target.files?.[0] || null;
                            setPendingFile(file);
                            setRemoveImage(false);
                            event.target.value = "";
                          }}
                        />
                        <button
                          type="button"
                          className="ghost sm"
                          disabled={busy}
                          onClick={() => fileRef.current?.click()}
                          data-testid="pcs-editor-image-upload"
                        >
                          Unggah Gambar
                        </button>
                        {(previewUrl || editor.image) && !removeImage ? (
                          <button
                            type="button"
                            className="ghost sm"
                            disabled={busy}
                            onClick={() => {
                              setPendingFile(null);
                              setRemoveImage(true);
                            }}
                            data-testid="pcs-editor-image-remove"
                          >
                            Hapus Gambar
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
              <div className="pcs-drawer-footer" data-testid="pcs-drawer-footer">
                <button
                  type="button"
                  className="ghost sm"
                  onClick={closeEditor}
                  disabled={busy}
                  data-testid="pcs-editor-cancel"
                >
                  Batal
                </button>
                {canEdit ? (
                  <button
                    type="button"
                    className="primary sm"
                    onClick={saveCategory}
                    disabled={busy}
                    data-testid="pcs-editor-save"
                  >
                    Simpan
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
