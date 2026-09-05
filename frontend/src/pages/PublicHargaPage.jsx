import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Calculator,
  PackageOpen,
  Search,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import BrandMark from "../components/BrandMark";
import api, { publicApiUrl } from "../lib/api";
import { formatApiError } from "../lib/errors";
import "./PublicHargaPage.css";

const formatNumber = (value) =>
  Number(value || 0).toLocaleString("id-ID", { maximumFractionDigits: 2 });

const formatMoney = (value) => `Rp ${formatNumber(value)}`;

const tierRange = (tier) => {
  if (!tier) return "—";
  return tier.max_qty == null
    ? `${formatNumber(tier.min_qty)}+`
    : `${formatNumber(tier.min_qty)}–${formatNumber(tier.max_qty)}`;
};

function PublicHeader() {
  return (
    <header className="ph-header">
      <Link className="ph-brand" to="/harga" aria-label="Eternal Packaging">
        <BrandMark compact />
        <span>ETERNAL PACKAGING</span>
      </Link>
      <span className="ph-header-label">Harga Produk</span>
    </header>
  );
}

function ProductVisual({ imageUrl, name, large = false }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [imageUrl]);
  const source = imageUrl ? publicApiUrl(imageUrl) : "";

  return (
    <div
      className={`ph-product-visual ${large ? "is-large" : ""}`}
      data-testid={large ? "harga-detail-image" : "harga-card-image"}
    >
      {source && !failed ? (
        <img src={source} alt={name} onError={() => setFailed(true)} />
      ) : (
        <div className="ph-image-placeholder" data-testid="harga-image-placeholder">
          <PackageOpen aria-hidden="true" />
          <span>ETERNAL</span>
        </div>
      )}
    </div>
  );
}

function VariantVisual({ imageUrl, name, size = "md" }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [imageUrl]);
  const source = imageUrl ? publicApiUrl(imageUrl) : "";

  return (
    <div
      className={`ph-variant-visual is-${size}`}
      data-testid="harga-variant-visual"
    >
      {source && !failed ? (
        <img src={source} alt={name} onError={() => setFailed(true)} />
      ) : (
        <div className="ph-variant-placeholder" data-testid="harga-variant-placeholder">
          <PackageOpen size={size === "sm" ? 14 : size === "lg" ? 22 : 18} aria-hidden="true" />
          <span>FOTO</span>
        </div>
      )}
    </div>
  );
}

function CatalogPage() {
  const navigate = useNavigate();
  const [groups, setGroups] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    api.publicCustomerPricingGroups()
      .then((data) => {
        if (!cancelled) {
          setGroups(Array.isArray(data?.groups) ? data.groups : []);
        }
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

  const filteredGroups = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("id-ID");
    if (!query) return groups;
    return groups.filter((group) => (
      group.name.toLocaleLowerCase("id-ID").includes(query)
      || (group.public_description || "").toLocaleLowerCase("id-ID").includes(query)
      || (group.variants || []).some((variant) => (
        variant.display_name.toLocaleLowerCase("id-ID").includes(query)
      ))
    ));
  }, [groups, search]);

  return (
    <main className="public-harga-page" data-testid="public-harga-page">
      <PublicHeader />
      <section className="ph-catalog-hero">
        <p className="ph-eyebrow">KATALOG PRODUK</p>
        <h1>Cek Harga Produk</h1>
        <p>Pilih produk untuk melihat varian dan harga berdasarkan jumlah pesanan.</p>
      </section>

      <section className="ph-catalog-shell" aria-labelledby="harga-catalog-title">
        <div className="ph-catalog-toolbar">
          <div>
            <p className="ph-kicker">Pilihan Kemasan</p>
            <h2 id="harga-catalog-title">Temukan produk yang Anda butuhkan</h2>
          </div>
          {groups.length > 4 && (
            <label className="ph-search">
              <Search size={18} aria-hidden="true" />
              <span className="ph-sr-only">Cari produk</span>
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Cari produk..."
                data-testid="harga-catalog-search"
              />
            </label>
          )}
        </div>

        {loading && (
          <div className="ph-public-state" data-testid="public-harga-loading">
            Loading katalog...
          </div>
        )}
        {!loading && loadError && (
          <div className="ph-public-state is-error" role="alert" data-testid="public-harga-error">
            {loadError}
          </div>
        )}
        {!loading && !loadError && groups.length === 0 && (
          <div className="ph-public-state" data-testid="public-harga-empty">
            <PackageOpen aria-hidden="true" />
            <h2>Produk tidak tersedia.</h2>
            <p>Katalog harga belum tersedia saat ini.</p>
          </div>
        )}
        {!loading && !loadError && groups.length > 0 && (
          <>
            <div className="ph-catalog-grid" data-testid="public-harga-groups">
              {filteredGroups.map((group) => (
                <article className="ph-product-card" key={group.public_id}>
                  <button
                    type="button"
                    onClick={() => navigate(`/harga/${encodeURIComponent(group.public_id)}`)}
                    data-testid={`harga-group-${group.public_id}`}
                    aria-label={`Lihat harga ${group.name}`}
                  >
                    <ProductVisual imageUrl={group.image_url} name={group.name} />
                    <span className="ph-card-content">
                      <span className="ph-card-title">{group.name}</span>
                      <span className="ph-card-variants">
                        {group.variants?.length || 0} varian
                      </span>
                      {group.variants && group.variants.length > 0 && (
                        <div className="ph-card-variant-strip" data-testid={`harga-variants-strip-${group.public_id}`}>
                          {group.variants.slice(0, 4).map((variant) => (
                            <div
                              key={variant.public_variant_id}
                              className="ph-card-variant-item"
                              title={variant.display_name}
                              data-testid={`harga-variant-item-${variant.public_variant_id}`}
                            >
                              <VariantVisual imageUrl={variant.image_url} name={variant.display_name} size="sm" />
                              <span className="ph-card-variant-name">{variant.display_name}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {group.public_description && (
                        <span className="ph-card-description">
                          {group.public_description}
                        </span>
                      )}
                      <span className="ph-card-cta">
                        Lihat Harga <ArrowRight size={17} aria-hidden="true" />
                      </span>
                    </span>
                  </button>
                </article>
              ))}
            </div>
            {filteredGroups.length === 0 && (
              <div className="ph-public-state is-compact">
                Produk tidak ditemukan.
              </div>
            )}
          </>
        )}
      </section>
      <footer className="ph-footer">Eternal Packaging · Harga produk terkini</footer>
    </main>
  );
}

function DetailPage({ groupPublicId }) {
  const [group, setGroup] = useState(null);
  const [selectedVariantId, setSelectedVariantId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [calculating, setCalculating] = useState(false);
  const [calculationError, setCalculationError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    setGroup(null);
    api.publicCustomerPricingGroup(groupPublicId)
      .then((data) => {
        if (cancelled) return;
        const detail = data?.group || null;
        setGroup(detail);
        setSelectedVariantId(detail?.variants?.[0]?.public_variant_id || "");
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
  }, [groupPublicId]);

  const selectedVariant = group?.variants?.find(
    (variant) => variant.public_variant_id === selectedVariantId
  ) || null;

  const selectVariant = (publicVariantId) => {
    setSelectedVariantId(publicVariantId);
    setQuantity("");
    setResult(null);
    setCalculationError("");
  };

  const updateQuantity = (value) => {
    setQuantity(value);
    setResult(null);
    setCalculationError("");
  };

  const calculate = async (event) => {
    event.preventDefault();
    setResult(null);
    setCalculationError("");
    if (!selectedVariantId || !/^\d+$/.test(quantity) || Number(quantity) <= 0) {
      setCalculationError("Jumlah pesanan tidak valid.");
      return;
    }
    setCalculating(true);
    try {
      const calculated = await api.calculatePublicCustomerPrice({
        group_public_id: groupPublicId,
        public_variant_id: selectedVariantId,
        quantity: Number(quantity),
      });
      setResult(calculated);
    } catch (error) {
      setCalculationError(formatApiError(error, "Harga belum tersedia."));
    } finally {
      setCalculating(false);
    }
  };

  const selectedTierMatches = (tier) => (
    result?.selected_tier
    && result.selected_tier.min_qty === tier.min_qty
    && result.selected_tier.max_qty === tier.max_qty
  );

  return (
    <main className="public-harga-page" data-testid="public-harga-detail-page">
      <PublicHeader />
      <div className="ph-detail-shell">
        <Link className="ph-back-link" to="/harga">
          <ArrowLeft size={18} aria-hidden="true" />
          Kembali ke Katalog
        </Link>

        {loading && (
          <div className="ph-public-state" data-testid="public-harga-detail-loading">
            Memuat produk...
          </div>
        )}
        {!loading && loadError && (
          <div className="ph-public-state is-error" role="alert" data-testid="public-harga-not-found">
            <PackageOpen aria-hidden="true" />
            <h1>Produk tidak tersedia.</h1>
            <p>{loadError}</p>
            <Link to="/harga">Lihat katalog produk</Link>
          </div>
        )}

        {!loading && !loadError && group && selectedVariant && (
          <>
            <section className="ph-detail-hero">
              <ProductVisual imageUrl={group.image_url} name={group.name} large />
              <div className="ph-detail-content">
                <p className="ph-eyebrow">ETERNAL PACKAGING</p>
                <h1>{group.name}</h1>
                {group.public_description && (
                  <p className="ph-detail-description">{group.public_description}</p>
                )}

                <form className="ph-calculator" onSubmit={calculate}>
                  <fieldset>
                    <legend>Pilih Varian</legend>
                    <div className="ph-variant-chips" data-testid="harga-variant-buttons">
                      {group.variants.map((variant) => (
                        <button
                          type="button"
                          key={variant.public_variant_id}
                          className={
                            variant.public_variant_id === selectedVariantId
                              ? "is-selected"
                              : ""
                          }
                          aria-pressed={variant.public_variant_id === selectedVariantId}
                          onClick={() => selectVariant(variant.public_variant_id)}
                          data-testid={`harga-variant-${variant.public_variant_id}`}
                        >
                          <VariantVisual imageUrl={variant.image_url} name={variant.display_name} size="md" />
                          <span className="ph-variant-btn-label">{variant.display_name}</span>
                        </button>
                      ))}
                    </div>
                  </fieldset>

                  <label className="ph-quantity-field">
                    <span>Jumlah Pesanan</span>
                    <span className="ph-quantity-input">
                      <input
                        type="number"
                        min="1"
                        max="10000000"
                        step="1"
                        inputMode="numeric"
                        value={quantity}
                        onChange={(event) => updateQuantity(event.target.value)}
                        placeholder={formatNumber(selectedVariant.minimum_quantity)}
                        data-testid="harga-quantity-input"
                      />
                      <b>{selectedVariant.unit || "pcs"}</b>
                    </span>
                    <small>
                      Minimum {formatNumber(selectedVariant.minimum_quantity)}{" "}
                      {selectedVariant.unit || "pcs"}
                    </small>
                  </label>

                  <button
                    type="submit"
                    className="ph-calculate-button"
                    disabled={calculating}
                    data-testid="harga-calculate-button"
                  >
                    <Calculator size={19} aria-hidden="true" />
                    {calculating ? "Menghitung..." : "Hitung Harga"}
                  </button>
                  {calculationError && (
                    <p className="ph-form-error" role="alert" data-testid="harga-calculation-error">
                      {calculationError}
                    </p>
                  )}
                </form>
              </div>
            </section>

            {result ? (
              <section className="ph-results" data-testid="harga-result" aria-live="polite">
                <div className="ph-section-heading">
                  <p className="ph-kicker">Hasil Harga</p>
                  <h2>Estimasi harga pesanan Anda</h2>
                </div>
                <div className="ph-result-grid">
                  <div>
                    <span>Harga / {result.variant.unit || "pcs"}</span>
                    <strong data-testid="harga-unit-price">
                      {formatMoney(result.unit_price)}
                    </strong>
                  </div>
                  <div>
                    <span>Total</span>
                    <strong data-testid="harga-total">{formatMoney(result.total)}</strong>
                  </div>
                </div>
                <p className="ph-used-tier">
                  Tier yang digunakan
                  <b data-testid="harga-selected-tier">
                    {tierRange(result.selected_tier)} {result.variant.unit || "pcs"}
                  </b>
                </p>
              </section>
            ) : (
              <section className="ph-result-prompt" data-testid="harga-result-empty">
                <Calculator aria-hidden="true" />
                <div>
                  <h2>Harga belum tersedia.</h2>
                  <p>Pilih varian dan masukkan jumlah pesanan untuk menghitung harga.</p>
                </div>
              </section>
            )}

            <section className="ph-tier-section">
              <div className="ph-section-heading">
                <p className="ph-kicker">Harga Berdasarkan Jumlah</p>
                <h2>Tier harga {selectedVariant.display_name}</h2>
              </div>
              <div className="ph-selected-variant-card" data-testid="harga-selected-variant-card">
                <VariantVisual imageUrl={selectedVariant.image_url} name={selectedVariant.display_name} size="lg" />
                <div className="ph-selected-variant-meta">
                  <strong>{selectedVariant.display_name}</strong>
                  <span>Minimum pemesanan: {formatNumber(selectedVariant.minimum_quantity)} {selectedVariant.unit || "pcs"}</span>
                </div>
              </div>
              <div className="ph-tier-table-wrap">
                <table className="ph-tier-table" data-testid="harga-tier-table">
                  <thead>
                    <tr>
                      <th>Jumlah</th>
                      <th>Harga / {selectedVariant.unit || "pcs"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedVariant.tiers.map((tier) => (
                      <tr
                        key={`${tier.min_qty}-${tier.max_qty ?? "plus"}`}
                        className={selectedTierMatches(tier) ? "is-selected" : ""}
                        data-testid={
                          selectedTierMatches(tier) ? "harga-tier-selected-row" : undefined
                        }
                      >
                        <td>
                          {tierRange(tier)} {selectedVariant.unit || "pcs"}
                        </td>
                        <td>{formatMoney(tier.unit_price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
      <footer className="ph-footer">
        Harga dihitung berdasarkan tier jumlah pesanan yang berlaku.
      </footer>
    </main>
  );
}

export default function PublicHargaPage() {
  const { groupPublicId } = useParams();
  return groupPublicId
    ? <DetailPage groupPublicId={groupPublicId} />
    : <CatalogPage />;
}
