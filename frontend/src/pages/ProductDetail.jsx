import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Lock,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import InventoryValueMeaning from "../components/InventoryValueMeaning";
import { acceptValuationFocus, INVENTORY_VALUE_LINK_LABEL, inventoryValueItemPath, inventoryValueLinkVisible } from "../lib/inventoryValueLink";
import { movementLabel, mutationQtyColumns, openingReady, PHYSICAL_MOVEMENT_LABELS, warehouseChoices } from "../lib/physicalStockView";
import "./productDetail.css";

const TYPE_LABEL = {
  INVENTORY: "Barang",
  SERVICE: "Jasa",
  NON_INVENTORY: "Non-Persediaan",
  GROUP: "Grup",
};

function typeLabel(raw) {
  if (!raw) return "—";
  return TYPE_LABEL[raw] || raw;
}

export default function ProductDetail() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canEditConversion = hasPerm("current_stock.update") || hasPerm("master_items.sync");
  const canEditOpeningBalance = hasPerm("current_stock.update") || hasPerm("permissions.manage");

  // Active tab: 'umum' | 'stok' | 'mutasi'
  const tabParam = searchParams.get("tab");
  const [activeTab, setActiveTab] = useState(tabParam && ["umum", "stok", "mutasi"].includes(tabParam) ? tabParam : "umum");

  const setTab = (tab) => {
    setActiveTab(tab);
    setSearchParams({ tab });
  };

  // Product & Stock detail state
  const [productData, setProductData] = useState(null);
  const [loadingProduct, setLoadingProduct] = useState(true);
  const [productError, setProductError] = useState("");

  // Unit conversion form state
  const [txUom, setTxUom] = useState("");
  const [baseUom, setBaseUom] = useState("");
  const [convFactor, setConvFactor] = useState("1");
  const [savingSetting, setSavingSetting] = useState(false);
  const [settingSuccess, setSettingSuccess] = useState("");
  const [settingError, setSettingError] = useState("");

  const [physicalStock, setPhysicalStock] = useState(null);
  const [branches, setBranches] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [stockBranchId, setStockBranchId] = useState("");
  const [stockWarehouseId, setStockWarehouseId] = useState("");
  const [openingBranchId, setOpeningBranchId] = useState("");
  const [openingWarehouseId, setOpeningWarehouseId] = useState("");
  const [openingQty, setOpeningQty] = useState("");
  const [openingReason, setOpeningReason] = useState("");
  const [openingStatus, setOpeningStatus] = useState(null);
  const [openingStatusLoading, setOpeningStatusLoading] = useState(false);
  const [savingOpening, setSavingOpening] = useState(false);
  const [openingSuccess, setOpeningSuccess] = useState("");
  const [openingError, setOpeningError] = useState("");
  const [openingKey, setOpeningKey] = useState("");

  // Mutation ledger state
  const [mutations, setMutations] = useState([]);
  const [mutationLoading, setMutationLoading] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [searchQ, setSearchQ] = useState("");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [pageCount, setPageCount] = useState(1);
  const [totalMutations, setTotalMutations] = useState(0);

  // Selected mutation for drawer
  const [selectedMutation, setSelectedMutation] = useState(null);
  const [valueFocus, setValueFocus] = useState(null);
  const [valueFocusLoading, setValueFocusLoading] = useState(false);
  const [valueFocusError, setValueFocusError] = useState("");

  const masterItemId = String(productData?.item?.id || "").trim();
  const canOpenInventoryValue = inventoryValueLinkVisible({
    masterItemId,
    itemType: productData?.item?.item_type,
    isActive: productData?.item?.is_active,
    hasPerm,
  });

  useEffect(() => {
    if (!canOpenInventoryValue) {
      setValueFocus(null);
      setValueFocusError("");
      setValueFocusLoading(false);
      return undefined;
    }
    let cancelled = false;
    setValueFocusLoading(true);
    setValueFocusError("");
    api.inventoryValuationItem(masterItemId)
      .then((payload) => {
        if (!cancelled) setValueFocus(acceptValuationFocus(payload, masterItemId));
      })
      .catch((ex) => {
        if (cancelled) return;
        const det = ex.response?.data?.detail;
        setValueFocus(null);
        setValueFocusError(typeof det === "object" ? (det.message || det.code) : (det || "Nilai persediaan gagal dimuat"));
      })
      .finally(() => {
        if (!cancelled) setValueFocusLoading(false);
      });
    return () => { cancelled = true; };
  }, [canOpenInventoryValue, masterItemId]);

  // Load product detail & stock summary
  const loadProduct = useCallback(async () => {
    setLoadingProduct(true);
    setProductError("");
    try {
      const data = await api.masterItemDetail(id);
      setProductData(data);
      const us = data.unit_setting || {};
      const ss = data.stock_summary || {};
      setTxUom(us.transaction_uom || ss.transaction_uom || data.item?.unit || "PCS");
      setBaseUom(us.base_uom || ss.base_uom || data.item?.unit || "PCS");
      setConvFactor(String(us.conversion_factor ?? ss.conversion_factor ?? 1));
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setProductError(typeof det === "object" ? det.message : (det || "Gagal memuat detail produk"));
    } finally {
      setLoadingProduct(false);
    }
  }, [id]);

  // Load mutations
  const loadMutations = useCallback(async () => {
    setMutationLoading(true);
    setMutationError("");
    try {
      const params = {
        page,
        limit: pageSize,
      };
      if (startDate) params.from_date = startDate;
      if (endDate) params.to_date = endDate;
      if (typeFilter && typeFilter !== "ALL") params.movement_type = typeFilter;
      const res = await api.physicalMutations(id, params);
      setMutations(res.items || []);
      setTotalMutations(res.total || 0);
      setPageCount(res.page_count || 1);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setMutationError(typeof det === "object" ? det.message : (det || "Gagal memuat mutasi stok"));
    } finally {
      setMutationLoading(false);
    }
  }, [id, page, pageSize, startDate, endDate, typeFilter]);

  useEffect(() => {
    loadProduct();
  }, [loadProduct]);

  useEffect(() => {
    if (activeTab === "mutasi") {
      loadMutations();
    }
  }, [activeTab, loadMutations]);

  const loadPhysicalStock = useCallback(async () => {
    try {
      const res = await api.physicalStock(id);
      setPhysicalStock(res);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setPhysicalStock({ items: [], total_physical_qty: 0, base_unit: "" });
      setProductError(typeof det === "object" ? det.message : (det || "Gagal memuat stok fisik"));
    }
  }, [id]);

  useEffect(() => {
    loadPhysicalStock();
  }, [loadPhysicalStock]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.branches(), api.warehouses()])
      .then(([branchRes, warehouseRes]) => {
        if (cancelled) return;
        setBranches(branchRes.items || []);
        setWarehouses(warehouseRes.items || []);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!openingBranchId || !openingWarehouseId) {
      setOpeningStatus(null);
      return undefined;
    }
    let cancelled = false;
    setOpeningStatusLoading(true);
    api.physicalOpeningBalance(id, { branch_id: openingBranchId, warehouse_id: openingWarehouseId })
      .then((res) => { if (!cancelled) setOpeningStatus(res); })
      .catch(() => { if (!cancelled) setOpeningStatus(null); })
      .finally(() => { if (!cancelled) setOpeningStatusLoading(false); });
    return () => { cancelled = true; };
  }, [id, openingBranchId, openingWarehouseId, openingSuccess]);

  // Save unit conversion setting
  const handleSaveConversion = async (e) => {
    e.preventDefault();
    setSavingSetting(true);
    setSettingSuccess("");
    setSettingError("");

    const factorNum = parseFloat(convFactor.replace(",", "."));
    if (isNaN(factorNum) || factorNum <= 0) {
      setSettingError("Faktor konversi harus berupa angka lebih besar dari 0");
      setSavingSetting(false);
      return;
    }

    try {
      await api.saveUnitConversion(id, {
        transaction_uom: txUom.trim().toUpperCase(),
        base_uom: baseUom.trim().toUpperCase(),
        conversion_factor: factorNum,
      });
      setSettingSuccess("Pengaturan konversi satuan berhasil disimpan");
      await loadProduct();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setSettingError(typeof det === "object" ? det.message : (det || "Gagal menyimpan konversi satuan"));
    } finally {
      setSavingSetting(false);
    }
  };

  const handleSaveOpeningBalance = async (e) => {
    e.preventDefault();
    setSavingOpening(true);
    setOpeningSuccess("");
    setOpeningError("");
    if (!openingReady({ itemId: id, branchId: openingBranchId, warehouseId: openingWarehouseId, qty: openingQty })) {
      setOpeningError("Barang, cabang, gudang, dan qty stok fisik wajib diisi");
      setSavingOpening(false);
      return;
    }
    const key = openingKey || (globalThis.crypto?.randomUUID?.() || `saldo-${Date.now()}`);
    setOpeningKey(key);
    try {
      await api.createPhysicalOpeningBalance(id, {
        branch_id: openingBranchId,
        warehouse_id: openingWarehouseId,
        physical_qty: Number(String(openingQty).replace(",", ".")),
        reason: openingReason.trim(),
        idempotency_key: key,
      });
      setOpeningSuccess("Saldo awal stok fisik berhasil ditetapkan");
      setOpeningQty("");
      setOpeningKey("");
      await loadPhysicalStock();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setOpeningError(typeof det === "object" ? det.message : (det || "Gagal menetapkan saldo awal stok fisik"));
    } finally {
      setSavingOpening(false);
    }
  };

  const item = productData?.item || {};
  const stockSummary = productData?.stock_summary || {};
  const stockState = productData?.stock_state;
  const isStockInitialized = Boolean(stockState && stockState.initialized);
  const normalizedBaseUom = (stockSummary.base_uom || item.unit || "PCS").toUpperCase();

  const stockWarehouses = warehouseChoices(warehouses, stockBranchId).filter(
    (row) => !stockWarehouseId || row.id === stockWarehouseId,
  );
  const physicalByLocation = new Map(
    (physicalStock?.items || []).map((row) => [`${row.branch_id}:${row.warehouse_id}`, row]),
  );
  const stockRows = stockWarehouses.map((row) => {
    const hit = physicalByLocation.get(`${row.branch_id}:${row.id}`);
    const branch = branches.find((itemRow) => itemRow.id === row.branch_id);
    return {
      branch_id: row.branch_id,
      branch_code: hit?.branch_code || branch?.code || "",
      branch_name: hit?.branch_name || branch?.name || "",
      warehouse_id: row.id,
      warehouse_code: hit?.warehouse_code || row.code || "",
      warehouse_name: hit?.warehouse_name || row.name || "",
      physical_qty: hit ? Number(hit.physical_qty || 0) : 0,
    };
  });
  const openingWarehouses = warehouseChoices(warehouses, openingBranchId);
  const openingBranch = branches.find((row) => row.id === openingBranchId);
  const openingWarehouse = openingWarehouses.find((row) => row.id === openingWarehouseId);
  const openingEstablished = Boolean(openingStatus?.established);
  const visibleMutations = mutations.filter((row) => {
    const text = searchQ.trim().toLowerCase();
    if (!text) return true;
    return [row.reference_id, row.reason, row.movement_type, row.branch_code, row.warehouse_code]
      .join(" ")
      .toLowerCase()
      .includes(text);
  });

  return (
    <div className="product-detail-page" data-testid="product-detail-page">
      {/* Navigation breadcrumb */}
      <Link to="/persediaan/master-barang-jasa" className="product-detail-nav-back" data-testid="product-back-link">
        <ArrowLeft size={14} />
        <span>Kembali ke Barang/Jasa</span>
      </Link>

      {/* Global Load Error Banner */}
      {productError && (
        <div className="signal-strip" role="alert" style={{ marginBottom: 16 }}>
          <div><AlertCircle size={16} /><span>{productError}</span></div>
        </div>
      )}

      {/* PRODUCT HEADER */}
      <div className="product-header-card" data-testid="product-header">
        <div className="product-title-group">
          <h1 data-testid="product-name">{item.item_name || "Detail Produk"}</h1>
          <div className="product-badges">
            <span className="badge-code" data-testid="product-code">
              {item.item_code || "TANPA KODE"}
            </span>
            <span className="badge-type" data-testid="product-type">
              {typeLabel(item.item_type)}
            </span>
            <span
              className={`badge-status ${item.is_active === false ? "inactive" : "active"}`}
              data-testid="product-status"
            >
              {item.is_active === false ? "Nonaktif" : "Aktif"}
            </span>
            {item.accurate_item_id && (
              <span className="badge-accurate-ref" data-testid="product-accurate-id">
                Accurate ID: #{item.accurate_item_id}
              </span>
            )}
          </div>
        </div>
        {canOpenInventoryValue && (
          <Link
            className="ghost"
            to={inventoryValueItemPath(masterItemId)}
            data-testid="product-value-link"
          >
            {INVENTORY_VALUE_LINK_LABEL}
          </Link>
        )}
      </div>

      {/* STOCK SUMMARY CARDS */}
      <div className="stock-kpi-grid" data-testid="stock-kpi-grid">
        <div className="stock-kpi-card" data-testid="kpi-total-stock">
          <div className="kpi-label">Stok Fisik Saat Ini</div>
          <div className="kpi-value" data-testid="kpi-physical-qty">
            {physicalStock
              ? `${fmtQty(physicalStock.total_physical_qty || 0)} ${physicalStock.base_unit || normalizedBaseUom}`
              : "Memuat"}
          </div>
          <div className="kpi-sub">Per cabang dan gudang</div>
        </div>

        <div className="stock-kpi-card" data-testid="kpi-base-uom">
          <div className="kpi-label">Satuan Dasar</div>
          <div className="kpi-value">{physicalStock?.base_unit || normalizedBaseUom}</div>
          <div className="kpi-sub">Satuan stok fisik</div>
        </div>
      </div>

      {canOpenInventoryValue && (
        <div className="product-tab-card" data-testid="product-value-labels">
          <h3 className="card-section-title">Nilai dan rekonsiliasi</h3>
          <InventoryValueMeaning
            physicalText={physicalStock
              ? `${fmtQty(physicalStock.total_physical_qty || 0)} ${physicalStock.base_unit || normalizedBaseUom}`
              : "Memuat"}
            item={valueFocus?.item}
            covered={Boolean(valueFocus?.covered)}
            loading={valueFocusLoading}
            reason={valueFocusError || (!valueFocus?.covered ? valueFocus?.reason : "")}
            testIdPrefix="product-value"
          />
        </div>
      )}

      {/* TABS */}
      <div className="product-tabs-bar" data-testid="product-tabs">
        <button
          type="button"
          className={`product-tab-btn ${activeTab === "umum" ? "is-active" : ""}`}
          onClick={() => setTab("umum")}
          data-testid="tab-btn-umum"
        >
          Umum
        </button>
        <button
          type="button"
          className={`product-tab-btn ${activeTab === "stok" ? "is-active" : ""}`}
          onClick={() => setTab("stok")}
          data-testid="tab-btn-stok"
        >
          Stok
        </button>
        <button
          type="button"
          className={`product-tab-btn ${activeTab === "mutasi" ? "is-active" : ""}`}
          onClick={() => setTab("mutasi")}
          data-testid="tab-btn-mutasi"
        >
          Mutasi
        </button>
      </div>

      {/* TAB 1: UMUM */}
      {activeTab === "umum" && (
        <div data-testid="tab-content-umum">
          <div className="product-tab-card">
            <h3 className="card-section-title">Informasi Produk</h3>
            <table className="prod-table" style={{ width: "100%", maxWidth: 600 }}>
              <tbody>
                <tr>
                  <td style={{ width: 180, color: "#64748b", fontWeight: 500 }}>Nama Barang</td>
                  <td style={{ fontWeight: 600 }}>{item.item_name || "—"}</td>
                </tr>
                <tr>
                  <td style={{ color: "#64748b", fontWeight: 500 }}>Kode Barang / SKU</td>
                  <td className="mono">{item.item_code || "—"}</td>
                </tr>
                <tr>
                  <td style={{ color: "#64748b", fontWeight: 500 }}>Jenis</td>
                  <td>{typeLabel(item.item_type)}</td>
                </tr>
                <tr>
                  <td style={{ color: "#64748b", fontWeight: 500 }}>Status</td>
                  <td>{item.is_active === false ? "Nonaktif" : "Aktif"}</td>
                </tr>
                {item.accurate_item_id && (
                  <tr>
                    <td style={{ color: "#64748b", fontWeight: 500 }}>Referensi Accurate</td>
                    <td className="mono">ID #{item.accurate_item_id}</td>
                  </tr>
                )}
                {item.last_synced_at && (
                  <tr>
                    <td style={{ color: "#64748b", fontWeight: 500 }}>Terakhir Sinkronisasi</td>
                    <td>{formatDate(item.last_synced_at)}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* SETTING KONVERSI SATUAN */}
          <div className="product-tab-card" data-testid="unit-conversion-card">
            <h3 className="card-section-title">Setting Konversi Satuan</h3>
            <p style={{ fontSize: 13, color: "#64748b", marginBottom: 16 }}>
              Menentukan relasi antara Satuan Transaksi / Jual dengan Satuan Dasar persediaan untuk produk ini.
            </p>

            {settingSuccess && (
              <div className="signal-strip" style={{ marginBottom: 14, background: "#ecfdf5", borderColor: "#a7f3d0", color: "#065f46" }}>
                <div><CheckCircle2 size={16} /><span>{settingSuccess}</span></div>
              </div>
            )}
            {settingError && (
              <div className="signal-strip" style={{ marginBottom: 14 }} role="alert">
                <div><AlertCircle size={16} /><span>{settingError}</span></div>
              </div>
            )}

            <form onSubmit={handleSaveConversion}>
              <div className="unit-setting-grid">
                <div className="form-field">
                  <label htmlFor="tx-uom-input">Satuan Transaksi / Jual</label>
                  <input
                    id="tx-uom-input"
                    type="text"
                    value={txUom}
                    onChange={(e) => setTxUom(e.target.value)}
                    placeholder="Contoh: PACK / BOX"
                    disabled={!canEditConversion || savingSetting}
                    data-testid="input-tx-uom"
                  />
                </div>

                <div className="form-field">
                  <label htmlFor="base-uom-input">Satuan Dasar</label>
                  <input
                    id="base-uom-input"
                    type="text"
                    value={baseUom}
                    onChange={(e) => setBaseUom(e.target.value)}
                    placeholder="Contoh: PCS"
                    disabled={!canEditConversion || savingSetting || isStockInitialized}
                    data-testid="input-base-uom"
                  />
                  {isStockInitialized && (
                    <span style={{ fontSize: 11, color: "#64748b", display: "inline-flex", alignItems: "center", gap: 4, marginTop: 4 }}>
                      <Lock size={12} /> Terkunci: Saldo awal stok fisik sudah ditetapkan
                    </span>
                  )}
                </div>

                <div className="form-field">
                  <label htmlFor="factor-input">Faktor Konversi</label>
                  <input
                    id="factor-input"
                    type="text"
                    value={convFactor}
                    onChange={(e) => setConvFactor(e.target.value)}
                    placeholder="Contoh: 250"
                    disabled={!canEditConversion || savingSetting || txUom.trim().toUpperCase() === baseUom.trim().toUpperCase()}
                    data-testid="input-conversion-factor"
                  />
                </div>
              </div>

              {/* Live Conversion Preview */}
              <div className="conversion-preview-box" data-testid="conversion-preview">
                <span>Konversi Terdefinisi:</span>
                <strong>
                  1 {txUom.trim().toUpperCase() || "UNIT"} ={" "}
                  {txUom.trim().toUpperCase() === baseUom.trim().toUpperCase() ? "1" : convFactor}{" "}
                  {baseUom.trim().toUpperCase() || "PCS"}
                </strong>
              </div>

              {canEditConversion && (
                <button
                  type="submit"
                  className="primary"
                  disabled={savingSetting}
                  data-testid="btn-save-conversion"
                >
                  <RefreshCw size={14} className={savingSetting ? "spin" : ""} />
                  {savingSetting ? "Menyimpan…" : "Simpan Konversi Satuan"}
                </button>
              )}
            </form>
          </div>
        </div>
      )}

      {/* TAB 2: STOK */}
      {activeTab === "stok" && (
        <div data-testid="tab-content-stok">
          <div className="product-tab-card">
            <h3 className="card-section-title">Stok Fisik Saat Ini</h3>
            <div className="prod-filter-row" style={{ marginBottom: 12 }}>
              <select value={stockBranchId} onChange={(e) => { setStockBranchId(e.target.value); setStockWarehouseId(""); }} data-testid="product-stock-branch" aria-label="Cabang">
                <option value="">Semua Cabang</option>
                {branches.map((row) => <option key={row.id} value={row.id}>{row.code} — {row.name}</option>)}
              </select>
              <select value={stockWarehouseId} onChange={(e) => setStockWarehouseId(e.target.value)} data-testid="product-stock-warehouse" aria-label="Gudang">
                <option value="">Semua Gudang</option>
                {warehouseChoices(warehouses, stockBranchId).map((row) => <option key={row.id} value={row.id}>{row.code} — {row.name}</option>)}
              </select>
            </div>
            <table className="prod-table" data-testid="product-physical-stock-table">
              <thead>
                <tr>
                  <th>Cabang</th>
                  <th>Gudang</th>
                  <th>Stok Fisik</th>
                  <th>Satuan Dasar</th>
                </tr>
              </thead>
              <tbody>
                {stockRows.length === 0 && (
                  <tr><td colSpan={4} data-testid="product-stock-empty">{stockBranchId ? "Cabang ini belum memiliki gudang." : "Belum ada gudang."}</td></tr>
                )}
                {stockRows.map((row) => (
                  <tr key={`${row.branch_id}-${row.warehouse_id}`} data-testid={`product-stock-row-${row.warehouse_id}`}>
                    <td>{row.branch_code || row.branch_name || "—"}</td>
                    <td>{row.warehouse_code || row.warehouse_name || "—"}</td>
                    <td data-testid={`product-stock-qty-${row.warehouse_id}`}>{fmtQty(row.physical_qty)}</td>
                    <td>{physicalStock?.base_unit || normalizedBaseUom}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="product-tab-card" data-testid="opening-balance-card">
            <h3 className="card-section-title">Saldo Awal</h3>
            <p style={{ fontSize: 13, color: "#64748b" }}>Menetapkan stok fisik awal untuk satu barang, cabang, dan gudang. Penetapan hanya satu kali.</p>
            {openingSuccess && (
              <div className="signal-strip" style={{ marginBottom: 14, background: "#ecfdf5", borderColor: "#a7f3d0", color: "#065f46" }}>
                <div><CheckCircle2 size={16} /><span>{openingSuccess}</span></div>
              </div>
            )}
            {openingError && (
              <div className="signal-strip" role="alert" style={{ marginBottom: 14 }}>
                <div><AlertCircle size={16} /><span>{openingError}</span></div>
              </div>
            )}
            <form onSubmit={handleSaveOpeningBalance}>
              <div className="unit-setting-grid">
                <div className="form-field">
                  <label htmlFor="opening-branch">Cabang</label>
                  <select id="opening-branch" value={openingBranchId} onChange={(e) => { setOpeningBranchId(e.target.value); setOpeningWarehouseId(""); }} data-testid="opening-branch" disabled={openingEstablished}>
                    <option value="">Pilih cabang</option>
                    {branches.map((row) => <option key={row.id} value={row.id}>{row.code} — {row.name}</option>)}
                  </select>
                </div>
                <div className="form-field">
                  <label htmlFor="opening-warehouse">Gudang</label>
                  <select id="opening-warehouse" value={openingWarehouseId} onChange={(e) => setOpeningWarehouseId(e.target.value)} data-testid="opening-warehouse" disabled={openingEstablished}>
                    <option value="">Pilih gudang</option>
                    {openingWarehouses.map((row) => <option key={row.id} value={row.id}>{row.code} — {row.name}</option>)}
                  </select>
                </div>
              </div>
              {openingEstablished ? (
                <div data-testid="opening-established">
                  <strong>SALDO AWAL SUDAH DITETAPKAN</strong>
                  <p data-testid="badge-opening-status">Barang: {item.item_name || item.item_code || id}</p>
                  <p>Cabang: {openingBranch?.name || openingBranch?.code || openingBranchId}</p>
                  <p>Gudang: {openingWarehouse?.name || openingWarehouse?.code || openingWarehouseId}</p>
                  <p data-testid="opening-display-total">Saldo Awal: {fmtQty(openingStatus.opening_qty)} {physicalStock?.base_unit || normalizedBaseUom}</p>
                  <p>Satuan: {physicalStock?.base_unit || normalizedBaseUom}</p>
                  <p>Waktu Penetapan: {openingStatus.occurred_at ? formatDate(openingStatus.occurred_at) : "—"}</p>
                </div>
              ) : (
                <>
                  <div className="unit-setting-grid">
                    <div className="form-field">
                      <label htmlFor="opening-qty">Qty Stok Fisik</label>
                      <input id="opening-qty" value={openingQty} onChange={(e) => setOpeningQty(e.target.value)} data-testid="input-opening-qty" disabled={!canEditOpeningBalance || savingOpening || openingStatusLoading} />
                    </div>
                    <div className="form-field">
                      <label htmlFor="opening-unit">Satuan Dasar</label>
                      <input id="opening-unit" value={physicalStock?.base_unit || normalizedBaseUom} readOnly data-testid="opening-base-unit" />
                    </div>
                    <div className="form-field">
                      <label htmlFor="opening-reason">Catatan</label>
                      <input id="opening-reason" value={openingReason} onChange={(e) => setOpeningReason(e.target.value)} data-testid="input-opening-reason" disabled={!canEditOpeningBalance || savingOpening} />
                    </div>
                  </div>
                  {canEditOpeningBalance ? (
                    <button type="submit" className="primary" disabled={savingOpening || openingStatusLoading || !openingReady({ itemId: id, branchId: openingBranchId, warehouseId: openingWarehouseId, qty: openingQty })} data-testid="btn-save-opening">
                      <RefreshCw size={14} className={savingOpening ? "spin" : ""} />
                      {savingOpening ? "Menyimpan…" : "Tetapkan Saldo Awal"}
                    </button>
                  ) : (
                    <div style={{ fontSize: 12, color: "#64748b" }}>Anda memerlukan izin current_stock.update atau permissions.manage untuk menetapkan saldo awal.</div>
                  )}
                </>
              )}
            </form>
          </div>
        </div>
      )}

      {/* TAB 3: MUTASI */}
      {activeTab === "mutasi" && (
        <div data-testid="tab-content-mutasi">
          {/* Controls Bar */}
          <div className="mutation-toolbar" data-testid="mutation-toolbar">
            <div className="mutation-filter-row">
              <div className="date-range-inputs">
                <span>Periode:</span>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => { setStartDate(e.target.value); setPage(1); }}
                  data-testid="input-start-date"
                  aria-label="Tanggal Mulai"
                />
                <span>s/d</span>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => { setEndDate(e.target.value); setPage(1); }}
                  data-testid="input-end-date"
                  aria-label="Tanggal Akhir"
                />
                <button
                  type="button"
                  className="ghost sm"
                  onClick={loadMutations}
                  disabled={mutationLoading}
                  data-testid="btn-refresh-mutations"
                >
                  <RefreshCw size={13} className={mutationLoading ? "spin" : ""} />
                </button>
              </div>

              <div className="filter-btn-group" data-testid="type-filter-group">
                {[{ key: "ALL", label: "Semua" }, ...Object.entries(PHYSICAL_MOVEMENT_LABELS).map(([key, label]) => ({ key, label }))].map((tf) => (
                  <button
                    key={tf.key}
                    type="button"
                    className={`filter-pill-btn ${typeFilter === tf.key ? "is-active" : ""}`}
                    onClick={() => { setTypeFilter(tf.key); setPage(1); }}
                    data-testid={`filter-type-${tf.key}`}
                  >
                    {tf.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="search-inline" style={{ maxWidth: 300 }}>
              <Search size={14} />
              <input
                type="text"
                value={searchQ}
                onChange={(e) => setSearchQ(e.target.value)}
                placeholder="Cari referensi / keterangan"
                data-testid="input-search-mutation"
                aria-label="Cari mutasi"
              />
              {searchQ && (
                <button type="button" className="ghost sm" onClick={() => setSearchQ("")} aria-label="Hapus cari">
                  <X size={12} />
                </button>
              )}
            </div>
          </div>

          {mutationError && (
            <div className="signal-strip" style={{ marginBottom: 14 }} role="alert">
              <div><AlertCircle size={16} /><span>{mutationError}</span></div>
            </div>
          )}

          <div className="mutation-table-container">
            <table className="mutation-table" data-testid="mutation-table">
              <thead>
                <tr>
                  <th>Tanggal</th>
                  <th>Referensi</th>
                  <th>Tipe Mutasi</th>
                  <th>Cabang</th>
                  <th>Gudang</th>
                  <th>Keterangan</th>
                  <th style={{ textAlign: "right" }}>Masuk</th>
                  <th style={{ textAlign: "right" }}>Keluar</th>
                  <th style={{ textAlign: "right" }}>Saldo</th>
                </tr>
              </thead>
              <tbody data-testid="mutation-tbody">
                {mutationLoading && (
                  <tr>
                    <td colSpan={9} data-testid="mutation-loading">Memuat mutasi persediaan…</td>
                  </tr>
                )}
                {!mutationLoading && visibleMutations.length === 0 && (
                  <tr>
                    <td colSpan={9} data-testid="mutation-empty">Belum ada riwayat mutasi stok untuk filter ini.</td>
                  </tr>
                )}
                {!mutationLoading && visibleMutations.map((m) => {
                  const qty = mutationQtyColumns(m.qty_delta);
                  return (
                    <tr
                      key={m.id}
                      className="mutation-row"
                      onClick={() => setSelectedMutation(m)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setSelectedMutation(m);
                        }
                      }}
                      tabIndex={0}
                      data-testid={`mutation-row-${m.id}`}
                      role="button"
                      aria-label={`Detail mutasi ${m.reference_id || m.id}`}
                    >
                      <td data-testid={`mutation-date-${m.id}`}>{m.occurred_at ? formatDate(m.occurred_at) : "—"}</td>
                      <td className="mono" data-testid={`mutation-ref-${m.id}`}>{m.reference_id || "—"}</td>
                      <td data-testid={`mutation-type-${m.id}`}>{movementLabel(m.movement_type)}</td>
                      <td>{m.branch_code || m.branch_name || "—"}</td>
                      <td>{m.warehouse_code || m.warehouse_name || "—"}</td>
                      <td data-testid={`mutation-desc-${m.id}`}>{m.reason || "—"}</td>
                      <td style={{ textAlign: "right" }} data-testid={`mutation-in-${m.id}`}>{qty.masuk == null ? "—" : fmtQty(qty.masuk)}</td>
                      <td style={{ textAlign: "right" }} data-testid={`mutation-out-${m.id}`}>{qty.keluar == null ? "—" : fmtQty(qty.keluar)}</td>
                      <td style={{ textAlign: "right" }} data-testid={`mutation-bal-${m.id}`}>{m.balance_after == null ? "—" : fmtQty(m.balance_after)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {pageCount > 1 && (
            <div className="master-pager" style={{ marginTop: 14 }}>
              <button className="ghost sm" disabled={page <= 1 || mutationLoading} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
              <span className="muted small">Halaman {page} dari {pageCount} · Total {totalMutations} transaksi</span>
              <button className="ghost sm" disabled={page >= pageCount || mutationLoading} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
            </div>
          )}
        </div>
      )}

      {selectedMutation && (
        <div className="mutation-drawer-overlay" onClick={() => setSelectedMutation(null)} data-testid="mutation-drawer-backdrop">
          <div className="mutation-drawer" onClick={(e) => e.stopPropagation()} data-testid="mutation-detail-drawer">
            <div className="drawer-header">
              <h3>Detail Mutasi Stok</h3>
              <button type="button" className="ghost sm" onClick={() => setSelectedMutation(null)} aria-label="Tutup Detail" data-testid="btn-close-drawer">
                <X size={16} />
              </button>
            </div>
            <div className="drawer-body">
              <div className="drawer-prop-list">
                <div className="drawer-prop-row"><span className="prop-label">Tanggal</span><span className="prop-value">{selectedMutation.occurred_at ? formatDate(selectedMutation.occurred_at) : "—"}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Tipe Mutasi</span><span className="prop-value">{movementLabel(selectedMutation.movement_type)}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Cabang</span><span className="prop-value">{selectedMutation.branch_code || selectedMutation.branch_name || "—"}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Gudang</span><span className="prop-value">{selectedMutation.warehouse_code || selectedMutation.warehouse_name || "—"}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Masuk</span><span className="prop-value">{mutationQtyColumns(selectedMutation.qty_delta).masuk == null ? "—" : fmtQty(mutationQtyColumns(selectedMutation.qty_delta).masuk)}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Keluar</span><span className="prop-value">{mutationQtyColumns(selectedMutation.qty_delta).keluar == null ? "—" : fmtQty(mutationQtyColumns(selectedMutation.qty_delta).keluar)}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Saldo Sebelum</span><span className="prop-value" data-testid="drawer-balance-before">{selectedMutation.balance_before == null ? "—" : fmtQty(selectedMutation.balance_before)}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Saldo</span><span className="prop-value" data-testid="drawer-balance-after">{selectedMutation.balance_after == null ? "—" : fmtQty(selectedMutation.balance_after)}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Referensi</span><span className="prop-value mono">{selectedMutation.reference_id || "—"}</span></div>
                <div className="drawer-prop-row"><span className="prop-label">Keterangan</span><span className="prop-value">{selectedMutation.reason || "—"}</span></div>
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
