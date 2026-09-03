import { useEffect, useMemo, useState } from "react";
import { Calculator, Search } from "lucide-react";
import BrandMark from "../components/BrandMark";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";
import "./PublicHargaPage.css";

const formatNumber = (value) =>
  Number(value || 0).toLocaleString("id-ID", { maximumFractionDigits: 2 });

const formatMoney = (value) => `Rp ${formatNumber(value)}`;

const tierLabel = (tier, unit = "pcs") => {
  if (!tier) return "—";
  const range = tier.max_qty == null
    ? `${formatNumber(tier.min_qty)}+`
    : `${formatNumber(tier.min_qty)}–${formatNumber(tier.max_qty)}`;
  return `${range} ${unit || "pcs"}`;
};

export default function PublicHargaPage() {
  const [groups, setGroups] = useState([]);
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [groupDetail, setGroupDetail] = useState(null);
  const [selectedVariantId, setSelectedVariantId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [calculating, setCalculating] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [calculationError, setCalculationError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    api.publicCustomerPricingGroups()
      .then((data) => {
        if (cancelled) return;
        const available = Array.isArray(data?.groups) ? data.groups : [];
        setGroups(available);
        setSelectedGroupId(available[0]?.public_id || "");
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadError(formatApiError(error, "Harga produk tidak dapat dimuat."));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setGroupDetail(null);
    setSelectedVariantId("");
    setResult(null);
    setCalculationError("");
    if (!selectedGroupId) return () => { cancelled = true; };
    setDetailLoading(true);
    api.publicCustomerPricingGroup(selectedGroupId)
      .then((data) => {
        if (cancelled) return;
        const detail = data?.group || null;
        setGroupDetail(detail);
        setSelectedVariantId(detail?.variants?.[0]?.public_variant_id || "");
      })
      .catch((error) => {
        if (!cancelled) {
          setCalculationError(formatApiError(error, "Produk harga tidak tersedia."));
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => { cancelled = true; };
  }, [selectedGroupId]);

  const filteredGroups = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("id-ID");
    if (!query) return groups;
    return groups.filter((group) => (
      group.name.toLocaleLowerCase("id-ID").includes(query)
      || (group.variants || []).some((variant) => (
        variant.display_name.toLocaleLowerCase("id-ID").includes(query)
      ))
    ));
  }, [groups, search]);

  const selectedVariant = groupDetail?.variants?.find(
    (variant) => variant.public_variant_id === selectedVariantId
  ) || null;

  const selectGroup = (publicId) => {
    if (publicId === selectedGroupId) return;
    setSelectedGroupId(publicId);
    setQuantity("");
  };

  const selectVariant = (publicVariantId) => {
    setSelectedVariantId(publicVariantId);
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
    if (!selectedGroupId || !selectedVariantId || !/^\d+$/.test(quantity)) {
      setCalculationError("Jumlah pesanan tidak valid.");
      return;
    }
    setCalculating(true);
    try {
      const calculated = await api.calculatePublicCustomerPrice({
        group_public_id: selectedGroupId,
        public_variant_id: selectedVariantId,
        quantity: Number(quantity),
      });
      setResult(calculated);
    } catch (error) {
      setCalculationError(formatApiError(error, "Harga tidak dapat dihitung."));
    } finally {
      setCalculating(false);
    }
  };

  return (
    <main className="public-harga-page" data-testid="public-harga-page">
      <header className="public-harga-header">
        <BrandMark compact />
        <div>
          <p className="public-harga-eyebrow">Eternal Packaging</p>
          <h1>Cek Harga Produk</h1>
          <p>Pilih produk, varian, dan jumlah pesanan.</p>
        </div>
      </header>

      <div className="public-harga-content">
        {loading && (
          <section className="public-harga-state" data-testid="public-harga-loading">
            Memuat harga produk...
          </section>
        )}

        {!loading && loadError && (
          <section className="public-harga-state is-error" role="alert" data-testid="public-harga-error">
            {loadError}
          </section>
        )}

        {!loading && !loadError && groups.length === 0 && (
          <section className="public-harga-state" data-testid="public-harga-empty">
            <h2>Harga produk belum tersedia.</h2>
            <p>Silakan cek kembali nanti atau hubungi Eternal Packaging.</p>
          </section>
        )}

        {!loading && !loadError && groups.length > 0 && (
          <>
            <section className="public-harga-section" aria-labelledby="harga-produk-title">
              <div className="public-harga-section-head">
                <div>
                  <p className="public-harga-step">01</p>
                  <h2 id="harga-produk-title">Pilih Produk</h2>
                </div>
                {groups.length > 6 && (
                  <label className="public-harga-search">
                    <Search size={16} aria-hidden="true" />
                    <span className="sr-only">Cari produk</span>
                    <input
                      type="search"
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder="Cari produk..."
                    />
                  </label>
                )}
              </div>
              <div className="public-harga-groups" data-testid="public-harga-groups">
                {filteredGroups.map((group) => (
                  <button
                    type="button"
                    key={group.public_id}
                    className={group.public_id === selectedGroupId ? "is-selected" : ""}
                    aria-pressed={group.public_id === selectedGroupId}
                    onClick={() => selectGroup(group.public_id)}
                    data-testid={`harga-group-${group.public_id}`}
                  >
                    <strong>{group.name}</strong>
                    <span>{group.variants.length} varian</span>
                  </button>
                ))}
              </div>
              {filteredGroups.length === 0 && (
                <p className="public-harga-no-search">Produk tidak ditemukan.</p>
              )}
            </section>

            <section className="public-harga-section" aria-labelledby="harga-kalkulator-title">
              <div className="public-harga-section-head">
                <div>
                  <p className="public-harga-step">02</p>
                  <h2 id="harga-kalkulator-title">Hitung Harga</h2>
                </div>
              </div>

              {detailLoading && (
                <p className="public-harga-inline-state" data-testid="public-harga-detail-loading">
                  Memuat varian...
                </p>
              )}

              {!detailLoading && groupDetail && selectedVariant && (
                <form className="public-harga-form" onSubmit={calculate}>
                  <label>
                    <span>Varian</span>
                    <select
                      value={selectedVariantId}
                      onChange={(event) => selectVariant(event.target.value)}
                      data-testid="harga-variant-select"
                    >
                      {groupDetail.variants.map((variant) => (
                        <option key={variant.public_variant_id} value={variant.public_variant_id}>
                          {variant.display_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Jumlah ({selectedVariant.unit || "pcs"})</span>
                    <input
                      type="number"
                      min="1"
                      max="10000000"
                      step="1"
                      inputMode="numeric"
                      value={quantity}
                      onChange={(event) => updateQuantity(event.target.value)}
                      placeholder={`Minimal ${formatNumber(selectedVariant.minimum_quantity)}`}
                      data-testid="harga-quantity-input"
                    />
                  </label>
                  <button
                    type="submit"
                    className="public-harga-calculate"
                    disabled={calculating}
                    data-testid="harga-calculate-button"
                  >
                    <Calculator size={18} aria-hidden="true" />
                    {calculating ? "Menghitung..." : "Hitung Harga"}
                  </button>
                </form>
              )}

              {calculationError && (
                <p className="public-harga-message is-error" role="alert" data-testid="harga-calculation-error">
                  {calculationError}
                </p>
              )}

              {result && (
                <div className="public-harga-result" data-testid="harga-result" aria-live="polite">
                  <div>
                    <span>Harga / {result.variant.unit || "pcs"}</span>
                    <strong data-testid="harga-unit-price">{formatMoney(result.unit_price)}</strong>
                  </div>
                  <div className="is-total">
                    <span>Total</span>
                    <strong data-testid="harga-total">{formatMoney(result.total)}</strong>
                  </div>
                  <p>
                    Tier yang dipakai
                    <b data-testid="harga-selected-tier">
                      {tierLabel(result.selected_tier, result.variant.unit)}
                    </b>
                  </p>
                </div>
              )}
            </section>

            {!detailLoading && selectedVariant && (
              <section className="public-harga-section" aria-labelledby="harga-tier-title">
                <div className="public-harga-section-head">
                  <div>
                    <p className="public-harga-step">03</p>
                    <h2 id="harga-tier-title">Tier Harga</h2>
                  </div>
                  <span className="public-harga-variant-name">{selectedVariant.display_name}</span>
                </div>
                <div className="public-harga-table-wrap">
                  <table className="public-harga-table" data-testid="harga-tier-table">
                    <thead>
                      <tr>
                        <th>Qty</th>
                        <th>Harga / {selectedVariant.unit || "pcs"}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedVariant.tiers.map((tier) => (
                        <tr key={`${tier.min_qty}-${tier.max_qty ?? "plus"}`}>
                          <td>{tierLabel(tier, selectedVariant.unit)}</td>
                          <td>{formatMoney(tier.unit_price)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
      </div>

      <footer className="public-harga-footer">
        Harga dihitung berdasarkan jumlah pesanan dan dapat diperbarui oleh Eternal Packaging.
      </footer>
    </main>
  );
}
