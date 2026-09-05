import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ImageIcon, Plus, RefreshCw, Trash2, Upload, X } from "lucide-react";
import api from "../lib/api";
import { formatDate } from "../lib/constants";
import { errDetail, formatRupiah } from "../lib/purchaseUi";
import { LIVE_SEARCH_DEBOUNCE_MS } from "../hooks/useDebouncedLiveSearch";

export const MODE_FORMULA = "HPP_FORMULA";
export const MODE_MANUAL = "MANUAL_TIER_PRICE";
export const HPP_AUTO = "AUTO_HPP";
export const HPP_MANUAL = "MANUAL_HPP";
export const PROFIT_FIXED = "HPP_PLUS_FIXED";
export const PROFIT_PERCENT = "HPP_PLUS_PERCENT";

function emptyTier() {
  return { min_qty: "", max_qty: "", profit_value: "" };
}

function versionTiers(version) {
  return version?.tiers?.length
    ? version.tiers.map((tier) => ({
        min_qty: tier.min_qty ?? "",
        max_qty: tier.max_qty ?? "",
        profit_value: tier.profit_value ?? "",
      }))
    : [emptyTier()];
}

function versionProducts(version) {
  return (version?.products || []).map((product) => ({
    ...product,
    manual_hpp: product.manual_hpp ?? "",
    tier_prices: [...(product.tier_prices || [])],
  }));
}

function compactNumber(value) {
  const number = Number(value);
  return Number.isFinite(number)
    ? number.toLocaleString("id-ID", { maximumFractionDigits: 2 })
    : "—";
}

export function profitSummary(version) {
  if (!version) return { value: "Belum diatur", label: "", owner: "" };
  const owner = version.status === "DRAFT" ? "DRAFT" : "PUBLISHED";
  if (version.pricing_mode === MODE_MANUAL) {
    return { value: "Harga Manual", label: "Tier Manual", owner };
  }
  const values = (version.tiers || [])
    .map((tier) => Number(tier.profit_value))
    .filter(Number.isFinite);
  if (!values.length) return { value: "Belum diatur", label: "", owner };
  const format = version.profit_mode === PROFIT_PERCENT
    ? (value) => `${compactNumber(value)}%`
    : (value) => formatRupiah(value);
  return {
    value: values.every((value) => value === values[0])
      ? format(values[0])
      : `${format(values[0])} → ${format(values[values.length - 1])}`,
    label: version.profit_mode === PROFIT_PERCENT ? "Persentase" : "Nilai Tetap",
    owner,
  };
}

export function modeSummary(version) {
  if (!version) return "Belum diatur";
  return version.pricing_mode === MODE_FORMULA ? "Formula HPP" : "Tier Manual";
}

export function hppSourceSummary(version) {
  if (!version || version.pricing_mode !== MODE_FORMULA) return "—";
  return version.hpp_source === HPP_AUTO ? "HPP Eternal" : "HPP Manual";
}

export function tierLabel(tier) {
  return tier.max_qty == null || tier.max_qty === ""
    ? `${tier.min_qty || "?"}+`
    : `${tier.min_qty || "?"}–${tier.max_qty}`;
}

export function calculateFormulaPrice(hpp, profit, profitMode) {
  const base = Number(hpp);
  const value = Number(profit);
  if (!Number.isFinite(base) || base <= 0 || !Number.isFinite(value) || value <= 0) {
    return null;
  }
  const result = profitMode === PROFIT_PERCENT
    ? base * (1 + value / 100)
    : base + value;
  return Math.round(result * 100) / 100;
}

export function appendExactProduct(current, master, tierCount) {
  if (current.some((product) => product.accurate_item_id === master.accurate_item_id)) {
    throw new Error("Produk yang sama tidak boleh dipilih dua kali.");
  }
  return [
    ...current,
    {
      accurate_item_id: master.accurate_item_id,
      master_item_id: master.id,
      display_name: master.item_name,
      item_code: master.item_code,
      unit: master.unit,
      is_active: master.is_active !== false,
      manual_hpp: "",
      hpp_snapshot: null,
      hpp_method: null,
      hpp_status: null,
      hpp_message: null,
      tier_prices: Array.from({ length: tierCount }, () => null),
    },
  ];
}

export function removeExactProduct(current, accurateItemId) {
  return current.filter((product) => product.accurate_item_id !== accurateItemId);
}

export function setProductTierPrice(current, accurateItemId, tierIndex, value, tierCount) {
  return current.map((product) => {
    if (product.accurate_item_id !== accurateItemId) return product;
    const prices = [...(product.tier_prices || [])];
    while (prices.length < tierCount) prices.push(null);
    prices[tierIndex] = value;
    return { ...product, tier_prices: prices };
  });
}

function Segmented({ name, value, options, onChange, disabled, testId }) {
  return (
    <div className="cp-seg" data-testid={testId} role="group">
      {options.map((option) => (
        <label key={option.value} className={value === option.value ? "is-active" : ""}>
          <input
            type="radio"
            name={name}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
            disabled={disabled}
          />
          <span>{option.label}</span>
        </label>
      ))}
    </div>
  );
}

function StatusBadge({ product }) {
  if (product.hpp_status === "OK") {
    return <span className="cp-status-pill is-published">Ready</span>;
  }
  if (product.hpp_status === "UNRESOLVED_AMBIGUOUS_FIFO") {
    return <span className="cp-badge-warn">Ambiguous</span>;
  }
  return <span className="cp-badge-warn">{product.hpp_status || "Belum diisi"}</span>;
}

export default function CustomerPricingSettings({ notify, canEdit, canPublish }) {
  const [settings, setSettings] = useState({
    admin_phone: "",
    default_quotation_validity_days: "",
  });
  const [groups, setGroups] = useState([]);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");

  const [editorGroup, setEditorGroup] = useState(null);
  const [version, setVersion] = useState(null);
  const [viewOnlyVersion, setViewOnlyVersion] = useState(null);
  const [history, setHistory] = useState([]);
  const [groupName, setGroupName] = useState("");
  const [publicDescription, setPublicDescription] = useState("");
  const [catalogImage, setCatalogImage] = useState(null);
  const [catalogImageBroken, setCatalogImageBroken] = useState(false);
  const catalogImageInput = useRef(null);
  const [brokenProductImages, setBrokenProductImages] = useState({});
  const productImageInputs = useRef({});
  const [pricingMode, setPricingMode] = useState(MODE_MANUAL);
  const [hppSource, setHppSource] = useState(HPP_AUTO);
  const [profitMode, setProfitMode] = useState(PROFIT_FIXED);
  const [tiers, setTiers] = useState([emptyTier()]);
  const [products, setProducts] = useState([]);

  const [productPickerOpen, setProductPickerOpen] = useState(false);
  const [masterQuery, setMasterQuery] = useState("");
  const [masterItems, setMasterItems] = useState([]);
  const [masterSearchBusy, setMasterSearchBusy] = useState(false);
  const [selectedMaster, setSelectedMaster] = useState(null);
  const masterSearchSeq = useRef(0);

  const loadSettings = useCallback(async () => {
    const response = await api.customerPricingSettings();
    const value = response.settings || {};
    setSettings({
      admin_phone: value.admin_phone || "",
      default_quotation_validity_days: value.default_quotation_validity_days ?? "",
    });
  }, []);

  const loadGroups = useCallback(async () => {
    const response = await api.customerPricingGroups({ q: query });
    setGroups(response.groups || []);
  }, [query]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setQuery(queryInput.trim());
    }, LIVE_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [queryInput]);

  useEffect(() => {
    setBusy(true);
    loadSettings()
      .catch((exception) => setError(errDetail(exception)))
      .finally(() => setBusy(false));
  }, [loadSettings]);

  useEffect(() => {
    setBusy(true);
    loadGroups()
      .catch((exception) => setError(errDetail(exception)))
      .finally(() => setBusy(false));
  }, [loadGroups]);

  const applyVersion = (next) => {
    setVersion(next);
    setViewOnlyVersion(null);
    setGroupName(next.group_name || "");
    setPricingMode(next.pricing_mode || MODE_MANUAL);
    setHppSource(next.hpp_source || HPP_AUTO);
    setProfitMode(next.profit_mode || PROFIT_FIXED);
    setTiers(versionTiers(next));
    setProducts(versionProducts(next));
    setPreview(null);
  };

  const openGroup = async (group) => {
    setBusy(true);
    setError("");
    try {
      const groupResponse = await api.customerPricingGroup(group.id);
      const versionsResponse = await api.customerPricingVersions(group.id);
      setEditorGroup(groupResponse.group);
      setPublicDescription(groupResponse.group?.public_description || "");
      setCatalogImage(groupResponse.group?.catalog_image || null);
      setCatalogImageBroken(false);
      setHistory(versionsResponse.versions || []);
      if (canEdit) {
        const draft = (versionsResponse.versions || []).find(
          (item) => item.status === "DRAFT"
        );
        const published = (versionsResponse.versions || []).find(
          (item) => item.status === "PUBLISHED"
        );
        if (draft) {
          applyVersion(draft);
        } else if (published) {
          setViewOnlyVersion(published);
          setVersion(null);
          setGroupName(published.group_name || group.group_name);
        } else {
          const draftResponse = await api.createCustomerPricingDraft(group.id);
          applyVersion(draftResponse.version);
        }
      } else {
        const published = (versionsResponse.versions || []).find(
          (item) => item.status === "PUBLISHED"
        );
        setViewOnlyVersion(published || null);
        setVersion(null);
        setGroupName(published?.group_name || group.group_name);
      }
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const closeEditor = () => {
    setEditorGroup(null);
    setVersion(null);
    setViewOnlyVersion(null);
    setHistory([]);
    setPublicDescription("");
    setCatalogImage(null);
    setCatalogImageBroken(false);
    setBrokenProductImages({});
    setPreview(null);
    setError("");
  };

  const saveSettings = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await api.updateCustomerPricingSettings({
        admin_phone: settings.admin_phone,
        default_quotation_validity_days:
          settings.default_quotation_validity_days === ""
            ? null
            : Number(settings.default_quotation_validity_days),
      });
      setSettings({
        admin_phone: response.settings?.admin_phone || "",
        default_quotation_validity_days:
          response.settings?.default_quotation_validity_days ?? "",
      });
      notify?.(response.message || "Pengaturan disimpan");
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const createGroup = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await api.createCustomerPricingGroup({ group_name: newGroupName });
      setCreateOpen(false);
      setNewGroupName("");
      await loadGroups();
      await openGroup(response.group);
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const saveCatalogPresentation = async () => {
    if (!editorGroup) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.patchCustomerPricingGroup(editorGroup.id, {
        public_description: publicDescription,
      });
      setEditorGroup(response.group);
      setPublicDescription(response.group?.public_description || "");
      notify?.(response.message || "Tampilan katalog disimpan");
      await loadGroups();
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const uploadCatalogImage = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !editorGroup) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.uploadCustomerPricingCatalogImage(editorGroup.id, file);
      setEditorGroup(response.group);
      setCatalogImage(response.group?.catalog_image || null);
      setCatalogImageBroken(false);
      notify?.(response.message || "Foto katalog diperbarui");
      await loadGroups();
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const removeCatalogImage = async () => {
    if (!editorGroup || !catalogImage) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.deleteCustomerPricingCatalogImage(editorGroup.id);
      setEditorGroup(response.group);
      setCatalogImage(null);
      setCatalogImageBroken(false);
      notify?.(response.message || "Foto katalog dihapus");
      await loadGroups();
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const uploadProductImage = async (accurateItemId, event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !editorGroup || !accurateItemId) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.uploadCustomerPricingProductImage(
        editorGroup.id,
        accurateItemId,
        file
      );
      setEditorGroup(response.group);
      setBrokenProductImages((prev) => ({ ...prev, [accurateItemId]: false }));
      notify?.(response.message || "Foto produk diperbarui");
      await loadGroups();
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const removeProductImage = async (accurateItemId) => {
    if (!editorGroup || !accurateItemId) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.deleteCustomerPricingProductImage(
        editorGroup.id,
        accurateItemId
      );
      setEditorGroup(response.group);
      setBrokenProductImages((prev) => ({ ...prev, [accurateItemId]: false }));
      notify?.(response.message || "Foto produk dihapus");
      await loadGroups();
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const cloneDraft = async () => {
    if (!editorGroup) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.createCustomerPricingDraft(editorGroup.id);
      applyVersion(response.version);
      const versionsResponse = await api.customerPricingVersions(editorGroup.id);
      setHistory(versionsResponse.versions || []);
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!productPickerOpen) return undefined;
    setMasterItems([]);
    setMasterSearchBusy(true);
    setSelectedMaster(null);
    const requestId = ++masterSearchSeq.current;
    const handle = window.setTimeout(async () => {
      try {
        const response = await api.masterItems({
          q: masterQuery.trim(),
          status: "active",
          page: 1,
          page_size: 50,
        });
        if (requestId !== masterSearchSeq.current) return;
        setMasterItems(response.items || []);
      } catch (exception) {
        if (requestId !== masterSearchSeq.current) return;
        setMasterItems([]);
        setError(errDetail(exception));
      } finally {
        if (requestId === masterSearchSeq.current) setMasterSearchBusy(false);
      }
    }, 300);
    return () => {
      window.clearTimeout(handle);
      masterSearchSeq.current += 1;
    };
  }, [productPickerOpen, masterQuery]);

  const addSelectedProduct = () => {
    if (!selectedMaster) return;
    if (products.some((product) => product.accurate_item_id === selectedMaster.accurate_item_id)) {
      setError("Produk yang sama tidak boleh dipilih dua kali.");
      return;
    }
    setProducts((current) => appendExactProduct(current, selectedMaster, tiers.length));
    setProductPickerOpen(false);
    setSelectedMaster(null);
    setMasterItems([]);
    setMasterQuery("");
    setError("");
  };

  const removeProduct = (accurateItemId) => {
    setProducts((current) => removeExactProduct(current, accurateItemId));
  };

  const updateTier = (index, patch) => {
    setTiers((current) =>
      current.map((tier, tierIndex) => (tierIndex === index ? { ...tier, ...patch } : tier))
    );
  };

  const addTier = () => {
    setTiers((current) => [...current, emptyTier()]);
    setProducts((current) =>
      current.map((product) => ({
        ...product,
        tier_prices: [...(product.tier_prices || []), null],
      }))
    );
  };

  const removeTier = (index) => {
    if (tiers.length <= 1) return;
    setTiers((current) => current.filter((_tier, tierIndex) => tierIndex !== index));
    setProducts((current) =>
      current.map((product) => ({
        ...product,
        tier_prices: (product.tier_prices || []).filter(
          (_price, tierIndex) => tierIndex !== index
        ),
      }))
    );
  };

  const updateProduct = (accurateItemId, patch) => {
    setProducts((current) =>
      current.map((product) =>
        product.accurate_item_id === accurateItemId ? { ...product, ...patch } : product
      )
    );
  };

  const updateManualPrice = (accurateItemId, tierIndex, value) => {
    setProducts((current) =>
      setProductTierPrice(current, accurateItemId, tierIndex, value, tiers.length)
    );
  };

  const buildPayload = () => ({
    revision: version?.revision,
    group_name: groupName,
    pricing_mode: pricingMode,
    hpp_source: pricingMode === MODE_FORMULA ? hppSource : null,
    profit_mode: pricingMode === MODE_FORMULA ? profitMode : null,
    tiers: tiers.map((tier, index) => ({
      min_qty: tier.min_qty === "" ? null : Number(tier.min_qty),
      max_qty:
        index === tiers.length - 1
          ? null
          : tier.max_qty === ""
            ? null
            : Number(tier.max_qty),
      ...(pricingMode === MODE_FORMULA
        ? { profit_value: tier.profit_value === "" ? null : Number(tier.profit_value) }
        : {}),
    })),
    products: products.map((product) => ({
      accurate_item_id: product.accurate_item_id,
      ...(pricingMode === MODE_FORMULA && hppSource === HPP_MANUAL
        ? { manual_hpp: product.manual_hpp === "" ? null : Number(product.manual_hpp) }
        : {}),
      ...(pricingMode === MODE_MANUAL
        ? {
            tier_prices: tiers.map((_tier, index) => {
              const price = product.tier_prices?.[index];
              return price === "" || price == null ? null : Number(price);
            }),
          }
        : {}),
    })),
  });

  const saveDraft = async () => {
    if (!version) return null;
    setBusy(true);
    setError("");
    try {
      const response = await api.updateCustomerPricingVersion(version.id, buildPayload());
      applyVersion(response.version);
      const previewResponse = await api.previewCustomerPricingVersion(response.version.id, {});
      setPreview(previewResponse.preview);
      if (!previewResponse.preview?.valid) {
        setError(
          (previewResponse.preview?.errors || []).join(" · ") ||
            "Draft tersimpan, tetapi belum siap Publish."
        );
        notify?.("Draft disimpan (belum siap Publish)");
      } else {
        notify?.(response.message || "Draft disimpan");
      }
      const versionsResponse = await api.customerPricingVersions(editorGroup.id);
      setHistory(versionsResponse.versions || []);
      await loadGroups();
      return response.version;
    } catch (exception) {
      setError(errDetail(exception));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const runPreview = async () => {
    const saved = await saveDraft();
    if (!saved) return;
    setBusy(true);
    try {
      const response = await api.previewCustomerPricingVersion(saved.id, {});
      setPreview(response.preview);
      if (!response.preview?.valid) {
        setError((response.preview?.errors || []).join(" · "));
      } else {
        setError("");
        notify?.("Preview harga siap");
      }
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const refreshHpp = async () => {
    const saved = await saveDraft();
    if (!saved) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.refreshCustomerPricingHpp(saved.id);
      applyVersion(response.version);
      const unresolved = response.version.products.filter(
        (product) => product.hpp_status !== "OK"
      );
      if (unresolved.length) {
        setError(
          unresolved
            .map((product) => `${product.display_name}: ${product.hpp_message || product.hpp_status}`)
            .join(" · ")
        );
      } else {
        notify?.("HPP semua produk diperbarui");
      }
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    const saved = await saveDraft();
    if (!saved) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.publishCustomerPricingVersion(saved.id, {
        revision: saved.revision,
      });
      setEditorGroup(response.group);
      setVersion(null);
      setViewOnlyVersion(response.version);
      setHistory((await api.customerPricingVersions(response.group.id)).versions || []);
      await loadGroups();
      notify?.(response.message || "Berhasil di-Publish");
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const openHistoryVersion = async (versionId) => {
    setBusy(true);
    setError("");
    try {
      const response = await api.customerPricingVersion(versionId);
      if (response.version.status === "PUBLISHED" || !canEdit) {
        setViewOnlyVersion(response.version);
        setVersion(null);
      } else {
        applyVersion(response.version);
      }
    } catch (exception) {
      setError(errDetail(exception));
    } finally {
      setBusy(false);
    }
  };

  const activeView = viewOnlyVersion || version;
  const readOnly = Boolean(viewOnlyVersion);

  const liveFormulaProducts = useMemo(() => {
    if (pricingMode !== MODE_FORMULA) return products;
    return products.map((product) => {
      const hpp = hppSource === HPP_MANUAL ? product.manual_hpp : product.hpp_snapshot;
      return {
        ...product,
        display_hpp: hpp,
        display_prices: tiers.map((tier) =>
          calculateFormulaPrice(hpp, tier.profit_value, profitMode)
        ),
      };
    });
  }, [pricingMode, products, hppSource, tiers, profitMode]);

  return (
    <div className="settings-card cp-settings" data-testid="customer-pricing-settings">
      <div className="cp-page-head">
        <h2>Harga Customer</h2>
        <p className="muted small">
          Kelompok harga untuk beberapa produk exact. HPP dan harga akhir tetap dihitung per produk.
        </p>
      </div>

      {error && (
        <div className="signal-strip" role="alert" data-testid="cp-error">
          <AlertCircle size={16} /><span>{error}</span>
        </div>
      )}

      {!editorGroup && (
        <>
          <section className="cp-section cp-section-compact" data-testid="cp-general-settings">
            <div className="cp-section-head">
              <h3>Pengaturan Umum</h3>
              {canEdit && (
                <button className="ghost sm" disabled={busy} onClick={saveSettings}>
                  Simpan Pengaturan
                </button>
              )}
            </div>
            <div className="cp-inline-fields">
              <label>Nomor Admin Eternal
                <input
                  value={settings.admin_phone}
                  onChange={(event) =>
                    setSettings((current) => ({ ...current, admin_phone: event.target.value }))
                  }
                  disabled={!canEdit || busy}
                />
              </label>
              <label>Masa Berlaku Quotation (hari)
                <input
                  type="number"
                  min={1}
                  value={settings.default_quotation_validity_days}
                  onChange={(event) =>
                    setSettings((current) => ({
                      ...current,
                      default_quotation_validity_days: event.target.value,
                    }))
                  }
                  disabled={!canEdit || busy}
                />
              </label>
            </div>
          </section>

          <section className="cp-section cp-section-compact" data-testid="cp-group-list">
            <div className="cp-section-head">
              <h3>Kelompok Harga</h3>
              <div className="cp-toolbar">
                <input
                  value={queryInput}
                  onChange={(event) => setQueryInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      setQuery(queryInput.trim());
                    }
                  }}
                  placeholder="Cari kelompok / produk (live)…"
                  data-testid="cp-group-search"
                  aria-label="Cari kelompok harga live"
                />
                {canEdit && (
                  <button
                    className="primary sm"
                    disabled={busy}
                    onClick={() => setCreateOpen(true)}
                    data-testid="cp-add-group"
                  >
                    <Plus size={14} /> Tambah Kelompok
                  </button>
                )}
              </div>
            </div>
            <div className="cp-product-table-wrap">
              <table className="cp-sheet cp-product-sheet">
                <thead>
                  <tr>
                    <th>Kelompok</th>
                    <th>Produk</th>
                    <th>Mode Harga</th>
                    <th>Sumber HPP</th>
                    <th>Keuntungan</th>
                    <th>Draft</th>
                    <th>Published</th>
                    <th>Status</th>
                    <th>Updated</th>
                    <th>Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((group) => {
                    const summary = group.summary_version;
                    const profit = profitSummary(summary);
                    const names = (summary?.products || []).map((product) => product.display_name);
                    return (
                      <tr key={group.id} data-testid={`cp-group-${group.id}`}>
                        <td><strong>{group.group_name}</strong></td>
                        <td>
                          <strong>{names.length} produk</strong>
                          <span className="cp-cell-sub">{names.join(" • ") || "Belum ada produk"}</span>
                        </td>
                        <td>{modeSummary(summary)}</td>
                        <td>{hppSourceSummary(summary)}</td>
                        <td>
                          <strong>{profit.value}</strong>
                          <span className="cp-cell-sub">
                            {[profit.label, profit.owner].filter(Boolean).join(" · ")}
                          </span>
                        </td>
                        <td>
                          {group.current_draft_version_id
                            ? `v${group.current_draft_version_number || summary?.version_number || "✓"}`
                            : "—"}
                        </td>
                        <td>
                          {group.current_published_version_id
                            ? `v${group.current_published_version_number || "✓"}`
                            : "—"}
                        </td>
                        <td>
                          <span className={`cp-status-pill ${group.enabled ? "is-published" : ""}`}>
                            {group.enabled ? "Aktif" : "Nonaktif"}
                          </span>
                        </td>
                        <td className="cp-updated-cell">
                          {group.updated_at ? formatDate(group.updated_at) : "—"}
                        </td>
                        <td>
                          <button className="ghost sm" onClick={() => openGroup(group)}>
                            {canEdit ? "Edit" : "Lihat"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {!groups.length && (
                    <tr>
                      <td colSpan={10} className="muted small">Belum ada kelompok harga.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {createOpen && (
        <div className="modal" role="dialog" data-testid="cp-create-group-modal">
          <div className="modal-inner">
            <h3>Tambah Kelompok Harga</h3>
            <label>Nama Kelompok
              <input
                value={newGroupName}
                onChange={(event) => setNewGroupName(event.target.value)}
                placeholder="Contoh: Paper Cup"
                data-testid="cp-group-name-new"
              />
            </label>
            <div className="delete-modal-actions">
              <button className="ghost" onClick={() => setCreateOpen(false)}>Batal</button>
              <button
                className="primary"
                disabled={busy || !newGroupName.trim()}
                onClick={createGroup}
              >
                Buat Kelompok
              </button>
            </div>
          </div>
        </div>
      )}

      {productPickerOpen && (
        <div className="modal" role="dialog" data-testid="cp-product-picker">
          <div className="modal-inner">
            <h3>Tambah Produk Exact</h3>
            <p className="muted small">Pilih manual dari Master Barang/Jasa. Tidak ada pencocokan nama otomatis.</p>
            <div className="cp-toolbar">
              <input
                value={masterQuery}
                onChange={(event) => {
                  setMasterQuery(event.target.value);
                  setMasterItems([]);
                  setMasterSearchBusy(true);
                  setSelectedMaster(null);
                }}
                placeholder="Cari nama / kode…"
                data-testid="cp-master-search"
                autoFocus
              />
            </div>
            {masterSearchBusy && (
              <p className="muted small" data-testid="cp-master-loading">Memuat…</p>
            )}
            {!masterSearchBusy && masterItems.length === 0 && (
              <p className="muted small" data-testid="cp-master-empty">Produk tidak ditemukan</p>
            )}
            {!masterSearchBusy && (
            <ul className="cp-master-list">
              {masterItems.map((item) => {
                const duplicate = products.some(
                  (product) => product.accurate_item_id === item.accurate_item_id
                );
                return (
                  <li key={item.accurate_item_id}>
                    <button
                      className={
                        selectedMaster?.accurate_item_id === item.accurate_item_id
                          ? "is-selected"
                          : ""
                      }
                      disabled={duplicate}
                      onClick={() => setSelectedMaster(item)}
                      data-testid={`cp-master-option-${item.accurate_item_id}`}
                    >
                      <strong>{item.item_name}</strong>
                      <span className="muted small">
                        {" "}· {item.item_code || "—"} · {item.unit || "—"} · ID {item.accurate_item_id}
                        {duplicate ? " · Sudah dipilih" : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            )}
            <div className="delete-modal-actions">
              <button className="ghost" onClick={() => setProductPickerOpen(false)}>Batal</button>
              <button
                className="primary"
                disabled={!selectedMaster}
                onClick={addSelectedProduct}
                data-testid="cp-add-product-confirm"
              >
                Tambah Produk
              </button>
            </div>
          </div>
        </div>
      )}

      {editorGroup && activeView && (
        <section className="cp-editor-shell" data-testid="cp-editor">
          <header className="cp-editor-header">
            <div className="cp-editor-title">
              <input
                className="cp-group-name-input"
                value={groupName}
                onChange={(event) => setGroupName(event.target.value)}
                disabled={readOnly || !canEdit}
                aria-label="Nama Kelompok"
              />
              <div className="cp-editor-meta">
                <span className={`cp-status-pill ${readOnly ? "is-published" : "is-draft"}`}>
                  {readOnly ? "Published" : "Draft"}
                </span>
                <span>v{activeView.version_number} · rev {activeView.revision}</span>
                <span>{activeView.products?.length || 0} produk exact</span>
              </div>
            </div>
            <div className="cp-editor-actions">
              {!readOnly && canEdit && (
                <button className="ghost sm" disabled={busy} onClick={saveDraft}>
                  Simpan Draft
                </button>
              )}
              {!readOnly && canEdit && (
                <button className="ghost sm" disabled={busy} onClick={runPreview}>Preview</button>
              )}
              {!readOnly && canPublish && (
                <button className="primary sm" disabled={busy} onClick={publish}>Publish</button>
              )}
              {readOnly && canEdit && (
                <button
                  className="primary sm"
                  disabled={busy}
                  onClick={cloneDraft}
                  data-testid="cp-clone-draft"
                >
                  Clone Draft
                </button>
              )}
              <button className="ghost sm" onClick={closeEditor}>
                <X size={14} /> Tutup
              </button>
            </div>
          </header>

          {history.length > 0 && (
            <div className="cp-history">
              <span className="cp-history-label">Versi</span>
              <ul>
                {history.map((item) => (
                  <li key={item.id}>
                    <button
                      className={`ghost sm ${activeView.id === item.id ? "is-active-ver" : ""}`}
                      onClick={() => openHistoryVersion(item.id)}
                    >
                      v{item.version_number} · {item.status}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <section className="cp-catalog-presentation" data-testid="cp-catalog-presentation">
            <div className="cp-catalog-copy">
              <div>
                <span className="cp-config-label">Tampilan Katalog Publik</span>
                <strong>Deskripsi & Foto Katalog</strong>
                <p className="muted small">
                  Metadata tampilan grup. Perubahan ini tidak mengubah versi atau angka harga.
                </p>
              </div>
              <label>
                Deskripsi Singkat
                <textarea
                  rows={3}
                  maxLength={280}
                  value={publicDescription}
                  onChange={(event) => setPublicDescription(event.target.value)}
                  disabled={!canEdit || busy}
                  placeholder="Deskripsi singkat untuk pelanggan (opsional)"
                  data-testid="cp-public-description"
                />
                <span className="cp-description-count">{publicDescription.length}/280</span>
              </label>
              {canEdit && (
                <button
                  className="ghost sm"
                  disabled={busy}
                  onClick={saveCatalogPresentation}
                  data-testid="cp-save-catalog-presentation"
                >
                  Simpan Tampilan
                </button>
              )}
            </div>
            <div className="cp-catalog-image-panel">
              <span className="cp-config-label">Foto Katalog</span>
              <div className="cp-catalog-image-preview">
                {catalogImage && !catalogImageBroken ? (
                  <img
                    src={api.customerPricingCatalogImageUrl(
                      editorGroup.id,
                      catalogImage.file_id
                    )}
                    alt={`Foto katalog ${groupName}`}
                    onError={() => setCatalogImageBroken(true)}
                  />
                ) : (
                  <div className="cp-catalog-image-empty">
                    <ImageIcon size={28} aria-hidden="true" />
                    <span>Belum ada foto</span>
                  </div>
                )}
              </div>
              {catalogImage?.original_filename && (
                <span className="muted small">{catalogImage.original_filename}</span>
              )}
              {canEdit && (
                <div className="cp-catalog-image-actions">
                  <input
                    ref={catalogImageInput}
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    hidden
                    onChange={uploadCatalogImage}
                    data-testid="cp-catalog-image-input"
                  />
                  <button
                    className="ghost sm"
                    disabled={busy}
                    onClick={() => catalogImageInput.current?.click()}
                    data-testid="cp-upload-catalog-image"
                  >
                    <Upload size={14} />
                    {catalogImage ? "Ganti Foto" : "Upload Foto"}
                  </button>
                  {catalogImage && (
                    <button
                      className="ghost sm danger"
                      disabled={busy}
                      onClick={removeCatalogImage}
                      data-testid="cp-remove-catalog-image"
                    >
                      <Trash2 size={14} /> Hapus Foto
                    </button>
                  )}
                </div>
              )}
            </div>
          </section>

          {!readOnly && (
            <>
              <div className="cp-config-bar">
                <div className="cp-config-block">
                  <span className="cp-config-label">Mode Harga</span>
                  <Segmented
                    name="cp-mode"
                    value={pricingMode}
                    onChange={setPricingMode}
                    disabled={!canEdit}
                    options={[
                      { value: MODE_FORMULA, label: "Formula HPP" },
                      { value: MODE_MANUAL, label: "Harga Tier Manual" },
                    ]}
                  />
                </div>
                {pricingMode === MODE_FORMULA && (
                  <>
                    <div className="cp-config-block">
                      <span className="cp-config-label">Sumber HPP</span>
                      <Segmented
                        name="cp-hpp"
                        value={hppSource}
                        onChange={setHppSource}
                        disabled={!canEdit}
                        options={[
                          { value: HPP_AUTO, label: "Otomatis HPP Eternal" },
                          { value: HPP_MANUAL, label: "HPP Manual per Produk" },
                        ]}
                      />
                    </div>
                    <div className="cp-config-block">
                      <span className="cp-config-label">Metode Keuntungan</span>
                      <Segmented
                        name="cp-profit"
                        value={profitMode}
                        onChange={setProfitMode}
                        disabled={!canEdit}
                        options={[
                          { value: PROFIT_FIXED, label: "HPP + Nilai Tetap" },
                          { value: PROFIT_PERCENT, label: "HPP + Persentase" },
                        ]}
                      />
                    </div>
                    {hppSource === HPP_AUTO && (
                      <button className="ghost sm" disabled={busy} onClick={refreshHpp}>
                        <RefreshCw size={14} /> Refresh Semua HPP
                      </button>
                    )}
                  </>
                )}
              </div>

              <div className="cp-sheet-toolbar">
                <strong>Produk / Varian</strong>
                <button
                  className="ghost sm"
                  onClick={() => setProductPickerOpen(true)}
                  data-testid="cp-add-product"
                >
                  <Plus size={14} /> Tambah Produk
                </button>
              </div>
              <div className="cp-sheet-scroll">
                <table className="cp-sheet cp-membership-table">
                  <thead>
                    <tr>
                      <th>Foto</th><th>Produk</th><th>Kode</th><th>Unit</th>
                      <th>HPP / Status</th><th>Aksi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {products.map((product) => {
                      const prodImg = editorGroup?.product_images?.[product.accurate_item_id];
                      return (
                        <tr key={product.accurate_item_id} data-testid={`cp-selected-${product.accurate_item_id}`}>
                          <td className="cp-product-photo-cell" data-testid={`cp-product-photo-${product.accurate_item_id}`}>
                            <div className="cp-product-photo-wrap">
                              {prodImg && !brokenProductImages[product.accurate_item_id] ? (
                                <img
                                  src={api.customerPricingProductImageUrl(
                                    editorGroup.id,
                                    product.accurate_item_id,
                                    prodImg.file_id
                                  )}
                                  alt={`Foto ${product.display_name}`}
                                  className="cp-product-photo-thumb"
                                  onError={() =>
                                    setBrokenProductImages((prev) => ({
                                      ...prev,
                                      [product.accurate_item_id]: true,
                                    }))
                                  }
                                />
                              ) : (
                                <div
                                  className="cp-product-photo-empty"
                                  data-testid={`cp-product-photo-placeholder-${product.accurate_item_id}`}
                                >
                                  <ImageIcon size={18} aria-hidden="true" />
                                  <span>Belum ada foto</span>
                                </div>
                              )}
                            </div>
                            {canEdit && (
                              <div className="cp-product-photo-actions">
                                <input
                                  ref={(element) => {
                                    productImageInputs.current[product.accurate_item_id] = element;
                                  }}
                                  type="file"
                                  accept="image/jpeg,image/png,image/webp"
                                  hidden
                                  onChange={(event) =>
                                    uploadProductImage(product.accurate_item_id, event)
                                  }
                                  data-testid={`cp-product-photo-input-${product.accurate_item_id}`}
                                />
                                <button
                                  type="button"
                                  className="ghost sm"
                                  disabled={busy}
                                  onClick={() =>
                                    productImageInputs.current[product.accurate_item_id]?.click()
                                  }
                                  data-testid={`cp-upload-product-photo-${product.accurate_item_id}`}
                                >
                                  <Upload size={12} />
                                  {prodImg ? "Ganti Foto" : "Upload Foto"}
                                </button>
                                {prodImg && (
                                  <button
                                    type="button"
                                    className="ghost sm danger"
                                    disabled={busy}
                                    onClick={() => removeProductImage(product.accurate_item_id)}
                                    data-testid={`cp-remove-product-photo-${product.accurate_item_id}`}
                                  >
                                    <Trash2 size={12} /> Hapus Foto
                                  </button>
                                )}
                              </div>
                            )}
                          </td>
                          <td>
                            <strong>{product.display_name}</strong>
                            <span className="cp-cell-sub mono">ID {product.accurate_item_id}</span>
                          </td>
                          <td>{product.item_code || "—"}</td>
                          <td>{product.unit || "—"}</td>
                          <td>
                            {pricingMode === MODE_MANUAL
                              ? "Tidak digunakan"
                              : hppSource === HPP_MANUAL
                                ? (
                                  <input
                                    type="number"
                                    min="0"
                                    step="0.01"
                                    value={product.manual_hpp}
                                    onChange={(event) =>
                                      updateProduct(product.accurate_item_id, {
                                        manual_hpp: event.target.value,
                                      })
                                    }
                                    aria-label={`HPP Manual ${product.display_name}`}
                                  />
                                )
                                : <StatusBadge product={product} />}
                          </td>
                          <td>
                            <button
                              className="ghost sm icon"
                              onClick={() => removeProduct(product.accurate_item_id)}
                              aria-label={`Hapus ${product.display_name}`}
                            >
                              <Trash2 size={14} />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {!products.length && (
                      <tr><td colSpan={6} className="muted small">Belum ada produk dipilih.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>

              <div className="cp-sheet-toolbar">
                <strong>Tier Bersama</strong>
                <button className="ghost sm" onClick={addTier}>
                  <Plus size={14} /> Tambah Baris
                </button>
              </div>
              <div className="cp-sheet-scroll">
                <table className="cp-sheet cp-shared-tier-table" data-testid="cp-shared-tiers">
                  <thead>
                    <tr>
                      <th>No</th><th>Min Qty</th><th>Max Qty</th>
                      {pricingMode === MODE_FORMULA && (
                        <th>{profitMode === PROFIT_PERCENT ? "Persentase" : "Nilai Tetap"}</th>
                      )}
                      <th>Aksi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tiers.map((tier, index) => (
                      <tr key={index}>
                        <td className="col-no">{index + 1}</td>
                        <td>
                          <input
                            type="number"
                            min="1"
                            value={tier.min_qty}
                            onChange={(event) => updateTier(index, { min_qty: event.target.value })}
                          />
                        </td>
                        <td>
                          {index === tiers.length - 1
                            ? <input disabled value="Tanpa batas" />
                            : (
                              <input
                                type="number"
                                min="1"
                                value={tier.max_qty}
                                onChange={(event) =>
                                  updateTier(index, { max_qty: event.target.value })
                                }
                              />
                            )}
                        </td>
                        {pricingMode === MODE_FORMULA && (
                          <td>
                            <input
                              type="number"
                              min="0"
                              step="0.01"
                              value={tier.profit_value}
                              onChange={(event) =>
                                updateTier(index, { profit_value: event.target.value })
                              }
                            />
                          </td>
                        )}
                        <td>
                          {tiers.length > 1 && (
                            <button
                              className="ghost sm icon"
                              onClick={() => removeTier(index)}
                              aria-label={`Hapus tier ${index + 1}`}
                            >
                              <Trash2 size={14} />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {pricingMode === MODE_FORMULA ? (
                <div data-testid="cp-formula-matrix">
                  <div className="cp-sheet-toolbar">
                    <strong>Preview Harga per Produk × Tier</strong>
                    <span className="muted small">Readonly · setiap produk memakai HPP sendiri</span>
                  </div>
                  <div className="cp-sheet-scroll">
                    <table className="cp-sheet cp-price-matrix">
                      <thead>
                        <tr>
                          <th>Produk</th><th>HPP</th><th>Method</th><th>Status</th>
                          {tiers.map((tier, index) => <th key={index}>{tierLabel(tier)}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {liveFormulaProducts.map((product) => (
                          <tr key={product.accurate_item_id}>
                            <td><strong>{product.display_name}</strong></td>
                            <td>{product.display_hpp ? formatRupiah(product.display_hpp) : "—"}</td>
                            <td>{hppSource === HPP_MANUAL ? "MANUAL" : product.hpp_method || "—"}</td>
                            <td>
                              {hppSource === HPP_MANUAL
                                ? (Number(product.manual_hpp) > 0 ? "Ready" : "Belum diisi")
                                : <StatusBadge product={product} />}
                            </td>
                            {product.display_prices.map((price, index) => (
                              <td key={index}>{price != null ? formatRupiah(price) : "—"}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <div data-testid="cp-manual-price-matrix">
                  <div className="cp-sheet-toolbar">
                    <strong>Harga Manual per Produk × Tier</strong>
                    <span className="muted small">Harga setiap produk diisi independen</span>
                  </div>
                  <div className="cp-sheet-scroll">
                    <table className="cp-sheet cp-price-matrix">
                      <thead>
                        <tr>
                          <th>Produk</th>
                          {tiers.map((tier, index) => <th key={index}>{tierLabel(tier)}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {products.map((product) => (
                          <tr key={product.accurate_item_id}>
                            <td><strong>{product.display_name}</strong></td>
                            {tiers.map((_tier, index) => (
                              <td key={index}>
                                <input
                                  type="number"
                                  min="0"
                                  step="0.01"
                                  value={product.tier_prices?.[index] ?? ""}
                                  onChange={(event) =>
                                    updateManualPrice(
                                      product.accurate_item_id,
                                      index,
                                      event.target.value
                                    )
                                  }
                                  aria-label={`${product.display_name} tier ${index + 1}`}
                                />
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}

          {readOnly && (
            <div className="cp-readonly" data-testid="cp-readonly-version">
              <div className="cp-readonly-bar">
                <strong>Versi Published v{viewOnlyVersion.version_number} (read-only)</strong>
                <span>{modeSummary(viewOnlyVersion)}</span>
                <span>{viewOnlyVersion.products.length} produk exact</span>
              </div>
              <div className="cp-sheet-scroll">
                <table className="cp-sheet cp-price-matrix">
                  <thead>
                    <tr>
                      <th>Foto</th>
                      <th>Produk</th>
                      {viewOnlyVersion.pricing_mode === MODE_FORMULA && <th>HPP</th>}
                      {viewOnlyVersion.tiers.map((tier, index) => (
                        <th key={index}>{tierLabel(tier)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {viewOnlyVersion.products.map((product) => {
                      const prodImg = editorGroup?.product_images?.[product.accurate_item_id];
                      return (
                        <tr key={product.accurate_item_id}>
                          <td className="cp-product-photo-cell" data-testid={`cp-readonly-product-photo-${product.accurate_item_id}`}>
                            <div className="cp-product-photo-wrap">
                              {prodImg && !brokenProductImages[product.accurate_item_id] ? (
                                <img
                                  src={api.customerPricingProductImageUrl(
                                    editorGroup.id,
                                    product.accurate_item_id,
                                    prodImg.file_id
                                  )}
                                  alt={`Foto ${product.display_name}`}
                                  className="cp-product-photo-thumb"
                                  onError={() =>
                                    setBrokenProductImages((prev) => ({
                                      ...prev,
                                      [product.accurate_item_id]: true,
                                    }))
                                  }
                                />
                              ) : (
                                <div
                                  className="cp-product-photo-empty"
                                  data-testid={`cp-readonly-product-photo-placeholder-${product.accurate_item_id}`}
                                >
                                  <ImageIcon size={18} aria-hidden="true" />
                                  <span>Belum ada foto</span>
                                </div>
                              )}
                            </div>
                          </td>
                          <td>
                            <strong>{product.display_name}</strong>
                            <span className="cp-cell-sub mono">ID {product.accurate_item_id}</span>
                          </td>
                          {viewOnlyVersion.pricing_mode === MODE_FORMULA && (
                            <td>{product.hpp_snapshot ? formatRupiah(product.hpp_snapshot) : "—"}</td>
                          )}
                          {product.tier_prices.map((price, index) => (
                            <td key={index}>{price != null ? formatRupiah(price) : "—"}</td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {preview && (
            <div className="cp-preview-bar" data-testid="cp-preview">
              <strong>Preview {preview.valid ? "Valid" : "Tidak Valid"}</strong>
              {(preview.errors || []).length > 0 && (
                <span className="cp-preview-err">{preview.errors.join(" · ")}</span>
              )}
              {(preview.warnings || []).length > 0 && (
                <span className="muted small">{preview.warnings.join(" · ")}</span>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
