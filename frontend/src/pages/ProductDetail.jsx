import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  ExternalLink,
  Filter,
  Lock,
  Package,
  RefreshCw,
  Search,
  Sliders,
  X,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
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
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { hasPerm, user } = useAuth();
  const canEditConversion = hasPerm("current_stock.update") || hasPerm("master_items.sync");
  const canEditOpeningBalance = hasPerm("current_stock.update") || hasPerm("permissions.manage");
  const canCorrectOpening = String(user?.role || "").toLowerCase() === "holding_team";

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

  // Opening balance form state
  const [unprintedInput, setUnprintedInput] = useState("");
  const [printedInput, setPrintedInput] = useState("");
  const [savingOpening, setSavingOpening] = useState(false);
  const [openingSuccess, setOpeningSuccess] = useState("");
  const [openingError, setOpeningError] = useState("");
  const [showCorrectModal, setShowCorrectModal] = useState(false);
  const [corrUnprinted, setCorrUnprinted] = useState("");
  const [corrPrinted, setCorrPrinted] = useState("");
  const [corrReason, setCorrReason] = useState("");
  const [corrRequestId, setCorrRequestId] = useState("");
  const [savingCorrection, setSavingCorrection] = useState(false);
  const [correctionError, setCorrectionError] = useState("");

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
        page_size: pageSize,
      };
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      if (typeFilter && typeFilter !== "ALL") params.movement_type = typeFilter;
      if (searchQ.trim()) params.q = searchQ.trim();

      const res = await api.listProductMutations(id, params);
      setMutations(res.items || []);
      setTotalMutations(res.total || 0);
      setPageCount(res.page_count || 1);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setMutationError(typeof det === "object" ? det.message : (det || "Gagal memuat mutasi stok"));
    } finally {
      setMutationLoading(false);
    }
  }, [id, page, pageSize, startDate, endDate, typeFilter, searchQ]);

  useEffect(() => {
    loadProduct();
  }, [loadProduct]);

  useEffect(() => {
    if (activeTab === "mutasi") {
      loadMutations();
    }
  }, [activeTab, loadMutations]);

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

  // Save physical stock opening balance
  const handleSaveOpeningBalance = async (e) => {
    e.preventDefault();
    setSavingOpening(true);
    setOpeningSuccess("");
    setOpeningError("");

    const unprintedStr = unprintedInput.trim().replace(",", ".");
    const printedStr = printedInput.trim().replace(",", ".");

    if (unprintedStr === "") {
      setOpeningError("Kuantitas stok cup belum dicetak wajib diisi");
      setSavingOpening(false);
      return;
    }
    if (printedStr === "") {
      setOpeningError("Kuantitas stok cup sudah dicetak wajib diisi");
      setSavingOpening(false);
      return;
    }

    const unprinted = parseFloat(unprintedStr);
    const printed = parseFloat(printedStr);

    if (isNaN(unprinted) || unprinted < 0) {
      setOpeningError("Kuantitas stok cup belum dicetak harus berupa angka non-negatif");
      setSavingOpening(false);
      return;
    }
    if (isNaN(printed) || printed < 0) {
      setOpeningError("Kuantitas stok cup sudah dicetak harus berupa angka non-negatif");
      setSavingOpening(false);
      return;
    }

    const total = unprinted + printed;

    try {
      await api.initOpeningBalance(id, {
        unprinted_qty: unprinted,
        printed_qty: printed,
        total_qty: total,
        base_uom: normalizedBaseUom,
      });
      setOpeningSuccess("Saldo awal stok fisik berhasil ditetapkan");
      setUnprintedInput("");
      setPrintedInput("");
      await loadProduct();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setOpeningError(typeof det === "object" ? det.message : (det || "Gagal menetapkan saldo awal stok fisik"));
    } finally {
      setSavingOpening(false);
    }
  };

  const openCorrectModal = () => {
    if (!canCorrectOpening || !isStockInitialized) return;
    setCorrUnprinted(String(stockState?.unprinted_qty ?? ""));
    setCorrPrinted(String(stockState?.printed_qty ?? ""));
    setCorrReason("");
    setCorrRequestId(
      (typeof crypto !== "undefined" && crypto.randomUUID)
        ? crypto.randomUUID()
        : `ksa-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    );
    setCorrectionError("");
    setShowCorrectModal(true);
  };

  const handleConfirmCorrection = async () => {
    if (!canCorrectOpening || savingCorrection) return;
    setSavingCorrection(true);
    setCorrectionError("");
    setOpeningError("");
    const unprintedStr = corrUnprinted.trim().replace(",", ".");
    const printedStr = corrPrinted.trim().replace(",", ".");
    const reason = corrReason.trim();
    if (!reason) {
      setCorrectionError("Alasan koreksi wajib diisi");
      setSavingCorrection(false);
      return;
    }
    const unprinted = parseFloat(unprintedStr);
    const printed = parseFloat(printedStr);
    if (unprintedStr === "" || isNaN(unprinted) || unprinted < 0) {
      setCorrectionError("Belum Dicetak harus berupa angka non-negatif");
      setSavingCorrection(false);
      return;
    }
    if (printedStr === "" || isNaN(printed) || printed < 0) {
      setCorrectionError("Sudah Dicetak harus berupa angka non-negatif");
      setSavingCorrection(false);
      return;
    }
    try {
      await api.correctOpeningBalance(id, {
        unprinted_qty: unprinted,
        printed_qty: printed,
        total_qty: unprinted + printed,
        note: reason,
        client_request_id: corrRequestId,
      });
      setOpeningSuccess("Koreksi saldo awal stok fisik berhasil");
      setShowCorrectModal(false);
      await loadProduct();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setCorrectionError(typeof det === "object" ? det.message : (det || "Gagal mengkoreksi saldo awal"));
    } finally {
      setSavingCorrection(false);
    }
  };

  const item = productData?.item || {};
  const stockSummary = productData?.stock_summary || {};
  const stockState = productData?.stock_state;
  const isStockInitialized = Boolean(stockState && stockState.initialized);
  const normalizedBaseUom = (stockSummary.base_uom || item.unit || "PCS").toUpperCase();

  const parseQtyInput = (v) => {
    if (!v || typeof v !== "string") return 0;
    const n = parseFloat(v.trim().replace(",", "."));
    return isNaN(n) || n < 0 ? 0 : n;
  };
  const computedOpeningTotal = parseQtyInput(unprintedInput) + parseQtyInput(printedInput);

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
      </div>

      {/* STOCK SUMMARY CARDS */}
      <div className="stock-kpi-grid" data-testid="stock-kpi-grid">
        <div className="stock-kpi-card" data-testid="kpi-total-stock">
          <div className="kpi-label">Stok Total Saat Ini</div>
          <div className="kpi-value">
            {stockSummary.stock_total != null
              ? `${fmtQty(stockSummary.stock_total)} ${normalizedBaseUom}`
              : "Belum tersedia"}
          </div>
          <div className="kpi-sub">
            {stockSummary.stock_total != null ? "Belum dicetak + sudah dicetak" : "Otoritas stok fisik belum ditetapkan"}
          </div>
        </div>

        <div className="stock-kpi-card" data-testid="kpi-unprinted-cup-stock">
          <div className="kpi-label">Stok Cup Belum Dicetak</div>
          <div className="kpi-value">
            {stockSummary.stock_unprinted_cup != null
              ? `${fmtQty(stockSummary.stock_unprinted_cup)} ${normalizedBaseUom}`
              : "Belum tersedia"}
          </div>
          <div className="kpi-sub">
            {stockSummary.stock_unprinted_cup != null
              ? (stockSummary.stock_printed_cup != null
                  ? `Sudah dicetak: ${fmtQty(stockSummary.stock_printed_cup)} ${normalizedBaseUom}`
                  : "Fisik polos / belum dicetak")
              : "Otoritas stok fisik belum ditetapkan"}
          </div>
        </div>

        <div className="stock-kpi-card" data-testid="kpi-base-uom">
          <div className="kpi-label">Satuan Dasar</div>
          <div className="kpi-value">{normalizedBaseUom}</div>
          <div className="kpi-sub">Kanonikal persediaan</div>
        </div>
      </div>

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
            <h3 className="card-section-title">Ringkasan Parameter Stok</h3>
            <table className="prod-table" style={{ width: "100%", maxWidth: 640 }}>
              <tbody>
                <tr>
                  <td style={{ width: 220, color: "#64748b", fontWeight: 500 }}>Stok Total Saat Ini</td>
                  <td style={{ fontWeight: 700, fontSize: 16 }}>
                    {stockSummary.stock_total != null
                      ? `${fmtQty(stockSummary.stock_total)} ${normalizedBaseUom}`
                      : "Belum tersedia"}
                  </td>
                </tr>
                <tr>
                  <td style={{ color: "#64748b", fontWeight: 500 }}>Stok Cup Belum Dicetak</td>
                  <td style={{ fontWeight: 600 }}>
                    {stockSummary.stock_unprinted_cup != null
                      ? `${fmtQty(stockSummary.stock_unprinted_cup)} ${normalizedBaseUom}`
                      : "Belum tersedia"}
                  </td>
                </tr>
                <tr>
                  <td style={{ color: "#64748b", fontWeight: 500 }}>Stok Cup Sudah Dicetak</td>
                  <td style={{ fontWeight: 600 }}>
                    {stockSummary.stock_printed_cup != null
                      ? `${fmtQty(stockSummary.stock_printed_cup)} ${normalizedBaseUom}`
                      : "Belum tersedia"}
                  </td>
                </tr>
                <tr>
                  <td style={{ color: "#64748b", fontWeight: 500 }}>Satuan Dasar (Base UOM)</td>
                  <td className="mono">{normalizedBaseUom}</td>
                </tr>
                <tr>
                  <td style={{ color: "#64748b", fontWeight: 500 }}>Satuan Transaksi Aktif</td>
                  <td className="mono">{stockSummary.transaction_uom || normalizedBaseUom}</td>
                </tr>
                <tr>
                  <td style={{ color: "#64748b", fontWeight: 500 }}>Konversi Aktif</td>
                  <td>
                    1 {stockSummary.transaction_uom || normalizedBaseUom} ={" "}
                    {stockSummary.conversion_factor ?? 1} {normalizedBaseUom}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* SALDO AWAL STOK FISIK */}
          <div className="product-tab-card" data-testid="opening-balance-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
              <h3 className="card-section-title" style={{ margin: 0 }}>Saldo Awal Stok Fisik</h3>
              {isStockInitialized ? (
                <span className="badge-status active" data-testid="badge-opening-status" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <CheckCircle2 size={12} /> Sudah Ditetapkan
                </span>
              ) : (
                <span className="badge-status inactive" data-testid="badge-opening-status" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <AlertCircle size={12} /> Belum Ditetapkan
                </span>
              )}
            </div>

            <p style={{ fontSize: 13, color: "#64748b", marginBottom: 16 }}>
              Menetapkan posisi fisik awal persediaan per produk dan mencatat mutasi <code>SALDO_AWAL</code> di ledger.
              Penetapan saldo awal hanya dapat dilakukan satu kali dan mengunci Satuan Dasar kanonikal (<code>{normalizedBaseUom}</code>).
            </p>

            {isStockInitialized ? (
              <div>
                <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 6, padding: 16, marginBottom: 16 }}>
                  <table className="prod-table" style={{ width: "100%", maxWidth: 640 }}>
                    <tbody>
                      <tr>
                        <td style={{ width: 220, color: "#64748b", fontWeight: 500 }}>Total Saldo Awal</td>
                        <td style={{ fontWeight: 700, fontSize: 16 }} data-testid="opening-display-total">
                          {fmtQty(stockState.total_qty)} {stockState.base_uom || normalizedBaseUom}
                        </td>
                      </tr>
                      <tr>
                        <td style={{ color: "#64748b", fontWeight: 500 }}>Stok Belum Dicetak</td>
                        <td style={{ fontWeight: 600 }} data-testid="opening-display-unprinted">
                          {fmtQty(stockState.unprinted_qty)} {stockState.base_uom || normalizedBaseUom}
                        </td>
                      </tr>
                      <tr>
                        <td style={{ color: "#64748b", fontWeight: 500 }}>Stok Sudah Dicetak</td>
                        <td style={{ fontWeight: 600 }} data-testid="opening-display-printed">
                          {fmtQty(stockState.printed_qty)} {stockState.base_uom || normalizedBaseUom}
                        </td>
                      </tr>
                      <tr>
                        <td style={{ color: "#64748b", fontWeight: 500 }}>Waktu Penetapan</td>
                        <td>{formatDate(stockState.initialized_at)}</td>
                      </tr>
                      <tr>
                        <td style={{ color: "#64748b", fontWeight: 500 }}>Ditetapkan Oleh</td>
                        <td>{stockState.initialized_by?.name || stockState.initialized_by?.email || "Petugas"}</td>
                      </tr>
                      <tr>
                        <td style={{ color: "#64748b", fontWeight: 500 }}>Status Otoritas</td>
                        <td>
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "#0f766e", fontWeight: 600 }}>
                            <Lock size={13} /> Terkunci Permanen (1 Kali Penetapan)
                          </span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div style={{ fontSize: 12.5, color: "#475569", background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 6, padding: "10px 14px", display: "flex", alignItems: "center", gap: 8 }}>
                  <CheckCircle2 size={16} color="#16a34a" style={{ flexShrink: 0 }} />
                  <span>
                    Saldo awal fisik telah aktif sebagai otoritas stok kanonikal. Mutasi berikutnya akan memperbarui saldo fisik secara berurutan di ledger.
                  </span>
                </div>
                {openingSuccess && (
                  <div className="signal-strip" style={{ marginTop: 14, background: "#ecfdf5", borderColor: "#a7f3d0", color: "#065f46" }}>
                    <div><CheckCircle2 size={16} /><span>{openingSuccess}</span></div>
                  </div>
                )}
                {canCorrectOpening && (
                  <button
                    type="button"
                    className="primary"
                    style={{ marginTop: 16 }}
                    onClick={openCorrectModal}
                    data-testid="btn-correct-opening"
                  >
                    Koreksi Saldo Awal
                  </button>
                )}
              </div>
            ) : (
              <div>
                <div style={{ fontSize: 12.5, color: "#b45309", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 6, padding: "10px 14px", marginBottom: 16, display: "flex", alignItems: "flex-start", gap: 8 }}>
                  <AlertCircle size={16} color="#d97706" style={{ flexShrink: 0, marginTop: 2 }} />
                  <div>
                    <strong>Penting:</strong> Saldo awal stok fisik hanya dapat ditetapkan <strong>satu kali</strong>. Setelah ditetapkan, Satuan Dasar (<code>{normalizedBaseUom}</code>) tidak dapat diubah lagi. Pastikan angka yang dimasukkan sesuai dengan stok fisik di gudang.
                  </div>
                </div>

                {openingSuccess && (
                  <div className="signal-strip" style={{ marginBottom: 14, background: "#ecfdf5", borderColor: "#a7f3d0", color: "#065f46" }}>
                    <div><CheckCircle2 size={16} /><span>{openingSuccess}</span></div>
                  </div>
                )}
                {openingError && (
                  <div className="signal-strip" style={{ marginBottom: 14 }} role="alert">
                    <div><AlertCircle size={16} /><span>{openingError}</span></div>
                  </div>
                )}

                <form onSubmit={handleSaveOpeningBalance}>
                  <div className="unit-setting-grid">
                    <div className="form-field">
                      <label htmlFor="opening-unprinted-input">
                        Stok Belum Dicetak ({normalizedBaseUom}) <span style={{ color: "#dc2626" }}>*</span>
                      </label>
                      <input
                        id="opening-unprinted-input"
                        type="text"
                        value={unprintedInput}
                        onChange={(e) => setUnprintedInput(e.target.value)}
                        placeholder="Contoh: 5000"
                        disabled={!canEditOpeningBalance || savingOpening}
                        data-testid="input-opening-unprinted"
                      />
                    </div>

                    <div className="form-field">
                      <label htmlFor="opening-printed-input">
                        Stok Sudah Dicetak ({normalizedBaseUom}) <span style={{ color: "#dc2626" }}>*</span>
                      </label>
                      <input
                        id="opening-printed-input"
                        type="text"
                        value={printedInput}
                        onChange={(e) => setPrintedInput(e.target.value)}
                        placeholder="Contoh: 0"
                        disabled={!canEditOpeningBalance || savingOpening}
                        data-testid="input-opening-printed"
                      />
                    </div>

                    <div className="form-field">
                      <label htmlFor="opening-base-uom">Satuan Dasar</label>
                      <input
                        id="opening-base-uom"
                        type="text"
                        value={normalizedBaseUom}
                        disabled
                        style={{ background: "#f1f5f9", color: "#64748b" }}
                      />
                    </div>
                  </div>

                  {/* Live Total Computation */}
                  <div className="conversion-preview-box" style={{ marginBottom: 16 }} data-testid="display-opening-total">
                    <span>Total Saldo Awal Terhitung:</span>
                    <strong>
                      {fmtQty(computedOpeningTotal)} {normalizedBaseUom}
                    </strong>
                    <span style={{ fontSize: 12, color: "#64748b", marginLeft: 8 }}>
                      (Total = {fmtQty(parseQtyInput(unprintedInput))} Belum Dicetak + {fmtQty(parseQtyInput(printedInput))} Sudah Dicetak)
                    </span>
                  </div>

                  {canEditOpeningBalance ? (
                    <button
                      type="submit"
                      className="primary"
                      disabled={savingOpening}
                      data-testid="btn-save-opening"
                    >
                      <RefreshCw size={14} className={savingOpening ? "spin" : ""} />
                      {savingOpening ? "Menyimpan…" : "Tetapkan Saldo Awal Stok Fisik"}
                    </button>
                  ) : (
                    <div style={{ fontSize: 12, color: "#64748b", fontStyle: "italic" }}>
                      Anda memerlukan izin <code>current_stock.update</code> atau <code>permissions.manage</code> untuk menetapkan saldo awal.
                    </div>
                  )}
                </form>
              </div>
            )}
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
                {[
                  { key: "ALL", label: "Semua" },
                  { key: "STOK_MASUK", label: "Stok Masuk" },
                  { key: "STATUS_CETAK", label: "Status Cetak" },
                  { key: "PENGIRIMAN", label: "Pengiriman" },
                  { key: "KOREKSI", label: "Koreksi" },
                ].map((tf) => (
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
                onChange={(e) => { setSearchQ(e.target.value); setPage(1); }}
                placeholder="Cari referensi / sumber / tujuan…"
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

          {/* Mutation Table */}
          <div className="mutation-table-container">
            <table className="mutation-table" data-testid="mutation-table">
              <thead>
                <tr>
                  <th style={{ width: 120 }}>Tanggal</th>
                  <th style={{ width: 140 }}>No. Sumber</th>
                  <th style={{ width: 130 }}>Tipe Transaksi</th>
                  <th>Keterangan</th>
                  <th>Sumber</th>
                  <th>Tujuan</th>
                  <th style={{ width: 110, textAlign: "right" }}>Masuk</th>
                  <th style={{ width: 110, textAlign: "right" }}>Keluar</th>
                  <th style={{ width: 110, textAlign: "right" }}>Saldo</th>
                </tr>
              </thead>
              <tbody data-testid="mutation-tbody">
                {mutationLoading && (
                  <tr>
                    <td colSpan={9} style={{ textAlign: "center", padding: 24, color: "#64748b" }} data-testid="mutation-loading">
                      Memuat mutasi persediaan…
                    </td>
                  </tr>
                )}
                {!mutationLoading && mutations.length === 0 && (
                  <tr>
                    <td colSpan={9} style={{ textAlign: "center", padding: 24, color: "#64748b" }} data-testid="mutation-empty">
                      Belum ada riwayat mutasi stok untuk filter ini.
                    </td>
                  </tr>
                )}
                {!mutationLoading && mutations.map((m) => {
                  const isSameUom = (m.transaction_uom || "").toUpperCase() === (m.base_uom || "").toUpperCase();
                  const isPenerimaan = m.movement_type === "PENERIMAAN" || m.movement_type === "STOK_MASUK";
                  const isPengiriman = m.movement_type === "PENGIRIMAN";
                  const isCetak = m.movement_type === "STATUS_CETAK" || m.movement_type === "PEMAKAIAN_CETAK";
                  const isKoreksi = m.movement_type === "KOREKSI";

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
                      aria-label={`Detail mutasi ${m.reference_number || m.id}`}
                    >
                      {/* Tanggal */}
                      <td className="nowrap" data-testid={`mutation-date-${m.id}`}>
                        {m.date ? formatDate(m.date) : "—"}
                      </td>

                      {/* No. Sumber (Inner Link) */}
                      <td className="mono" data-testid={`mutation-ref-${m.id}`}>
                        {m.reference_number ? (
                          <span
                            className="inner-entity-link"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (isPengiriman && m.reference_number.startsWith("SJ")) {
                                navigate(`/penjualan/pengiriman-pesanan?q=${encodeURIComponent(m.reference_number)}`);
                              } else if (isPenerimaan && m.reference_number.startsWith("PB")) {
                                navigate(`/pembelian/penerimaan-barang?q=${encodeURIComponent(m.reference_number)}`);
                              } else if (isCetak && m.order_id) {
                                navigate(`/tasks/operator`);
                              }
                            }}
                            data-testid={`inner-link-ref-${m.id}`}
                          >
                            {m.reference_number}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>

                      {/* Tipe Transaksi */}
                      <td>
                        <span className={`mutation-tag ${m.movement_type.toLowerCase()}`}>
                          {m.movement_type.replace("_", " ")}
                        </span>
                      </td>

                      {/* Keterangan */}
                      <td data-testid={`mutation-desc-${m.id}`}>{m.description || "—"}</td>

                      {/* Sumber */}
                      <td data-testid={`mutation-source-${m.id}`}>{m.source || "—"}</td>

                      {/* Tujuan */}
                      <td data-testid={`mutation-dest-${m.id}`}>{m.destination || "—"}</td>

                      {/* Masuk */}
                      <td style={{ textAlign: "right" }} data-testid={`mutation-in-${m.id}`}>
                        {m.delta > 0 ? (
                          <div className="qty-dual">
                            <span className="qty-in">+{fmtQty(m.transaction_qty)} {m.transaction_uom}</span>
                            {!isSameUom && (
                              <span className="base-qty">+{fmtQty(m.base_qty)} {m.base_uom}</span>
                            )}
                          </div>
                        ) : isCetak && m.delta_printed > 0 ? (
                          <div className="qty-dual">
                            <span className="qty-in">+{fmtQty(m.delta_printed)} Cetak</span>
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>

                      {/* Keluar */}
                      <td style={{ textAlign: "right" }} data-testid={`mutation-out-${m.id}`}>
                        {m.delta < 0 ? (
                          <div className="qty-dual">
                            <span className="qty-out">-{fmtQty(m.transaction_qty)} {m.transaction_uom}</span>
                            {!isSameUom && (
                              <span className="base-qty">-{fmtQty(m.base_qty)} {m.base_uom}</span>
                            )}
                          </div>
                        ) : isCetak && m.delta_unprinted < 0 ? (
                          <div className="qty-dual">
                            <span className="qty-out">-{fmtQty(Math.abs(m.delta_unprinted))} Polos</span>
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>

                      {/* Saldo */}
                      <td style={{ textAlign: "right" }} className="qty-saldo" data-testid={`mutation-bal-${m.id}`}>
                        {m.balance_after != null ? `${fmtQty(m.balance_after)} ${m.base_uom}` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pager */}
          {pageCount > 1 && (
            <div className="master-pager" style={{ marginTop: 14 }}>
              <button
                className="ghost sm"
                disabled={page <= 1 || mutationLoading}
                onClick={() => setPage((p) => p - 1)}
              >
                Sebelumnya
              </button>
              <span className="muted small">
                Halaman {page} dari {pageCount} · Total {totalMutations} transaksi
              </span>
              <button
                className="ghost sm"
                disabled={page >= pageCount || mutationLoading}
                onClick={() => setPage((p) => p + 1)}
              >
                Berikutnya
              </button>
            </div>
          )}
        </div>
      )}

      {/* DETAIL MUTASI DRAWER */}
      {selectedMutation && (
        <div
          className="mutation-drawer-overlay"
          onClick={() => setSelectedMutation(null)}
          data-testid="mutation-drawer-backdrop"
        >
          <div
            className="mutation-drawer"
            onClick={(e) => e.stopPropagation()}
            data-testid="mutation-detail-drawer"
          >
            <div className="drawer-header">
              <h3>Detail Mutasi Stok</h3>
              <button
                type="button"
                className="ghost sm"
                onClick={() => setSelectedMutation(null)}
                aria-label="Tutup Detail"
                data-testid="btn-close-drawer"
              >
                <X size={16} />
              </button>
            </div>

            <div className="drawer-body">
              <div className="drawer-prop-list">
                <div className="drawer-prop-row">
                  <span className="prop-label">Produk</span>
                  <span className="prop-value">{selectedMutation.item_name || item.item_name}</span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">Tanggal</span>
                  <span className="prop-value">{formatDate(selectedMutation.date)}</span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">Tipe Transaksi</span>
                  <span className="prop-value">
                    <span className={`mutation-tag ${selectedMutation.movement_type.toLowerCase()}`}>
                      {selectedMutation.movement_type.replace("_", " ")}
                    </span>
                  </span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">Qty Transaksi</span>
                  <span className="prop-value mono">
                    {fmtQty(selectedMutation.transaction_qty)} {selectedMutation.transaction_uom}
                  </span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">Konversi Transaksi</span>
                  <span className="prop-value">
                    1 {selectedMutation.transaction_uom} = {selectedMutation.conversion_factor}{" "}
                    {selectedMutation.base_uom}
                  </span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">Qty Dasar</span>
                  <span className="prop-value mono">
                    {fmtQty(selectedMutation.base_qty)} {selectedMutation.base_uom}
                  </span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">Perubahan Total</span>
                  <span
                    className={`prop-value mono ${selectedMutation.delta > 0 ? "qty-in" : selectedMutation.delta < 0 ? "qty-out" : ""}`}
                  >
                    {selectedMutation.delta > 0 ? "+" : ""}{fmtQty(selectedMutation.delta)}{" "}
                    {selectedMutation.base_uom}
                  </span>
                </div>

                {selectedMutation.delta_unprinted != null && (
                  <div className="drawer-prop-row">
                    <span className="prop-label">Δ Belum Dicetak</span>
                    <span className={`prop-value mono ${selectedMutation.delta_unprinted > 0 ? "qty-in" : "qty-out"}`}>
                      {selectedMutation.delta_unprinted > 0 ? "+" : ""}{fmtQty(selectedMutation.delta_unprinted)}{" "}
                      {selectedMutation.base_uom}
                    </span>
                  </div>
                )}

                {selectedMutation.delta_printed != null && (
                  <div className="drawer-prop-row">
                    <span className="prop-label">Δ Sudah Dicetak</span>
                    <span className={`prop-value mono ${selectedMutation.delta_printed > 0 ? "qty-in" : "qty-out"}`}>
                      {selectedMutation.delta_printed > 0 ? "+" : ""}{fmtQty(selectedMutation.delta_printed)}{" "}
                      {selectedMutation.base_uom}
                    </span>
                  </div>
                )}

                <div className="drawer-prop-row">
                  <span className="prop-label">Saldo Sebelum</span>
                  <span className="prop-value mono" data-testid="drawer-balance-before">
                    {selectedMutation.balance_before != null
                      ? `${fmtQty(selectedMutation.balance_before)} ${selectedMutation.base_uom}`
                      : "— (Belum ditetapkan)"}
                  </span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">Saldo Sesudah</span>
                  <span className="prop-value mono" data-testid="drawer-balance-after">
                    {selectedMutation.balance_after != null
                      ? `${fmtQty(selectedMutation.balance_after)} ${selectedMutation.base_uom}`
                      : "— (Belum ditetapkan)"}
                  </span>
                </div>

                {selectedMutation.unprinted_before != null && (
                  <div className="drawer-prop-row" data-testid="drawer-unprinted-transition">
                    <span className="prop-label">Belum Dicetak</span>
                    <span className="prop-value mono">
                      {fmtQty(selectedMutation.unprinted_before)} → {fmtQty(selectedMutation.unprinted_after)} {selectedMutation.base_uom}
                    </span>
                  </div>
                )}

                {selectedMutation.printed_before != null && (
                  <div className="drawer-prop-row" data-testid="drawer-printed-transition">
                    <span className="prop-label">Sudah Dicetak</span>
                    <span className="prop-value mono">
                      {fmtQty(selectedMutation.printed_before)} → {fmtQty(selectedMutation.printed_after)} {selectedMutation.base_uom}
                    </span>
                  </div>
                )}

                <div className="drawer-prop-row">
                  <span className="prop-label">Sumber</span>
                  <span className="prop-value">{selectedMutation.source || "—"}</span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">Tujuan</span>
                  <span className="prop-value">{selectedMutation.destination || "—"}</span>
                </div>

                <div className="drawer-prop-row">
                  <span className="prop-label">No. Referensi</span>
                  <span className="prop-value mono">{selectedMutation.reference_number || "—"}</span>
                </div>

                {selectedMutation.description && (
                  <div className="drawer-prop-row">
                    <span className="prop-label">Keterangan</span>
                    <span className="prop-value">{selectedMutation.description}</span>
                  </div>
                )}

                <div className="drawer-prop-row">
                  <span className="prop-label">Dilakukan Oleh</span>
                  <span className="prop-value">
                    {selectedMutation.actor?.name || selectedMutation.actor?.email || "Sistem"}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {showCorrectModal && (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          data-testid="correct-opening-modal"
          onClick={savingCorrection ? undefined : () => setShowCorrectModal(false)}
        >
          <div className="modal-inner confirm-action-inner" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>Koreksi Saldo Awal</h3>
            </div>
            <div className="confirm-action-body">
              <p style={{ fontSize: 13, color: "#64748b", marginBottom: 12 }}>
                Koreksi menetapkan <strong>target fisik sekarang</strong>. Mutasi <code>SALDO_AWAL</code> lama tetap di ledger; sistem menambahkan <code>KOREKSI</code>.
              </p>
              <dl className="confirm-action-dl" data-testid="correct-opening-current">
                <div>
                  <dt>Saldo sekarang (Total)</dt>
                  <dd>{fmtQty(stockState?.total_qty)} {stockState?.base_uom || normalizedBaseUom}</dd>
                </div>
                <div>
                  <dt>Belum Dicetak sekarang</dt>
                  <dd>{fmtQty(stockState?.unprinted_qty)} {stockState?.base_uom || normalizedBaseUom}</dd>
                </div>
                <div>
                  <dt>Sudah Dicetak sekarang</dt>
                  <dd>{fmtQty(stockState?.printed_qty)} {stockState?.base_uom || normalizedBaseUom}</dd>
                </div>
              </dl>
              <div className="form-field" style={{ marginTop: 12 }}>
                <label htmlFor="corr-unprinted">Belum Dicetak baru ({normalizedBaseUom})</label>
                <input
                  id="corr-unprinted"
                  type="text"
                  value={corrUnprinted}
                  onChange={(e) => setCorrUnprinted(e.target.value)}
                  disabled={savingCorrection}
                  data-testid="input-correct-unprinted"
                />
              </div>
              <div className="form-field" style={{ marginTop: 8 }}>
                <label htmlFor="corr-printed">Sudah Dicetak baru ({normalizedBaseUom})</label>
                <input
                  id="corr-printed"
                  type="text"
                  value={corrPrinted}
                  onChange={(e) => setCorrPrinted(e.target.value)}
                  disabled={savingCorrection}
                  data-testid="input-correct-printed"
                />
              </div>
              <div className="conversion-preview-box" style={{ marginTop: 12 }} data-testid="display-correct-total">
                <span>Total otomatis:</span>
                <strong>
                  {fmtQty(parseQtyInput(corrUnprinted) + parseQtyInput(corrPrinted))} {normalizedBaseUom}
                </strong>
              </div>
              <div className="form-field" style={{ marginTop: 12 }}>
                <label htmlFor="corr-reason">Alasan koreksi <span style={{ color: "#dc2626" }}>*</span></label>
                <input
                  id="corr-reason"
                  type="text"
                  value={corrReason}
                  onChange={(e) => setCorrReason(e.target.value)}
                  disabled={savingCorrection}
                  placeholder="Wajib diisi"
                  data-testid="input-correct-reason"
                />
              </div>
              {correctionError && <p className="error" data-testid="correct-opening-error">{correctionError}</p>}
            </div>
            <div className="delete-modal-actions">
              <button
                type="button"
                className="ghost"
                disabled={savingCorrection}
                onClick={() => setShowCorrectModal(false)}
                data-testid="correct-opening-cancel"
              >
                Batal
              </button>
              <button
                type="button"
                className="primary"
                disabled={savingCorrection}
                onClick={handleConfirmCorrection}
                data-testid="correct-opening-confirm"
              >
                {savingCorrection ? "Menyimpan…" : "Konfirmasi Koreksi"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
