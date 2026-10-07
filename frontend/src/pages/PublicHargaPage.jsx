import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  MessageCircle,
  PackageOpen,
  Search,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import BrandMark from "../components/BrandMark";
import api, { publicApiUrl } from "../lib/api";
import { formatApiError } from "../lib/errors";
import "./PublicHargaPage.css";

const formatNumber = (value) =>
  Number(value || 0).toLocaleString("id-ID", { maximumFractionDigits: 0 });

const formatMoney = (value) => `Rp ${formatNumber(value)}`;

const digitsOnlyPhone = (value) => String(value || "").replace(/\D/g, "");

function productPath(group) {
  if (group?.slug) return `/produk/${encodeURIComponent(group.slug)}`;
  return `/harga/${encodeURIComponent(group?.public_id || "")}`;
}

function buildWhatsAppUrl(phone, message) {
  const digits = digitsOnlyPhone(phone);
  if (!digits) return "";
  return `https://wa.me/${digits}?text=${encodeURIComponent(message)}`;
}

function ProductVisual({ imageUrl, name, large = false, testid }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [imageUrl]);
  const source = imageUrl ? publicApiUrl(imageUrl) : "";
  return (
    <div
      className={`ph-product-visual ${large ? "is-large" : ""}`}
      data-testid={testid || (large ? "harga-detail-image" : "harga-card-image")}
    >
      {source && !failed ? (
        <img src={source} alt={name || "Produk Eternal Packaging"} onError={() => setFailed(true)} />
      ) : (
        <div className="ph-image-placeholder" data-testid="harga-image-placeholder">
          <PackageOpen aria-hidden="true" />
          <span>ETERNAL</span>
        </div>
      )}
    </div>
  );
}

function PublicHeader({ phone, search, onSearchChange, showSearch = true }) {
  const wa = phone
    ? buildWhatsAppUrl(phone, "Halo Eternal Packaging, saya ingin bertanya tentang katalog produk.")
    : "";
  return (
    <header className="ph-header">
      <Link className="ph-brand" to="/harga" aria-label="Eternal Packaging">
        <BrandMark compact />
      </Link>
      <nav className="ph-nav" aria-label="Navigasi katalog">
        <a href="#katalog">Katalog</a>
        {wa ? <a href={wa} target="_blank" rel="noreferrer">Hubungi Kami</a> : null}
      </nav>
      {showSearch ? (
        <label className="ph-header-search">
          <Search size={16} aria-hidden="true" />
          <span className="ph-sr-only">Cari produk</span>
          <input
            type="search"
            value={search}
            onChange={(event) => onSearchChange?.(event.target.value)}
            placeholder="Cari produk..."
            data-testid="harga-catalog-search"
          />
        </label>
      ) : <span className="ph-header-spacer" />}
      {wa ? (
        <a className="ph-wa-btn" href={wa} target="_blank" rel="noreferrer" data-testid="harga-header-whatsapp">
          <MessageCircle size={16} aria-hidden="true" />
          Tanya via WhatsApp
        </a>
      ) : null}
    </header>
  );
}

function CatalogPage() {
  const navigate = useNavigate();
  const [settings, setSettings] = useState(null);
  const [categories, setCategories] = useState([]);
  const [groups, setGroups] = useState([]);
  const [search, setSearch] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [disabledMessage, setDisabledMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    setDisabledMessage("");
    api.publicCatalogHome()
      .then((data) => {
        if (cancelled) return;
        setSettings(data?.settings || null);
        setCategories(Array.isArray(data?.categories) ? data.categories : []);
        setGroups(Array.isArray(data?.groups) ? data.groups : []);
        if (data?.message) setDisabledMessage(data.message);
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadError(formatApiError(error, "Katalog produk tidak dapat dimuat."));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!settings) return undefined;
    const title = settings.hero_title || settings.catalog_name || "Katalog Eternal Packaging";
    document.title = `${title} · Eternal Packaging`;
    return undefined;
  }, [settings]);

  const filteredGroups = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("id-ID");
    return groups.filter((group) => {
      if (categoryId && group.category?.id !== categoryId) return false;
      if (!query) return true;
      const hay = [
        group.name,
        group.public_description,
        group.search_text,
        group.category?.name,
        group.size_summary,
        ...(group.variants || []).flatMap((variant) => [
          variant.display_name,
          variant.size,
          variant.color,
          variant.finishing,
        ]),
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase("id-ID");
      return hay.includes(query);
    });
  }, [groups, search, categoryId]);

  const phone = settings?.public_whatsapp_phone || "";
  const heroTitle = settings?.hero_title || settings?.catalog_name || "Kemasan Berkualitas untuk Bisnis Anda";
  const heroSubtitle = settings?.hero_subtitle
    || "Temukan produk kemasan dengan harga transparan berdasarkan jumlah pesanan.";

  return (
    <main className="public-harga-page ph-v2" data-testid="public-harga-page">
      <PublicHeader phone={phone} search={search} onSearchChange={setSearch} />

      <section className="ph-hero" aria-labelledby="ph-hero-title">
        <div className="ph-hero-copy">
          <p className="ph-eyebrow">KATALOG PRODUK</p>
          <h1 id="ph-hero-title">{heroTitle}</h1>
          <p>{heroSubtitle}</p>
          <label className="ph-hero-search">
            <Search size={18} aria-hidden="true" />
            <span className="ph-sr-only">Cari produk katalog</span>
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Cari cup, bowl, plastik..."
              data-testid="harga-hero-search"
            />
          </label>
          <ul className="ph-hero-points">
            <li>Harga Transparan</li>
            <li>Banyak Pilihan Varian</li>
            <li>Harga Berdasarkan Jumlah</li>
            <li>Mudah Hubungi Tim</li>
          </ul>
        </div>
        <div className="ph-hero-panel" aria-hidden="true">
          <PackageOpen size={40} />
          <strong>Solusi Kemasan Anda</strong>
        </div>
      </section>

      {categories.length > 0 && (
        <section className="ph-section" aria-labelledby="ph-category-title">
          <div className="ph-section-head">
            <h2 id="ph-category-title">Pilih Kategori Produk</h2>
          </div>
          <div className="ph-category-grid" data-testid="harga-category-grid">
            <button
              type="button"
              className={`ph-category-card ${!categoryId ? "is-active" : ""}`}
              onClick={() => setCategoryId("")}
              aria-pressed={!categoryId}
              data-testid="harga-category-all"
            >
              <span className="ph-category-visual"><PackageOpen size={22} /></span>
              <strong>Semua</strong>
            </button>
            {categories.map((category) => (
              <button
                type="button"
                key={category.id}
                className={`ph-category-card ${categoryId === category.id ? "is-active" : ""}`}
                onClick={() => setCategoryId(category.id)}
                aria-pressed={categoryId === category.id}
                data-testid={`harga-category-${category.id}`}
              >
                <span className="ph-category-visual">
                  {category.image_url ? (
                    <img src={publicApiUrl(category.image_url)} alt="" />
                  ) : (
                    <PackageOpen size={22} aria-hidden="true" />
                  )}
                </span>
                <strong>{category.name}</strong>
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="ph-section" id="katalog" aria-labelledby="harga-catalog-title">
        <div className="ph-section-head">
          <div>
            <p className="ph-kicker">Produk Tersedia</p>
            <h2 id="harga-catalog-title">Temukan produk yang Anda butuhkan</h2>
          </div>
        </div>

        {loading && (
          <div className="ph-skeleton-grid" data-testid="public-harga-loading" aria-busy="true">
            {[1, 2, 3, 4].map((key) => <div key={key} className="ph-skeleton-card" />)}
          </div>
        )}
        {!loading && loadError && (
          <div className="ph-public-state is-error" role="alert" data-testid="public-harga-error">
            {loadError}
          </div>
        )}
        {!loading && !loadError && disabledMessage && (
          <div className="ph-public-state" data-testid="public-harga-disabled">
            <PackageOpen aria-hidden="true" />
            <h2>{disabledMessage}</h2>
          </div>
        )}
        {!loading && !loadError && !disabledMessage && groups.length === 0 && (
          <div className="ph-public-state" data-testid="public-harga-empty">
            <PackageOpen aria-hidden="true" />
            <h2>Produk tidak tersedia.</h2>
            <p>Katalog harga belum tersedia saat ini.</p>
          </div>
        )}
        {!loading && !loadError && !disabledMessage && groups.length > 0 && (
          <>
            <div className="ph-catalog-grid" data-testid="public-harga-groups">
              {filteredGroups.map((group) => (
                <article className="ph-product-card" key={group.public_id}>
                  <button
                    type="button"
                    onClick={() => navigate(productPath(group))}
                    data-testid={`harga-group-${group.public_id}`}
                    aria-label={`Lihat harga ${group.name}`}
                  >
                    <ProductVisual imageUrl={group.image_url} name={group.name} />
                    <span className="ph-card-content">
                      <span className="ph-card-title">{group.name}</span>
                      {group.size_summary ? (
                        <span className="ph-card-meta">{group.size_summary}</span>
                      ) : null}
                      {group.min_unit_price != null ? (
                        <span className="ph-card-price" data-testid={`harga-min-price-${group.public_id}`}>
                          Mulai dari {formatMoney(group.min_unit_price)} / pcs
                        </span>
                      ) : null}
                      <span className="ph-card-variants">
                        {(group.variant_count ?? group.variants?.length ?? 0)} varian
                      </span>
                      <span className="ph-card-cta">
                        Lihat Harga <ArrowRight size={16} aria-hidden="true" />
                      </span>
                    </span>
                  </button>
                </article>
              ))}
            </div>
            {filteredGroups.length === 0 && (
              <div className="ph-public-state is-compact">Produk tidak ditemukan.</div>
            )}
          </>
        )}
      </section>

      {phone ? (
        <section className="ph-help-banner">
          <div>
            <h2>Butuh bantuan memilih kemasan?</h2>
            <p>Tim Eternal siap membantu via WhatsApp.</p>
          </div>
          <a
            className="ph-wa-btn"
            href={buildWhatsAppUrl(phone, "Halo Eternal Packaging, saya butuh bantuan memilih kemasan.")}
            target="_blank"
            rel="noreferrer"
            data-testid="harga-help-whatsapp"
          >
            <MessageCircle size={16} aria-hidden="true" />
            Tanya via WhatsApp
          </a>
        </section>
      ) : null}

      <footer className="ph-footer">Eternal Packaging · Katalog produk terkini</footer>
    </main>
  );
}

function uniqueAxisValues(variants, key) {
  const values = [];
  const seen = new Set();
  variants.forEach((variant) => {
    const value = String(variant?.[key] || "").trim();
    if (!value) return;
    const token = value.toLocaleLowerCase("id-ID");
    if (seen.has(token)) return;
    seen.add(token);
    values.push(value);
  });
  return values;
}

function DetailPage({ groupPublicId, slug }) {
  const [group, setGroup] = useState(null);
  const [selectedVariantId, setSelectedVariantId] = useState("");
  const [sizeFilter, setSizeFilter] = useState("");
  const [colorFilter, setColorFilter] = useState("");
  const [finishingFilter, setFinishingFilter] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    setGroup(null);
    setSelectedVariantId("");
    setSizeFilter("");
    setColorFilter("");
    setFinishingFilter("");
    setPreviewUrl("");
    const request = slug
      ? api.publicCustomerPricingGroupBySlug(slug)
      : api.publicCustomerPricingGroup(groupPublicId);
    request
      .then((data) => {
        if (cancelled) return;
        const detail = data?.group || null;
        setGroup(detail);
        const first = detail?.variants?.[0] || null;
        // Neutral start: no forced variant unless only one exists.
        if (detail?.variants?.length === 1 && first) {
          setSelectedVariantId(first.public_variant_id);
          setSizeFilter(first.size || "");
          setColorFilter(first.color || "");
          setFinishingFilter(first.finishing || "");
          setPreviewUrl(first.image_url || detail.image_url || "");
        } else {
          setPreviewUrl(detail?.image_url || "");
        }
        document.title = detail?.meta_title
          || (detail?.name ? `${detail.name} · Eternal Packaging` : "Eternal Packaging");
        if (detail?.meta_description) {
          let meta = document.querySelector('meta[name="description"]');
          if (!meta) {
            meta = document.createElement("meta");
            meta.setAttribute("name", "description");
            document.head.appendChild(meta);
          }
          meta.setAttribute("content", detail.meta_description);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadError(formatApiError(error, "Produk tidak tersedia."));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [groupPublicId, slug]);

  const variants = useMemo(() => group?.variants || [], [group]);
  const sizeOptions = useMemo(() => uniqueAxisValues(variants, "size"), [variants]);
  const colorOptions = useMemo(() => uniqueAxisValues(variants, "color"), [variants]);
  const finishingOptions = useMemo(() => uniqueAxisValues(variants, "finishing"), [variants]);
  const structured = sizeOptions.length + colorOptions.length + finishingOptions.length > 0;

  const matchingVariants = useMemo(() => {
    return variants.filter((variant) => {
      if (sizeFilter && (variant.size || "") !== sizeFilter) return false;
      if (colorFilter && (variant.color || "") !== colorFilter) return false;
      if (finishingFilter && (variant.finishing || "") !== finishingFilter) return false;
      return true;
    });
  }, [variants, sizeFilter, colorFilter, finishingFilter]);

  useEffect(() => {
    if (!structured) return;
    if (matchingVariants.length === 1) {
      const only = matchingVariants[0];
      if (only.public_variant_id !== selectedVariantId) {
        setSelectedVariantId(only.public_variant_id);
        setPreviewUrl(only.image_url || group?.image_url || "");
      }
    } else if (matchingVariants.length === 0) {
      setSelectedVariantId("");
    } else if (
      selectedVariantId
      && !matchingVariants.some((variant) => variant.public_variant_id === selectedVariantId)
    ) {
      setSelectedVariantId("");
    }
  }, [structured, matchingVariants, selectedVariantId, group]);

  const selectedVariant = variants.find(
    (variant) => variant.public_variant_id === selectedVariantId
  ) || null;

  const galleryImages = selectedVariant?.images?.length
    ? selectedVariant.images
    : (selectedVariant?.image_url
      ? [{ id: "legacy", url: selectedVariant.image_url, role_label: "Utama", is_default: true }]
      : (group?.image_url
        ? [{ id: "cover", url: group.image_url, role_label: "Utama", is_default: true }]
        : []));

  const phone = group?.contact?.public_whatsapp_phone || "";
  const publicUrl = typeof window !== "undefined"
    ? `${window.location.origin}${slug ? `/produk/${slug}` : `/harga/${groupPublicId}`}`
    : productPath(group || { public_id: groupPublicId, slug });
  const waMessage = [
    "Halo Eternal Packaging,",
    `saya tertarik dengan ${group?.name || "produk katalog"}.`,
    selectedVariant ? `Varian: ${selectedVariant.display_name}.` : "",
    selectedVariant?.size ? `Ukuran: ${selectedVariant.size}.` : "",
    selectedVariant?.color ? `Warna: ${selectedVariant.color}.` : "",
    selectedVariant?.finishing ? `Finishing: ${selectedVariant.finishing}.` : "",
    `Link: ${publicUrl}`,
  ].filter(Boolean).join(" ");
  const waUrl = phone ? buildWhatsAppUrl(phone, waMessage) : "";

  const selectFlatVariant = (publicVariantId) => {
    const variant = variants.find((item) => item.public_variant_id === publicVariantId);
    setSelectedVariantId(publicVariantId);
    setSizeFilter(variant?.size || "");
    setColorFilter(variant?.color || "");
    setFinishingFilter(variant?.finishing || "");
    setPreviewUrl(variant?.image_url || group?.image_url || "");
  };

  const currentPrice = selectedVariant?.tiers?.[0]?.unit_price ?? group?.min_unit_price;

  return (
    <main className="public-harga-page ph-v2" data-testid="public-harga-detail-page">
      <PublicHeader phone={phone} showSearch={false} />
      <div className="ph-detail-shell">
        <Link className="ph-back-link" to="/harga">
          <ArrowLeft size={18} aria-hidden="true" />
          Kembali ke Katalog
        </Link>

        {loading && (
          <div className="ph-skeleton-detail" data-testid="public-harga-detail-loading" aria-busy="true" />
        )}
        {!loading && loadError && (
          <div className="ph-public-state is-error" role="alert" data-testid="public-harga-not-found">
            {loadError}
          </div>
        )}
        {!loading && !loadError && group && (
          <div className="ph-detail-layout" data-testid="public-harga-detail">
            <section className="ph-gallery" aria-label="Galeri produk">
              <ProductVisual
                imageUrl={previewUrl || group.image_url}
                name={selectedVariant?.display_name || group.name}
                large
              />
              {galleryImages.length > 0 && (
                <div className="ph-thumbs" data-testid="harga-gallery-thumbs">
                  {galleryImages.map((image) => (
                    <button
                      key={image.id}
                      type="button"
                      className={`ph-thumb ${previewUrl === image.url ? "is-active" : ""}`}
                      onClick={() => setPreviewUrl(image.url)}
                      aria-pressed={previewUrl === image.url}
                      aria-label={`Lihat gambar ${image.role_label || "produk"}`}
                      data-testid={`harga-thumb-${image.id}`}
                    >
                      <img src={publicApiUrl(image.url)} alt="" />
                    </button>
                  ))}
                </div>
              )}
            </section>

            <section className="ph-detail-info">
              <p className="ph-brand-label">ETERNAL PACKAGING</p>
              <h1 data-testid="harga-detail-title">{group.name}</h1>
              {group.public_description ? (
                <p className="ph-detail-desc">{group.public_description}</p>
              ) : null}
              <div className="ph-price-block" data-testid="harga-current-price">
                {selectedVariant ? "Harga" : "Mulai dari"}{" "}
                <strong>{currentPrice != null ? `${formatMoney(currentPrice)} / pcs` : "—"}</strong>
              </div>

              {structured ? (
                <div className="ph-selectors" data-testid="harga-structured-selectors">
                  {sizeOptions.length > 0 && (
                    <fieldset>
                      <legend>Pilih Ukuran</legend>
                      <div className="ph-chip-row">
                        {sizeOptions.map((size) => (
                          <button
                            key={size}
                            type="button"
                            className={`ph-chip ${sizeFilter === size ? "is-active" : ""}`}
                            aria-pressed={sizeFilter === size}
                            onClick={() => setSizeFilter(size)}
                            data-testid={`harga-size-${size}`}
                          >
                            {size}
                          </button>
                        ))}
                      </div>
                    </fieldset>
                  )}
                  {colorOptions.length > 0 && (
                    <fieldset>
                      <legend>Pilih Warna</legend>
                      <div className="ph-chip-row">
                        {colorOptions.map((color) => {
                          const possible = variants.some((variant) => (
                            (!sizeFilter || variant.size === sizeFilter)
                            && variant.color === color
                            && (!finishingFilter || variant.finishing === finishingFilter)
                          ));
                          return (
                            <button
                              key={color}
                              type="button"
                              className={`ph-chip ${colorFilter === color ? "is-active" : ""}`}
                              aria-pressed={colorFilter === color}
                              disabled={!possible}
                              onClick={() => setColorFilter(color)}
                              data-testid={`harga-color-${color}`}
                            >
                              {color}
                            </button>
                          );
                        })}
                      </div>
                    </fieldset>
                  )}
                  {finishingOptions.length > 0 && (
                    <fieldset>
                      <legend>Pilih Finishing</legend>
                      <div className="ph-chip-row">
                        {finishingOptions.map((finishing) => {
                          const possible = variants.some((variant) => (
                            (!sizeFilter || variant.size === sizeFilter)
                            && (!colorFilter || variant.color === colorFilter)
                            && variant.finishing === finishing
                          ));
                          return (
                            <button
                              key={finishing}
                              type="button"
                              className={`ph-chip ${finishingFilter === finishing ? "is-active" : ""}`}
                              aria-pressed={finishingFilter === finishing}
                              disabled={!possible}
                              onClick={() => setFinishingFilter(finishing)}
                              data-testid={`harga-finishing-${finishing}`}
                            >
                              {finishing}
                            </button>
                          );
                        })}
                      </div>
                    </fieldset>
                  )}
                </div>
              ) : (
                <div className="ph-selectors" data-testid="harga-flat-variants">
                  <fieldset>
                    <legend>Pilihan Varian</legend>
                    <div className="ph-chip-row">
                      {variants.map((variant) => (
                        <button
                          key={variant.public_variant_id}
                          type="button"
                          className={`ph-chip ${selectedVariantId === variant.public_variant_id ? "is-active" : ""}`}
                          aria-pressed={selectedVariantId === variant.public_variant_id}
                          onClick={() => selectFlatVariant(variant.public_variant_id)}
                          data-testid={`harga-variant-${variant.public_variant_id}`}
                        >
                          {variant.display_name}
                        </button>
                      ))}
                    </div>
                  </fieldset>
                </div>
              )}

              {selectedVariant && (
                <div className="ph-tier-block" data-testid="harga-tier-block">
                  <div className="ph-tier-head">
                    <strong>Harga: {selectedVariant.display_name}</strong>
                    <span data-testid="harga-moq">
                      Minimum Order: {formatNumber(selectedVariant.minimum_quantity)} {selectedVariant.unit || "pcs"}
                    </span>
                  </div>
                  <div className="ph-tier-grid">
                    {selectedVariant.tiers.map((tier) => (
                      <div
                        key={`${tier.min_qty}-${tier.max_qty}`}
                        className="ph-tier-card"
                        data-testid="harga-tier-row"
                      >
                        <span>
                          {tier.max_qty == null
                            ? `${formatNumber(tier.min_qty)}+`
                            : `${formatNumber(tier.min_qty)}–${formatNumber(tier.max_qty)}`} pcs
                        </span>
                        <strong>{formatMoney(tier.unit_price)} / pcs</strong>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {waUrl ? (
                <a
                  className="ph-wa-btn ph-wa-primary"
                  href={waUrl}
                  target="_blank"
                  rel="noreferrer"
                  data-testid="harga-detail-whatsapp"
                >
                  <MessageCircle size={18} aria-hidden="true" />
                  Pesan via WhatsApp
                </a>
              ) : null}
            </section>
          </div>
        )}
      </div>
      <footer className="ph-footer">Eternal Packaging · Katalog produk terkini</footer>
    </main>
  );
}

export default function PublicHargaPage() {
  const { groupPublicId, slug } = useParams();
  if (slug) return <DetailPage slug={slug} />;
  if (groupPublicId) return <DetailPage groupPublicId={groupPublicId} />;
  return <CatalogPage />;
}
