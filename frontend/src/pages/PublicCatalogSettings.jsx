import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, ImageIcon, Plus, Search, X } from "lucide-react";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";
import "./PublicCatalogSettings.css";

const SECTIONS = [
  { id: "umum", label: "Informasi Umum", testid: "pcs-nav-umum" },
  { id: "kategori", label: "Kategori", testid: "pcs-nav-kategori" },
  { id: "kelompok", label: "Kelompok Produk", testid: "pcs-nav-kelompok" },
  { id: "varian", label: "Varian", testid: "pcs-nav-varian" },
  { id: "galeri", label: "Galeri", testid: "pcs-nav-galeri" },
  { id: "seo", label: "URL & SEO", testid: "pcs-nav-seo" },
];

const IMAGE_ACCEPT = "image/jpeg,image/png,image/webp,image/jpg";

const IMAGE_ROLE_OPTIONS = [
  { value: "MAIN", label: "Utama" },
  { value: "FRONT", label: "Depan" },
  { value: "SIDE", label: "Samping" },
  { value: "TOP", label: "Atas" },
  { value: "DETAIL", label: "Detail" },
  { value: "OTHER", label: "Lainnya" },
];

function emptyVariantEditor(variant, pricingGroupId) {
  const catalog = variant?.catalog || {};
  return {
    pricing_group_id: pricingGroupId || variant?.pricing_group_id || "",
    accurate_item_id: variant?.accurate_item_id || "",
    display_name: variant?.display_name || "",
    item_code: variant?.item_code || "",
    unit: variant?.unit || "",
    is_active: Boolean(variant?.is_active !== false),
    size: catalog.size || "",
    color: catalog.color || "",
    finishing: catalog.finishing || "",
    display_order: catalog.display_order ?? 0,
    visible: catalog.configured ? Boolean(catalog.visible) : true,
  };
}

function emptyGalleryEditor(image = null) {
  return {
    id: image?.id || null,
    role: image?.role || "OTHER",
    display_order: image?.display_order ?? 0,
    is_default: Boolean(image?.is_default),
    enabled: image ? Boolean(image.enabled) : true,
    file: null,
  };
}

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

export function previewProductUrl(slug) {
  const clean = String(slug || "").trim();
  if (!clean) return "Belum ada slug";
  return `katalog.eternalpackaging.id/produk/${clean}`;
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

function emptyGroupEditor(group) {
  const catalog = group?.catalog || {};
  return {
    pricing_group_id: group?.pricing_group_id || "",
    group_name: group?.group_name || "",
    pricing_enabled: Boolean(group?.pricing_enabled),
    public_description: group?.public_description || "",
    catalog_image: group?.catalog_image || null,
    category_id: catalog.category_id || "",
    public_name: catalog.public_name || "",
    slug: catalog.slug || "",
    display_order: catalog.display_order ?? 0,
    visible: catalog.configured ? Boolean(catalog.visible) : true,
    meta_title: catalog.meta_title || "",
    meta_description: catalog.meta_description || "",
    configured: Boolean(catalog.configured),
  };
}

export default function PublicCatalogSettings({ notify, canEdit }) {
  const [section, setSection] = useState("umum");
  const [settings, setSettings] = useState(emptyGlobal);
  const [categories, setCategories] = useState([]);
  const [groups, setGroups] = useState([]);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [groupQuery, setGroupQuery] = useState("");
  const [groupVisibilityFilter, setGroupVisibilityFilter] = useState("all");
  const [groupCategoryFilter, setGroupCategoryFilter] = useState("all");
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [selectedVariantId, setSelectedVariantId] = useState("");
  const [variants, setVariants] = useState([]);
  const [variantQuery, setVariantQuery] = useState("");
  const [variantVisibilityFilter, setVariantVisibilityFilter] = useState("all");
  const [variantEditor, setVariantEditor] = useState(null);
  const [gallery, setGallery] = useState(null);
  const [galleryPreviewId, setGalleryPreviewId] = useState("");
  const [galleryEditor, setGalleryEditor] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editor, setEditor] = useState(null);
  const [groupEditor, setGroupEditor] = useState(null);
  const [pendingFile, setPendingFile] = useState(null);
  const [removeImage, setRemoveImage] = useState(false);
  const closeRef = useRef(null);
  const groupCloseRef = useRef(null);
  const variantCloseRef = useRef(null);
  const galleryCloseRef = useRef(null);
  const fileRef = useRef(null);
  const galleryFileRef = useRef(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [settingsRes, categoriesRes, groupsRes] = await Promise.all([
        api.publicCatalogSettings(),
        api.publicCatalogCategories(query ? { q: query } : {}),
        api.publicCatalogGroups(),
      ]);
      setSettings({
        ...emptyGlobal(),
        ...(settingsRes.settings || {}),
      });
      setCategories(categoriesRes.categories || []);
      setGroups(groupsRes.groups || []);
    } catch (err) {
      setError(errDetail(err) || "Gagal memuat Katalog Publik.");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const loadVariants = useCallback(async (pricingGroupId) => {
    if (!pricingGroupId) {
      setVariants([]);
      return;
    }
    try {
      const response = await api.publicCatalogVariants(pricingGroupId);
      setVariants(response.variants || []);
    } catch (err) {
      setError(errDetail(err) || "Gagal memuat varian katalog.");
      setVariants([]);
    }
  }, []);

  const loadGallery = useCallback(async (pricingGroupId, accurateItemId) => {
    if (!pricingGroupId || !accurateItemId) {
      setGallery(null);
      setGalleryPreviewId("");
      return;
    }
    try {
      const response = await api.publicCatalogVariantImages(pricingGroupId, accurateItemId);
      setGallery(response);
      const images = response.images || [];
      const defaultImage = images.find((image) => image.is_default && image.enabled)
        || images.find((image) => image.enabled)
        || images[0];
      setGalleryPreviewId((current) => {
        if (current && images.some((image) => image.id === current)) return current;
        return defaultImage?.id || "";
      });
    } catch (err) {
      setError(errDetail(err) || "Gagal memuat galeri.");
      setGallery(null);
    }
  }, []);

  useEffect(() => {
    if (section === "varian" || section === "galeri") {
      loadVariants(selectedGroupId);
    }
  }, [section, selectedGroupId, loadVariants]);

  useEffect(() => {
    if (section === "galeri") {
      loadGallery(selectedGroupId, selectedVariantId);
    }
  }, [section, selectedGroupId, selectedVariantId, loadGallery]);

  useEffect(() => {
    if (!editor && !groupEditor && !variantEditor && !galleryEditor) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") {
        setEditor(null);
        setGroupEditor(null);
        setVariantEditor(null);
        setGalleryEditor(null);
        setPendingFile(null);
        setRemoveImage(false);
      }
    };
    window.addEventListener("keydown", onKey);
    if (editor) closeRef.current?.focus?.();
    if (groupEditor) groupCloseRef.current?.focus?.();
    if (variantEditor) variantCloseRef.current?.focus?.();
    if (galleryEditor) galleryCloseRef.current?.focus?.();
    return () => window.removeEventListener("keydown", onKey);
  }, [editor, groupEditor, variantEditor, galleryEditor]);

  const filteredHint = useMemo(
    () => (query ? `Hasil pencarian “${query}”` : null),
    [query]
  );

  const filteredGroups = useMemo(() => {
    const needle = groupQuery.trim().toLowerCase();
    return groups.filter((group) => {
      const catalog = group.catalog || {};
      if (groupCategoryFilter !== "all") {
        if (groupCategoryFilter === "unset") {
          if (catalog.category_id) return false;
        } else if (catalog.category_id !== groupCategoryFilter) {
          return false;
        }
      }
      if (groupVisibilityFilter === "configured_visible") {
        if (!catalog.configured || !catalog.visible) return false;
      } else if (groupVisibilityFilter === "configured_hidden") {
        if (!catalog.configured || catalog.visible) return false;
      } else if (groupVisibilityFilter === "unset") {
        if (catalog.configured) return false;
      }
      if (!needle) return true;
      const hay = [
        group.group_name,
        catalog.public_name,
        catalog.public_name_display,
        catalog.slug,
        catalog.category_name,
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [groups, groupQuery, groupCategoryFilter, groupVisibilityFilter]);

  const selectedGroup = useMemo(
    () => groups.find((group) => group.pricing_group_id === selectedGroupId) || null,
    [groups, selectedGroupId]
  );

  const seoDraft = useMemo(() => {
    if (!selectedGroup) return null;
    return emptyGroupEditor(selectedGroup);
  }, [selectedGroup]);

  const [seoForm, setSeoForm] = useState(null);
  useEffect(() => {
    setSeoForm(seoDraft);
  }, [seoDraft]);

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

  const openGroupEditor = (group) => {
    setSelectedGroupId(group.pricing_group_id);
    setGroupEditor(emptyGroupEditor(group));
  };

  const closeGroupEditor = () => setGroupEditor(null);

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
        await api.uploadPublicCatalogCategoryImage(category.id, pendingFile);
      } else if (category?.id && removeImage && editor.id) {
        await api.deletePublicCatalogCategoryImage(category.id);
      }
      closeEditor();
      await loadAll();
    } catch (err) {
      setError(errDetail(err) || "Gagal menyimpan kategori.");
    } finally {
      setBusy(false);
    }
  };

  const saveGroupPresentation = async (form, { closeDrawer = false } = {}) => {
    if (!canEdit || busy || !form?.pricing_group_id) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.updatePublicCatalogGroup(form.pricing_group_id, {
        category_id: form.category_id || null,
        public_name: form.public_name,
        slug: form.slug,
        display_order: Number(form.display_order) || 0,
        visible: Boolean(form.visible),
        meta_title: form.meta_title,
        meta_description: form.meta_description,
      });
      notify?.(response.message || "Presentasi kelompok katalog disimpan");
      if (closeDrawer) closeGroupEditor();
      await loadAll();
      if (response.group) {
        setSelectedGroupId(response.group.pricing_group_id);
      }
    } catch (err) {
      setError(errDetail(err) || "Gagal menyimpan presentasi kelompok.");
    } finally {
      setBusy(false);
    }
  };

  const filteredVariants = useMemo(() => {
    const needle = variantQuery.trim().toLowerCase();
    return variants.filter((variant) => {
      const catalog = variant.catalog || {};
      if (variantVisibilityFilter === "visible") {
        if (catalog.configured && !catalog.visible) return false;
      } else if (variantVisibilityFilter === "hidden") {
        if (!catalog.configured || catalog.visible) return false;
      } else if (variantVisibilityFilter === "unset") {
        if (catalog.configured) return false;
      }
      if (!needle) return true;
      const hay = [
        variant.display_name,
        variant.item_code,
        catalog.size,
        catalog.color,
        catalog.finishing,
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [variants, variantQuery, variantVisibilityFilter]);

  const openVariantEditor = (variant) => {
    setSelectedVariantId(variant.accurate_item_id);
    setVariantEditor(emptyVariantEditor(variant, selectedGroupId));
  };

  const closeVariantEditor = () => setVariantEditor(null);

  const saveVariant = async () => {
    if (!canEdit || busy || !variantEditor?.accurate_item_id) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.updatePublicCatalogVariant(
        variantEditor.pricing_group_id,
        variantEditor.accurate_item_id,
        {
          size: variantEditor.size,
          color: variantEditor.color,
          finishing: variantEditor.finishing,
          display_order: Number(variantEditor.display_order) || 0,
          visible: Boolean(variantEditor.visible),
        }
      );
      notify?.(response.message || "Metadata varian katalog disimpan");
      closeVariantEditor();
      await loadVariants(variantEditor.pricing_group_id);
    } catch (err) {
      setError(errDetail(err) || "Gagal menyimpan metadata varian.");
    } finally {
      setBusy(false);
    }
  };

  const openGalleryCreate = () => {
    setGalleryEditor(emptyGalleryEditor());
  };

  const openGalleryEdit = (image) => {
    setGalleryEditor(emptyGalleryEditor(image));
  };

  const closeGalleryEditor = () => {
    setGalleryEditor(null);
    if (galleryFileRef.current) galleryFileRef.current.value = "";
  };

  const saveGalleryEditor = async () => {
    if (!canEdit || busy || !selectedGroupId || !selectedVariantId || !galleryEditor) return;
    setBusy(true);
    setError("");
    try {
      if (!galleryEditor.id) {
        if (!galleryEditor.file) {
          setError("Pilih file gambar terlebih dahulu.");
          setBusy(false);
          return;
        }
        const response = await api.uploadPublicCatalogVariantImage(
          selectedGroupId,
          selectedVariantId,
          galleryEditor.file,
          {
            role: galleryEditor.role,
            display_order: Number(galleryEditor.display_order) || 0,
            is_default: Boolean(galleryEditor.is_default),
            enabled: Boolean(galleryEditor.enabled),
          }
        );
        notify?.(response.message || "Gambar galeri ditambahkan");
        setGallery(response.gallery || null);
      } else {
        const response = await api.patchPublicCatalogVariantImage(
          selectedGroupId,
          selectedVariantId,
          galleryEditor.id,
          {
            role: galleryEditor.role,
            display_order: Number(galleryEditor.display_order) || 0,
            is_default: Boolean(galleryEditor.is_default),
            enabled: Boolean(galleryEditor.enabled),
          }
        );
        notify?.(response.message || "Gambar galeri diperbarui");
        setGallery(response.gallery || null);
      }
      closeGalleryEditor();
      await loadGallery(selectedGroupId, selectedVariantId);
    } catch (err) {
      setError(errDetail(err) || "Gagal menyimpan gambar galeri.");
    } finally {
      setBusy(false);
    }
  };

  const deleteGalleryImage = async (image) => {
    if (!canEdit || busy || !selectedGroupId || !selectedVariantId || !image?.id) return;
    if (!window.confirm("Hapus gambar galeri ini?")) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.deletePublicCatalogVariantImage(
        selectedGroupId,
        selectedVariantId,
        image.id
      );
      notify?.(response.message || "Gambar galeri dihapus");
      setGallery(response.gallery || null);
      await loadGallery(selectedGroupId, selectedVariantId);
    } catch (err) {
      setError(errDetail(err) || "Gagal menghapus gambar galeri.");
    } finally {
      setBusy(false);
    }
  };

  const galleryPreviewSrc = useMemo(() => {
    if (!gallery) return null;
    const images = gallery.images || [];
    const selected = images.find((image) => image.id === galleryPreviewId);
    if (selected?.file_id) {
      return api.publicCatalogVariantImageUrl(
        selectedGroupId,
        selectedVariantId,
        selected.id,
        selected.file_id
      );
    }
    const resolved = gallery.resolved || {};
    if (resolved.image?.file_id) {
      return api.publicCatalogVariantImageUrl(
        selectedGroupId,
        selectedVariantId,
        resolved.image.id,
        resolved.image.file_id
      );
    }
    if (resolved.source === "legacy_variant" || gallery.legacy_product_image?.available) {
      const fileId =
        resolved.legacy_product_image?.file_id || gallery.legacy_product_image?.file_id;
      return api.customerPricingProductImageUrl(
        selectedGroupId,
        selectedVariantId,
        fileId || ""
      );
    }
    if (resolved.source === "group_cover" && resolved.group_catalog_image?.file_id) {
      return api.customerPricingCatalogImageUrl(
        selectedGroupId,
        resolved.group_catalog_image.file_id
      );
    }
    return null;
  }, [gallery, galleryPreviewId, selectedGroupId, selectedVariantId]);

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

      {!loading && section === "kelompok" && (
        <section className="pcs-section" data-testid="pcs-groups">
          <div className="pcs-surface">
            <div className="pcs-section-head">
              <div>
                <h3>Kelompok Produk</h3>
                <p className="pcs-section-desc">
                  Presentasi katalog untuk setiap kelompok harga existing.
                </p>
              </div>
              <div className="pcs-toolbar">
                <label className="pcs-search">
                  <span className="sr-only">Cari kelompok</span>
                  <Search size={14} className="pcs-search-icon" aria-hidden="true" />
                  <input
                    value={groupQuery}
                    placeholder="Cari kelompok..."
                    onChange={(event) => setGroupQuery(event.target.value)}
                    data-testid="pcs-group-search"
                  />
                </label>
                <select
                  value={groupCategoryFilter}
                  onChange={(event) => setGroupCategoryFilter(event.target.value)}
                  data-testid="pcs-group-filter-category"
                  aria-label="Filter kategori"
                >
                  <option value="all">Semua kategori</option>
                  <option value="unset">Belum diatur</option>
                  {categories.map((category) => (
                    <option key={category.id} value={category.id}>
                      {category.name}{category.enabled ? "" : " (Nonaktif)"}
                    </option>
                  ))}
                </select>
                <select
                  value={groupVisibilityFilter}
                  onChange={(event) => setGroupVisibilityFilter(event.target.value)}
                  data-testid="pcs-group-filter-visibility"
                  aria-label="Filter tampilan katalog"
                >
                  <option value="all">Semua tampilan</option>
                  <option value="configured_visible">Tampil</option>
                  <option value="configured_hidden">Disembunyikan</option>
                  <option value="unset">Belum Diatur</option>
                </select>
              </div>
            </div>

            {filteredGroups.length === 0 ? (
              <div className="pcs-empty" data-testid="pcs-group-empty">
                <p>Tidak ada kelompok harga yang cocok.</p>
                <p className="pcs-empty-hint">
                  Kelompok berasal dari Harga Customer. Overlay katalog bersifat opsional.
                </p>
              </div>
            ) : (
              <div className="pcs-group-list" data-testid="pcs-group-list">
                {filteredGroups.map((group) => {
                  const catalog = group.catalog || {};
                  return (
                    <article
                      key={group.pricing_group_id}
                      className="pcs-group-row"
                      data-testid="pcs-group-row"
                      data-pricing-group-id={group.pricing_group_id}
                    >
                      <div className="pcs-group-main">
                        <div className="pcs-row-title">{group.group_name}</div>
                        <div className="pcs-row-slug">
                          Public: {catalog.public_name_display || group.group_name}
                          {!catalog.configured ? " · Belum dikonfigurasi" : ""}
                        </div>
                      </div>
                      <div className="pcs-group-meta">
                        <span>
                          {catalog.category_name
                            ? `${catalog.category_name}${catalog.category_enabled === false ? " (Nonaktif)" : ""}`
                            : "Belum diatur"}
                        </span>
                        <span>{catalog.slug || "Belum diatur"}</span>
                        <span>
                          Urutan{" "}
                          {catalog.display_order == null ? "—" : catalog.display_order}
                        </span>
                        <span
                          className={`pcs-badge ${group.pricing_enabled ? "is-on" : "is-off"}`}
                          data-testid="pcs-group-pricing-status"
                        >
                          Pricing: {group.pricing_enabled ? "Aktif" : "Nonaktif"}
                        </span>
                        <span
                          className={`pcs-badge ${
                            !catalog.configured
                              ? ""
                              : catalog.visible
                                ? "is-on"
                                : "is-off"
                          }`}
                          data-testid="pcs-group-catalog-status"
                        >
                          Katalog:{" "}
                          {!catalog.configured
                            ? "Belum Diatur"
                            : catalog.visible
                              ? "Tampil"
                              : "Disembunyikan"}
                        </span>
                      </div>
                      <div className="pcs-row-action">
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => openGroupEditor(group)}
                          data-testid="pcs-group-configure"
                        >
                          Atur
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </section>
      )}

      {!loading && section === "varian" && (
        <section className="pcs-section" data-testid="pcs-variants">
          <div className="pcs-surface">
            <div className="pcs-section-head">
              <div>
                <h3>Varian</h3>
                <p className="pcs-section-desc">
                  Metadata presentasi ukuran, warna, finishing, urutan, dan tampilan katalog.
                </p>
              </div>
              <div className="pcs-toolbar">
                <label className="pcs-field pcs-inline-select">
                  <span className="sr-only">Pilih Kelompok</span>
                  <select
                    value={selectedGroupId}
                    onChange={(event) => {
                      setSelectedGroupId(event.target.value);
                      setSelectedVariantId("");
                    }}
                    data-testid="pcs-variant-group-select"
                    aria-label="Pilih Kelompok"
                  >
                    <option value="">Pilih Kelompok</option>
                    {groups.map((group) => (
                      <option key={group.pricing_group_id} value={group.pricing_group_id}>
                        {group.group_name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="pcs-search">
                  <span className="sr-only">Cari varian</span>
                  <Search size={14} className="pcs-search-icon" aria-hidden="true" />
                  <input
                    value={variantQuery}
                    placeholder="Cari varian..."
                    onChange={(event) => setVariantQuery(event.target.value)}
                    data-testid="pcs-variant-search"
                  />
                </label>
                <select
                  value={variantVisibilityFilter}
                  onChange={(event) => setVariantVisibilityFilter(event.target.value)}
                  data-testid="pcs-variant-filter-visibility"
                  aria-label="Status katalog"
                >
                  <option value="all">Semua status</option>
                  <option value="visible">Tampil</option>
                  <option value="hidden">Disembunyikan</option>
                  <option value="unset">Belum Diatur</option>
                </select>
              </div>
            </div>

            {!selectedGroupId ? (
              <p className="muted small" data-testid="pcs-variant-empty-group">
                Pilih kelompok produk untuk melihat varian.
              </p>
            ) : filteredVariants.length === 0 ? (
              <div className="pcs-empty" data-testid="pcs-variant-empty">
                <p>Tidak ada varian yang cocok.</p>
              </div>
            ) : (
              <div className="pcs-variant-table-wrap" data-testid="pcs-variant-list">
                <table className="pcs-variant-table">
                  <thead>
                    <tr>
                      <th>Produk</th>
                      <th>Ukuran</th>
                      <th>Warna</th>
                      <th>Finishing</th>
                      <th>Urutan</th>
                      <th>Katalog</th>
                      <th>Aksi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredVariants.map((variant) => {
                      const catalog = variant.catalog || {};
                      return (
                        <tr
                          key={variant.accurate_item_id}
                          data-testid="pcs-variant-row"
                          data-accurate-item-id={variant.accurate_item_id}
                        >
                          <td>
                            <div
                              className="pcs-variant-name"
                              title={variant.display_name || ""}
                            >
                              {variant.display_name}
                            </div>
                            <div className="pcs-row-slug">
                              {variant.item_code || "—"}
                              {variant.unit ? ` · ${variant.unit}` : ""}
                              {!variant.is_active ? " · Nonaktif" : ""}
                            </div>
                          </td>
                          <td>{catalog.size || "Belum diatur"}</td>
                          <td>{catalog.color || "Belum diatur"}</td>
                          <td>{catalog.finishing || "Belum diatur"}</td>
                          <td>{catalog.display_order == null ? "—" : catalog.display_order}</td>
                          <td>
                            <span
                              className={`pcs-badge ${
                                !catalog.configured
                                  ? ""
                                  : catalog.visible
                                    ? "is-on"
                                    : "is-off"
                              }`}
                              data-testid="pcs-variant-catalog-status"
                            >
                              {!catalog.configured
                                ? "Belum Diatur"
                                : catalog.visible
                                  ? "Tampil"
                                  : "Disembunyikan"}
                            </span>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="ghost sm"
                              onClick={() => openVariantEditor(variant)}
                              data-testid="pcs-variant-configure"
                            >
                              Atur
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      {!loading && section === "galeri" && (
        <section className="pcs-section" data-testid="pcs-gallery">
          <div className="pcs-surface">
            <div className="pcs-section-head">
              <div>
                <h3>Galeri</h3>
                <p className="pcs-section-desc">
                  Multi-gambar per varian. Legacy image tetap terpisah dan read-only.
                </p>
              </div>
              <div className="pcs-toolbar">
                <select
                  value={selectedGroupId}
                  onChange={(event) => {
                    setSelectedGroupId(event.target.value);
                    setSelectedVariantId("");
                  }}
                  data-testid="pcs-gallery-group-select"
                  aria-label="Pilih Kelompok"
                >
                  <option value="">Pilih Kelompok</option>
                  {groups.map((group) => (
                    <option key={group.pricing_group_id} value={group.pricing_group_id}>
                      {group.group_name}
                    </option>
                  ))}
                </select>
                <select
                  value={selectedVariantId}
                  onChange={(event) => setSelectedVariantId(event.target.value)}
                  data-testid="pcs-gallery-variant-select"
                  aria-label="Pilih Varian"
                  disabled={!selectedGroupId}
                >
                  <option value="">Pilih Varian</option>
                  {variants.map((variant) => (
                    <option key={variant.accurate_item_id} value={variant.accurate_item_id}>
                      {variant.display_name}
                    </option>
                  ))}
                </select>
                {canEdit && selectedGroupId && selectedVariantId ? (
                  <button
                    type="button"
                    className="primary sm"
                    onClick={openGalleryCreate}
                    disabled={busy}
                    data-testid="pcs-gallery-add"
                  >
                    <Plus size={14} /> Tambah Gambar
                  </button>
                ) : null}
              </div>
            </div>

            {!selectedGroupId || !selectedVariantId ? (
              <p className="muted small" data-testid="pcs-gallery-empty-select">
                Pilih kelompok dan varian untuk mengelola galeri.
              </p>
            ) : (
              <div className="pcs-gallery-layout" data-testid="pcs-gallery-manager">
                <div className="pcs-gallery-preview" data-testid="pcs-gallery-preview">
                  {galleryPreviewSrc ? (
                    <img
                      src={galleryPreviewSrc}
                      alt=""
                      onError={(event) => {
                        event.currentTarget.style.display = "none";
                      }}
                    />
                  ) : (
                    <div className="pcs-gallery-placeholder">
                      <ImageIcon size={28} />
                      <span>Belum ada preview</span>
                    </div>
                  )}
                  <div className="pcs-gallery-meta-line" data-testid="pcs-gallery-legacy-status">
                    Gallery baru: {gallery?.gallery_count ?? 0} gambar
                    {" · "}
                    Gambar legacy:{" "}
                    {gallery?.legacy_product_image?.available ? "tersedia" : "tidak ada"}
                  </div>
                </div>
                <div className="pcs-gallery-thumbs" data-testid="pcs-gallery-thumbs">
                  {(gallery?.images || []).length === 0 ? (
                    <div className="pcs-empty" data-testid="pcs-gallery-empty">
                      <p>Belum ada gambar galeri baru.</p>
                      {gallery?.legacy_product_image?.available ? (
                        <p className="pcs-empty-hint">
                          Legacy image tersedia (read-only). Tidak dimigrasikan otomatis.
                        </p>
                      ) : null}
                    </div>
                  ) : (
                    (gallery.images || []).map((image) => (
                      <article
                        key={image.id}
                        className={`pcs-gallery-card${galleryPreviewId === image.id ? " is-selected" : ""}`}
                        data-testid="pcs-gallery-card"
                      >
                        <button
                          type="button"
                          className="pcs-gallery-thumb-btn"
                          onClick={() => setGalleryPreviewId(image.id)}
                          data-testid="pcs-gallery-thumb"
                        >
                          <img
                            src={api.publicCatalogVariantImageUrl(
                              selectedGroupId,
                              selectedVariantId,
                              image.id,
                              image.file_id
                            )}
                            alt=""
                            onError={(event) => {
                              event.currentTarget.replaceWith(
                                Object.assign(document.createElement("span"), {
                                  className: "pcs-gallery-thumb-fallback",
                                  textContent: "Gagal muat",
                                })
                              );
                            }}
                          />
                        </button>
                        <div className="pcs-gallery-card-meta">
                          <strong>{image.role_label || image.role}</strong>
                          <span>Urutan {image.display_order}</span>
                          {image.is_default ? (
                            <span className="pcs-badge is-on" data-testid="pcs-gallery-default-badge">
                              Default
                            </span>
                          ) : null}
                          <span className={`pcs-badge ${image.enabled ? "is-on" : "is-off"}`}>
                            {image.enabled ? "Aktif" : "Nonaktif"}
                          </span>
                        </div>
                        <div className="pcs-row-action">
                          <button
                            type="button"
                            className="ghost sm"
                            onClick={() => openGalleryEdit(image)}
                            data-testid="pcs-gallery-edit"
                          >
                            Atur
                          </button>
                          {canEdit ? (
                            <button
                              type="button"
                              className="ghost sm"
                              onClick={() => deleteGalleryImage(image)}
                              disabled={busy}
                              data-testid="pcs-gallery-delete"
                            >
                              Hapus
                            </button>
                          ) : null}
                        </div>
                      </article>
                    ))
                  )}
                  {gallery?.legacy_product_image?.available ? (
                    <article className="pcs-gallery-card is-legacy" data-testid="pcs-gallery-legacy">
                      <div className="pcs-gallery-thumb-btn pcs-legacy-thumb">
                        <img
                          src={api.customerPricingProductImageUrl(
                            selectedGroupId,
                            selectedVariantId,
                            gallery.legacy_product_image.file_id || ""
                          )}
                          alt=""
                        />
                      </div>
                      <div className="pcs-gallery-card-meta">
                        <strong>Legacy image</strong>
                        <span>Read-only</span>
                      </div>
                    </article>
                  ) : null}
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {!loading && section === "seo" && (
        <section className="pcs-section" data-testid="pcs-seo">
          <div className="pcs-surface">
            <div className="pcs-section-head">
              <div>
                <h3>URL &amp; SEO</h3>
                <p className="pcs-section-desc">
                  Metadata presentasi untuk kelompok terpilih. Belum menjadi rute publik.
                </p>
              </div>
            </div>
            <label className="pcs-field">
              Pilih Kelompok Produk
              <select
                value={selectedGroupId}
                onChange={(event) => setSelectedGroupId(event.target.value)}
                data-testid="pcs-seo-group-select"
              >
                <option value="">— Pilih kelompok —</option>
                {groups.map((group) => (
                  <option key={group.pricing_group_id} value={group.pricing_group_id}>
                    {group.group_name}
                  </option>
                ))}
              </select>
            </label>

            {!seoForm ? (
              <p className="muted small" data-testid="pcs-seo-empty">
                Pilih kelompok produk untuk mengatur slug dan SEO.
              </p>
            ) : (
              <div className="pcs-grid-2" data-testid="pcs-seo-form">
                <label className="pcs-field">Nama Publik
                  <input
                    value={seoForm.public_name}
                    onChange={(event) =>
                      setSeoForm((current) => ({
                        ...current,
                        public_name: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-seo-public-name"
                  />
                </label>
                <label className="pcs-field">Slug
                  <input
                    value={seoForm.slug}
                    onChange={(event) =>
                      setSeoForm((current) => ({ ...current, slug: event.target.value }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-seo-slug"
                  />
                </label>
                <label className="pcs-field pcs-span-2">Preview URL
                  <input
                    readOnly
                    value={previewProductUrl(seoForm.slug)}
                    data-testid="pcs-seo-preview-url"
                  />
                </label>
                <label className="pcs-field">Meta Title
                  <input
                    value={seoForm.meta_title}
                    onChange={(event) =>
                      setSeoForm((current) => ({
                        ...current,
                        meta_title: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-seo-meta-title"
                  />
                </label>
                <label className="pcs-field">Meta Description
                  <input
                    value={seoForm.meta_description}
                    onChange={(event) =>
                      setSeoForm((current) => ({
                        ...current,
                        meta_description: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-seo-meta-description"
                  />
                </label>
                {canEdit ? (
                  <div className="pcs-actions pcs-actions-end pcs-span-2">
                    <button
                      type="button"
                      className="primary sm"
                      disabled={busy}
                      onClick={() => saveGroupPresentation(seoForm)}
                      data-testid="pcs-seo-save"
                    >
                      Simpan SEO
                    </button>
                  </div>
                ) : null}
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

      {groupEditor && createPortal(
        <div
          className="drawer pcs-drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="pcs-group-drawer-title"
          data-testid="pcs-group-drawer"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeGroupEditor();
          }}
        >
          <div className="drawer-panel pcs-drawer-panel" onClick={(event) => event.stopPropagation()}>
            <header className="drawer-head">
              <div>
                <h3 id="pcs-group-drawer-title">Atur Kelompok Produk</h3>
                <p className="muted small" data-testid="pcs-group-drawer-context">
                  {groupEditor.group_name}
                </p>
              </div>
              <button
                ref={groupCloseRef}
                type="button"
                className="ghost sm"
                onClick={closeGroupEditor}
                data-testid="pcs-group-drawer-close"
                aria-label="Tutup"
              >
                <X size={16} />
              </button>
            </header>
            <div className="drawer-body pcs-drawer-body">
              <div className="pcs-drawer-fields">
                <div className="pcs-readonly-box" data-testid="pcs-group-readonly">
                  <div><strong>Nama Internal</strong>: {groupEditor.group_name}</div>
                  <div>
                    <strong>Status Pricing</strong>:{" "}
                    {groupEditor.pricing_enabled ? "Aktif" : "Nonaktif"}
                  </div>
                  <div>
                    <strong>Deskripsi Publik</strong>:{" "}
                    {groupEditor.public_description || "—"}
                  </div>
                  <div className="pcs-image-row" style={{ marginTop: 8 }}>
                    <div
                      className={`pcs-image-preview${
                        groupEditor.catalog_image?.file_id ? " has-image" : ""
                      }`}
                    >
                      {groupEditor.catalog_image?.file_id ? (
                        <img
                          src={api.customerPricingCatalogImageUrl(
                            groupEditor.pricing_group_id,
                            groupEditor.catalog_image.file_id
                          )}
                          alt=""
                        />
                      ) : (
                        <>
                          <ImageIcon size={22} />
                          <span className="pcs-image-placeholder-label">Tidak ada cover</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                <label className="pcs-field">Kategori
                  <select
                    value={groupEditor.category_id || ""}
                    onChange={(event) =>
                      setGroupEditor((current) => ({
                        ...current,
                        category_id: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-group-editor-category"
                  >
                    <option value="">Belum diatur</option>
                    {categories.map((category) => (
                      <option key={category.id} value={category.id}>
                        {category.name}{category.enabled ? "" : " (Nonaktif)"}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="pcs-field">Nama Publik
                  <input
                    value={groupEditor.public_name}
                    onChange={(event) =>
                      setGroupEditor((current) => ({
                        ...current,
                        public_name: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-group-editor-public-name"
                  />
                </label>
                <label className="pcs-field">Slug
                  <input
                    value={groupEditor.slug}
                    onChange={(event) =>
                      setGroupEditor((current) => ({
                        ...current,
                        slug: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-group-editor-slug"
                  />
                </label>
                <div className="pcs-drawer-pair">
                  <label className="pcs-field">Urutan Tampil
                    <input
                      type="number"
                      min={0}
                      value={groupEditor.display_order}
                      onChange={(event) =>
                        setGroupEditor((current) => ({
                          ...current,
                          display_order: event.target.value,
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-group-editor-order"
                    />
                  </label>
                  <label className="pcs-field">Tampilkan di Katalog
                    <select
                      value={groupEditor.visible ? "1" : "0"}
                      onChange={(event) =>
                        setGroupEditor((current) => ({
                          ...current,
                          visible: event.target.value === "1",
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-group-editor-visible"
                    >
                      <option value="1">Tampil</option>
                      <option value="0">Disembunyikan</option>
                    </select>
                  </label>
                </div>
              </div>
              <div className="pcs-drawer-footer" data-testid="pcs-group-drawer-footer">
                <button
                  type="button"
                  className="ghost sm"
                  onClick={closeGroupEditor}
                  disabled={busy}
                  data-testid="pcs-group-editor-cancel"
                >
                  Batal
                </button>
                {canEdit ? (
                  <button
                    type="button"
                    className="primary sm"
                    onClick={() => saveGroupPresentation(groupEditor, { closeDrawer: true })}
                    disabled={busy}
                    data-testid="pcs-group-editor-save"
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

      {variantEditor && createPortal(
        <div
          className="drawer pcs-drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="pcs-variant-drawer-title"
          data-testid="pcs-variant-drawer"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeVariantEditor();
          }}
        >
          <div className="drawer-panel pcs-drawer-panel" onClick={(event) => event.stopPropagation()}>
            <header className="drawer-head">
              <div>
                <h3 id="pcs-variant-drawer-title">Atur Varian</h3>
                <p className="muted small" data-testid="pcs-variant-drawer-context">
                  {variantEditor.display_name}
                </p>
              </div>
              <button
                ref={variantCloseRef}
                type="button"
                className="ghost sm"
                onClick={closeVariantEditor}
                data-testid="pcs-variant-drawer-close"
                aria-label="Tutup"
              >
                <X size={16} />
              </button>
            </header>
            <div className="drawer-body pcs-drawer-body">
              <div className="pcs-drawer-fields">
                <div className="pcs-readonly-box" data-testid="pcs-variant-readonly">
                  <div><strong>Produk</strong>: {variantEditor.display_name}</div>
                  <div><strong>Kode</strong>: {variantEditor.item_code || "—"}</div>
                  <div>
                    <strong>Status Master</strong>:{" "}
                    {variantEditor.is_active ? "Aktif" : "Nonaktif"}
                  </div>
                </div>
                <label className="pcs-field">Ukuran
                  <input
                    value={variantEditor.size}
                    onChange={(event) =>
                      setVariantEditor((current) => ({ ...current, size: event.target.value }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-variant-editor-size"
                  />
                </label>
                <label className="pcs-field">Warna
                  <input
                    value={variantEditor.color}
                    onChange={(event) =>
                      setVariantEditor((current) => ({ ...current, color: event.target.value }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-variant-editor-color"
                  />
                </label>
                <label className="pcs-field">Finishing
                  <input
                    value={variantEditor.finishing}
                    onChange={(event) =>
                      setVariantEditor((current) => ({
                        ...current,
                        finishing: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-variant-editor-finishing"
                  />
                </label>
                <div className="pcs-drawer-pair">
                  <label className="pcs-field">Urutan Tampil
                    <input
                      type="number"
                      min={0}
                      value={variantEditor.display_order}
                      onChange={(event) =>
                        setVariantEditor((current) => ({
                          ...current,
                          display_order: event.target.value,
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-variant-editor-order"
                    />
                  </label>
                  <label className="pcs-field">Tampilkan di Katalog
                    <select
                      value={variantEditor.visible ? "1" : "0"}
                      onChange={(event) =>
                        setVariantEditor((current) => ({
                          ...current,
                          visible: event.target.value === "1",
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-variant-editor-visible"
                    >
                      <option value="1">Tampil</option>
                      <option value="0">Disembunyikan</option>
                    </select>
                  </label>
                </div>
              </div>
              <div className="pcs-drawer-footer" data-testid="pcs-variant-drawer-footer">
                <button
                  type="button"
                  className="ghost sm"
                  onClick={closeVariantEditor}
                  disabled={busy}
                  data-testid="pcs-variant-editor-cancel"
                >
                  Batal
                </button>
                {canEdit ? (
                  <button
                    type="button"
                    className="primary sm"
                    onClick={saveVariant}
                    disabled={busy}
                    data-testid="pcs-variant-editor-save"
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

      {galleryEditor && createPortal(
        <div
          className="drawer pcs-drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="pcs-gallery-drawer-title"
          data-testid="pcs-gallery-drawer"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeGalleryEditor();
          }}
        >
          <div className="drawer-panel pcs-drawer-panel" onClick={(event) => event.stopPropagation()}>
            <header className="drawer-head">
              <div>
                <h3 id="pcs-gallery-drawer-title">
                  {galleryEditor.id ? "Atur Gambar" : "Tambah Gambar"}
                </h3>
              </div>
              <button
                ref={galleryCloseRef}
                type="button"
                className="ghost sm"
                onClick={closeGalleryEditor}
                data-testid="pcs-gallery-drawer-close"
                aria-label="Tutup"
              >
                <X size={16} />
              </button>
            </header>
            <div className="drawer-body pcs-drawer-body">
              <div className="pcs-drawer-fields">
                {!galleryEditor.id ? (
                  <label className="pcs-field">File
                    <input
                      ref={galleryFileRef}
                      type="file"
                      accept={IMAGE_ACCEPT}
                      onChange={(event) =>
                        setGalleryEditor((current) => ({
                          ...current,
                          file: event.target.files?.[0] || null,
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-gallery-editor-file"
                    />
                  </label>
                ) : null}
                <label className="pcs-field">Role
                  <select
                    value={galleryEditor.role}
                    onChange={(event) =>
                      setGalleryEditor((current) => ({
                        ...current,
                        role: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-gallery-editor-role"
                  >
                    {IMAGE_ROLE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="pcs-field">Urutan
                  <input
                    type="number"
                    min={0}
                    value={galleryEditor.display_order}
                    onChange={(event) =>
                      setGalleryEditor((current) => ({
                        ...current,
                        display_order: event.target.value,
                      }))
                    }
                    disabled={!canEdit || busy}
                    data-testid="pcs-gallery-editor-order"
                  />
                </label>
                <div className="pcs-drawer-pair">
                  <label className="pcs-field">Jadikan Default
                    <select
                      value={galleryEditor.is_default ? "1" : "0"}
                      onChange={(event) =>
                        setGalleryEditor((current) => ({
                          ...current,
                          is_default: event.target.value === "1",
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-gallery-editor-default"
                    >
                      <option value="1">Ya</option>
                      <option value="0">Tidak</option>
                    </select>
                  </label>
                  <label className="pcs-field">Status
                    <select
                      value={galleryEditor.enabled ? "1" : "0"}
                      onChange={(event) =>
                        setGalleryEditor((current) => ({
                          ...current,
                          enabled: event.target.value === "1",
                        }))
                      }
                      disabled={!canEdit || busy}
                      data-testid="pcs-gallery-editor-enabled"
                    >
                      <option value="1">Aktif</option>
                      <option value="0">Nonaktif</option>
                    </select>
                  </label>
                </div>
              </div>
              <div className="pcs-drawer-footer" data-testid="pcs-gallery-drawer-footer">
                <button
                  type="button"
                  className="ghost sm"
                  onClick={closeGalleryEditor}
                  disabled={busy}
                  data-testid="pcs-gallery-editor-cancel"
                >
                  Batal
                </button>
                {canEdit ? (
                  <button
                    type="button"
                    className="primary sm"
                    onClick={saveGalleryEditor}
                    disabled={busy || (!galleryEditor.id && !galleryEditor.file)}
                    data-testid="pcs-gallery-editor-save"
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
