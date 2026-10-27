import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Check, ChevronDown, Download, Info, Search, SlidersHorizontal, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate, formatTrackingPageDateTime } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import {
  bookPhysicalDisplay,
  stockPhysicalPrimaryDisplay,
  isOperationalWarehouse,
  openingFieldErrors,
  openingReady,
  operationalOpeningWarehouses,
  preselectedWarehouseId,
  stockEmptyMessage,
  warehouseChoices,
  warehouseLocationLabel,
} from "../lib/physicalStockView";
import {
  SSI_REASON_LABEL,
  resolveDapatDijualDisplay,
  resolvePolosDisplay,
} from "../lib/ssiStatusDisplay";
import { storageUserKey } from "../lib/poListReturn";
import {
  buildStockSaatIniPageCacheKey,
  getStockSaatIniPageCache,
  invalidateStockSaatIniPageCache,
  setStockSaatIniPageCache,
} from "../lib/stockSaatIniPageCache";
import BranchScopeSelect from "../components/BranchScopeSelect";
import StockSaatIniDetailDrawer from "./StockSaatIniDetailDrawer";
import {
  IMPORT_RESUME_SAFE_MESSAGE,
  buildConfirmLines,
  classifyImportConfirmResponse,
  defaultSelectedKeys,
  isReadyRow,
  rowSelectKey,
  summarizeImportRows,
} from "../lib/openingImportUi";

/** Table body view that can keep rows visible while a soft refresh runs. */
export function stockCatalogTableView({
  pending,
  loading,
  refreshing,
  itemCount,
}) {
  const count = Number(itemCount) || 0;
  if (count > 0) return "results";
  if (pending || loading || refreshing) return "loading";
  return "empty";
}

const INLINE_ADJUST_REASON = "Koreksi manual dari Stock Saat Ini";

/** Owner Excel SSI columns (PCS). Source: stock_saat_ini_excel read model. */
const EXCEL_SSI_COLUMNS = [
  {
    key: "saldo_awal",
    label: "Saldo Awal",
    testId: "stock-saldo-awal",
    note: "OPENING_BALANCE (PCS) — editable via Saldo Awal / adjustment beraudit",
  },
  {
    key: "pembelian",
    label: "Pembelian",
    testId: "stock-pembelian",
    note: "Penerimaan pembelian / PURCHASE_RECEIPT (PCS)",
  },
  {
    key: "stock",
    label: "Stock",
    testId: "stock-excel-stock",
    note: "Saldo buku PCS (penerimaan, adjustment, pengiriman, reversal sah)",
    usePhysicalDisplay: true,
  },
  {
    key: "stock_dapat_dijual",
    label: "Stock Dapat Dijual",
    testId: "stock-siap-jual",
    note: "Stock − (PO aktif − qty sudah dikirim); boleh negatif; Indikatif bila stok belum lengkap",
    statusKey: "stock_dapat_dijual_status",
    legacyStatusKey: "qty_siap_jual_status",
    reasonKey: "stock_dapat_dijual_reason",
    legacyReasonKey: "qty_siap_jual_reason",
    noteKey: "qty_siap_jual_note",
  },
  {
    key: "qty_tercetak",
    label: "Qty Tercetak",
    testId: "stock-cetak",
    note: "Konfirmasi selesai operator pada parent SO (bukan jumlah warna)",
    statusKey: "qty_tercetak_status",
    legacyStatusKey: "qty_sudah_cetak_gudang_status",
  },
  {
    key: "qty_polos",
    label: "Qty Polos",
    testId: "stock-polos",
    note: "Stock − Barang Tercetak Belum Kirim (PCS); Belum terverifikasi bila bukti cetak/kirim/WIP/rusak tidak cukup",
    statusKey: "qty_polos_status",
    legacyStatusKey: "qty_polos_gudang_status",
    reasonKey: "qty_polos_reason",
    legacyReasonKey: "qty_polos_gudang_reason",
    noteKey: "qty_polos_gudang_note",
  },
  {
    key: "tercetak_belum_kirim",
    label: "Barang Tercetak Belum Kirim",
    testId: "stock-tercetak-belum",
    note: "Qty Tercetak − Barang Cetak Sudah Dikirim (PCS)",
    statusKey: "tercetak_belum_kirim_status",
    reasonKey: "tercetak_belum_kirim_reason",
  },
  {
    key: "cetak_sudah_dikirim",
    label: "Barang Cetak Sudah Dikirim",
    testId: "stock-cetak-dikirim",
    note: "Konfirmasi SJ barang cetak terbukti (PCS) — tanpa estimasi",
    statusKey: "cetak_sudah_dikirim_status",
    reasonKey: "cetak_sudah_dikirim_reason",
  },
];

/** @deprecated alias — FE tests / expand panel still reference condition helpers */
const STOCK_CONDITION_FIELDS = {
  polos: {
    label: "Qty Polos",
    qtyKey: "qty_polos",
    statusKey: "qty_polos_gudang_status",
    noteKey: "qty_polos_gudang_note",
    fallbackNote: "Qty Polos = Stock − Tercetak Belum Kirim (PCS)",
    testId: "stock-polos",
  },
  cetak: {
    label: "Qty Tercetak",
    qtyKey: "qty_tercetak",
    statusKey: "qty_sudah_cetak_gudang_status",
    noteKey: "qty_sudah_cetak_gudang_note",
    fallbackNote: "Qty Tercetak dari konfirmasi operator (parent SO)",
    testId: "stock-cetak",
  },
  siapJual: {
    label: "Stock Dapat Dijual",
    qtyKey: "stock_dapat_dijual",
    statusKey: "qty_siap_jual_status",
    noteKey: "qty_siap_jual_note",
    fallbackNote: "Stock − (PO aktif − qty sudah dikirim); boleh negatif",
    testId: "stock-siap-jual",
  },
};

function excelCellTitle(col, row, status) {
  const reasonCode = String(
    (col.reasonKey && row?.[col.reasonKey])
    || (col.legacyReasonKey && row?.[col.legacyReasonKey])
    || "",
  ).trim();
  const note = String(
    (col.noteKey && row?.[col.noteKey])
    || "",
  ).trim();
  const reasonLabel = SSI_REASON_LABEL[reasonCode] || reasonCode;
  if (status === "BELUM_TERSEDIA" || status === "UNVERIFIED") {
    return [note || "Belum terverifikasi", reasonLabel, col.note].filter(Boolean).join(" — ");
  }
  if (status === "INDICATIVE") {
    return [note || "Indikatif", reasonLabel, col.note].filter(Boolean).join(" — ");
  }
  return col.note;
}

function excelQtyCell(col, row, key, physicalPrimary) {
  const masterItemId = row?.master_item_id;
  // P0-B1 status contract for Dapat Dijual / Polos
  if (col.key === "qty_polos") {
    const disp = resolvePolosDisplay(row, physicalPrimary);
    if (disp.mode === "unverified") {
      return (
        <td
          key={key || col.key}
          className="col-fit num"
          data-testid={`${col.testId}-${masterItemId}`}
          data-condition-status="UNVERIFIED"
          data-unverified-reason={disp.reason || undefined}
          title={disp.title}
        >
          <span className="stock-status-badge stock-status-badge--unverified" title={disp.title}>
            Belum terverifikasi
          </span>
        </td>
      );
    }
    const neg = Number(disp.qty) < 0;
    return (
      <td
        key={key || col.key}
        className={`col-fit num mono${neg ? " stock-book-negative" : ""}`}
        data-testid={`${col.testId}-${masterItemId}`}
        data-condition-status="INDICATIVE"
        data-status-reason={disp.reason || undefined}
        title={disp.title}
      >
        <span className="stock-indicative-cell">
          <span className="mono">{fmtQty(disp.qty)}</span>
          <span className="stock-status-badge stock-status-badge--indicative">Indikatif</span>
        </span>
      </td>
    );
  }
  if (col.key === "stock_dapat_dijual") {
    const disp = resolveDapatDijualDisplay(row, physicalPrimary);
    if (disp.mode === "unverified") {
      return (
        <td
          key={key || col.key}
          className="col-fit num"
          data-testid={`${col.testId}-${masterItemId}`}
          data-condition-status="UNVERIFIED"
          title={disp.title}
        >
          <span className="stock-status-badge stock-status-badge--unverified">Belum terverifikasi</span>
        </td>
      );
    }
    const neg = Number(disp.qty) < 0;
    return (
      <td
        key={key || col.key}
        className={`col-fit num mono${neg ? " stock-book-negative" : ""}`}
        data-testid={`${col.testId}-${masterItemId}`}
        data-condition-status={disp.status}
        data-negative={neg ? "true" : "false"}
        data-status-reason={disp.reason || undefined}
        title={disp.title}
      >
        {disp.mode === "indicative" ? (
          <span className="stock-indicative-cell">
            <span className="mono">{disp.qty == null ? "—" : fmtQty(disp.qty)}</span>
            <span className="stock-status-badge stock-status-badge--indicative">Indikatif</span>
          </span>
        ) : (
          fmtQty(disp.qty)
        )}
      </td>
    );
  }

  const qty = row?.[col.key];
  const status = String(
    (col.statusKey && row?.[col.statusKey])
    || (col.legacyStatusKey && row?.[col.legacyStatusKey])
    || "",
  ).toUpperCase();
  const title = excelCellTitle(col, row, status);
  if (qty == null || status === "BELUM_TERSEDIA" || status === "UNVERIFIED") {
    return (
      <td
        key={key || col.key}
        className="col-fit num"
        data-testid={`${col.testId}-${masterItemId}`}
        data-condition-status="BELUM_TERSEDIA"
        data-unverified-reason={String(
          (col.reasonKey && row?.[col.reasonKey])
          || (col.legacyReasonKey && row?.[col.legacyReasonKey])
          || "",
        ) || undefined}
        title={title}
      >
        <span className="stock-status-badge stock-status-badge--unverified" title={title}>
          Belum terverifikasi
        </span>
      </td>
    );
  }
  const neg = Number(qty) < 0;
  return (
    <td
      key={key || col.key}
      className={`col-fit num mono${neg ? " stock-book-negative" : ""}`}
      data-testid={`${col.testId}-${masterItemId}`}
      data-condition-status={status || "READY"}
      title={title}
    >
      {fmtQty(qty)}
    </td>
  );
}

function conditionFieldNote(kind, row) {
  const meta = STOCK_CONDITION_FIELDS[kind];
  return row?.[meta.noteKey] || meta.fallbackNote;
}

function conditionFieldDisplay(kind, row) {
  const meta = STOCK_CONDITION_FIELDS[kind];
  const value = {
    polos: row?.qty_polos ?? row?.qty_polos_gudang,
    cetak: row?.qty_tercetak ?? row?.qty_sudah_cetak_gudang,
    siapJual: row?.stock_dapat_dijual ?? row?.qty_siap_jual,
  }[kind];
  const status = row?.[meta.statusKey] || (value == null ? "BELUM_TERSEDIA" : "");
  const note = conditionFieldNote(kind, row);
  if (value != null && status !== "BELUM_TERSEDIA") {
    return { mode: "qty", qty: value, status: status || "READY", note };
  }
  return { mode: "unavailable", status: status || "BELUM_TERSEDIA", note };
}

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `saldo-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function StockSaatIni() {
  const { user, hasPerm } = useAuth();
  const userKey = storageUserKey(user);
  const canOpening = hasPerm("current_stock.update") || hasPerm("permissions.manage");
  const canExport = hasPerm("current_stock.export");
  const [data, setData] = useState(null);
  const canAdjust = Boolean(data?.can_adjust_physical_stock);
  const [branches, setBranches] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [branchId, setBranchId] = useState("");
  const [warehouseId, setWarehouseId] = useState(""); // "" = Semua Gudang
  const [filtersReady, setFiltersReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [openingOpen, setOpeningOpen] = useState(false);
  const [openingSeed, setOpeningSeed] = useState(null);
  const [adjustItem, setAdjustItem] = useState(null);
  const [inlineItemId, setInlineItemId] = useState(null);
  const [savingAdjust, setSavingAdjust] = useState(false);
  const [legendOpen, setLegendOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [statusSummary, setStatusSummary] = useState(null);
  const [statusSummaryErr, setStatusSummaryErr] = useState("");
  const [statusSummaryOpen, setStatusSummaryOpen] = useState(false);
  const [statusDetailOpen, setStatusDetailOpen] = useState(false);
  const [detailItem, setDetailItem] = useState(null);
  const saveLock = useRef(false);
  const loadSeq = useRef(0);
  const summarySeq = useRef(0);
  /** Skip one catalog reload when syncing branchId from catalog effective_branch_id. */
  const skipBranchPinReload = useRef(false);
  const itemsRef = useRef([]);

  useEffect(() => { setPage(1); }, [q, branchId, warehouseId]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.branches(), api.warehouses()])
      .then(([branchRes, warehouseRes]) => {
        if (cancelled) return;
        const branchRows = branchRes.items || [];
        setBranches(branchRows);
        setWarehouses(warehouseRes.items || []);
        // Pin sole active branch before the first catalog GET to avoid a bounce refetch.
        setBranchId((current) => {
          if (current) return current;
          const active = branchRows.filter((row) => row?.active !== false && row?.id);
          return active.length === 1 ? active[0].id : current;
        });
      })
      .catch(() => {
        if (!cancelled) setErr("Gagal memuat cabang dan gudang");
      })
      .finally(() => {
        if (!cancelled) setFiltersReady(true);
      });
    return () => { cancelled = true; };
  }, []);

  const cacheKey = useMemo(() => buildStockSaatIniPageCacheKey({
    userId: userKey,
    branchId,
    warehouseId,
    q,
    page,
    pageSize,
  }), [userKey, branchId, warehouseId, q, page, pageSize]);

  const identityOk = Boolean(userKey && userKey !== "anon");

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setErr("");
    const hasVisibleRows = itemsRef.current.length > 0;
    // Cache only when user identity is proven — never reuse anon/cross-user pages.
    const cached = identityOk && !hasVisibleRows ? getStockSaatIniPageCache(cacheKey) : null;
    if (cached?.data) {
      setData(cached.data);
      itemsRef.current = cached.data.items || [];
      setRefreshing(true);
      setLoading(false);
    } else if (hasVisibleRows) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      const params = { q, page, limit: pageSize };
      if (branchId) params.branch_id = branchId;
      if (warehouseId) params.warehouse_id = warehouseId;
      const d = await api.physicalStockCatalog(params);
      if (seq !== loadSeq.current) return;
      setData(d);
      itemsRef.current = Array.isArray(d?.items) ? d.items : [];
      if (identityOk) setStockSaatIniPageCache(cacheKey, d);
      if (!branchId && d?.effective_branch_id) {
        // Sync controlled select without a second identical catalog GET.
        skipBranchPinReload.current = true;
        setBranchId(d.effective_branch_id);
      }
    } catch (ex) {
      if (seq !== loadSeq.current) return;
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Stock Saat Ini"));
      // Keep prior in-scope rows; only blank when nothing was shown.
      if (!itemsRef.current.length) {
        setData(null);
      }
    } finally {
      if (seq === loadSeq.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [q, page, pageSize, branchId, warehouseId, cacheKey, identityOk]);

  const reloadAfterMutation = useCallback(async () => {
    invalidateStockSaatIniPageCache();
    await load();
  }, [load]);

  useEffect(() => {
    if (!filtersReady) return;
    if (skipBranchPinReload.current) {
      skipBranchPinReload.current = false;
      return;
    }
    load();
  }, [load, filtersReady]);

  useEffect(() => {
    itemsRef.current = Array.isArray(data?.items) ? data.items : [];
  }, [data]);

  useEffect(() => {
    const seq = ++summarySeq.current;
    setStatusSummaryErr("");
    const params = {};
    if (branchId) params.branch_id = branchId;
    if (warehouseId) params.warehouse_id = warehouseId;
    const request = typeof api.physicalStockStatusSummary === "function"
      ? api.physicalStockStatusSummary(params)
      : null;
    Promise.resolve(request)
      .then((res) => {
        if (seq !== summarySeq.current) return;
        if (!res) {
          setStatusSummary(null);
          return;
        }
        setStatusSummary(res);
      })
      .catch((ex) => {
        if (seq !== summarySeq.current) return;
        setStatusSummary(null);
        const det = ex?.response?.data?.detail;
        setStatusSummaryErr(typeof det === "object" ? det.message : (det || "Ringkasan status gagal dimuat"));
      });
  }, [branchId, warehouseId, data?.total, data?.stock_read_mode]);

  const clearSearch = () => {
    clearQuery();
    setPage(1);
  };

  const items = data?.items || [];
  const effectiveBranchId = branchId || data?.effective_branch_id || "";
  const canSwitchBranches = Boolean(data?.can_switch_branches);
  const filterWarehouses = effectiveBranchId
    ? warehouseChoices(warehouses, effectiveBranchId).filter(
      (row) => row.active !== false && isOperationalWarehouse(row),
    )
    : [];
  const selectedFilterWarehouse = filterWarehouses.find((row) => row.id === warehouseId) || null;
  // Adjust targets the selected warehouse when filtered; otherwise all active
  // warehouses on the selected branch (ID-scoped — no YGY name/code hardcode).
  const branchAdjustWarehouses = effectiveBranchId
    ? warehouseChoices(warehouses, effectiveBranchId).filter((row) => row.active !== false)
    : [];
  const adjustWarehouses = selectedFilterWarehouse
    ? [selectedFilterWarehouse]
    : branchAdjustWarehouses;
  const singleWarehouse = adjustWarehouses.length === 1 ? adjustWarehouses[0] : null;
  const opnameHref = warehouseId
    ? `/persediaan/stock-opname?warehouse_id=${encodeURIComponent(warehouseId)}`
    : "/persediaan/stock-opname";

  const beginAdjust = (item, { balancePresent = true } = {}) => {
    if (savingAdjust || saveLock.current) return;
    if (adjustWarehouses.length === 0) {
      setInlineItemId(null);
      setAdjustItem(null);
      setErr("Koreksi tidak tersedia: belum ada gudang aktif.");
      return;
    }
    // No warehouse balance yet → Saldo Awal only. Do not let Adjustment invent a
    // first baseline when the operator lacks opening permission.
    if (!balancePresent) {
      setInlineItemId(null);
      setAdjustItem(null);
      if (!canOpening) {
        setErr(
          "Belum ada saldo fisik. Saldo Awal memerlukan izin current_stock.update "
          + "(atau permissions.manage). Koreksi tidak dapat menggantikan Saldo Awal.",
        );
        return;
      }
      const wh = singleWarehouse
        || (warehouseId ? adjustWarehouses.find((row) => row.id === warehouseId) : null)
        || (adjustWarehouses.length === 1 ? adjustWarehouses[0] : null);
      setErr("");
      setOpeningSeed({
        itemId: item.master_item_id,
        branchId: wh?.branch_id || effectiveBranchId || branchId || "",
        warehouseId: wh?.id || warehouseId || "",
      });
      setOpeningOpen(true);
      return;
    }
    if (adjustWarehouses.length > 1) {
      setInlineItemId(null);
      setAdjustItem(item);
      return;
    }
    setAdjustItem(null);
    setErr("");
    setInlineItemId(item.master_item_id);
  };

  const saveInline = async (item, target, setCellError, close) => {
    if (saveLock.current || !singleWarehouse) return;
    const current = Number(item.physical_qty);
    if (target === current) {
      close();
      return;
    }
    saveLock.current = true;
    setSavingAdjust(true);
    setCellError("");
    try {
      await api.createPhysicalAdjustment(item.master_item_id, {
        branch_id: singleWarehouse.branch_id,
        warehouse_id: singleWarehouse.id,
        target_physical_qty: target,
        reason: INLINE_ADJUST_REASON,
        idempotency_key: newKey(),
      });
      close();
      await reloadAfterMutation();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setCellError(typeof det === "object" ? det.message : (det || "Gagal menyimpan koreksi"));
    } finally {
      saveLock.current = false;
      setSavingAdjust(false);
    }
  };
  const pageCount = data?.page_count || 1;
  const tableBusy = searchPending || loading || refreshing;
  const tableView = stockCatalogTableView({
    pending: searchPending,
    loading,
    refreshing,
    itemCount: items.length,
  });
  const modePending = data == null;
  // Do not default to compatibility before the catalog response confirms mode.
  const readMode = modePending
    ? null
    : (data?.stock_read_mode || (data?.r2_authority_active ? "r2" : "compatibility"));
  const r2Active = Boolean(data?.r2_authority_active);
  // Warehouse-scoped UI when R2 OR explicit warehouse_stock_standalone is on.
  const warehouseActive = Boolean(
    !modePending && (
      r2Active
      || data?.warehouse_stock_standalone
      || readMode === "warehouse_standalone"
      || readMode === "r2"
    )
  );
  const emptyText = stockEmptyMessage({
    masterItemCount: data?.master_item_count || 0,
    warehouseCount: data?.warehouse_count || 0,
    readMode: readMode || "compatibility",
  });
  const modeChipLabel = r2Active
    ? "R2"
    : warehouseActive
      ? "Gudang"
      : "Kompatibilitas";
  const modeFullLabel = modePending
    ? "Memuat mode stok…"
    : r2Active
      ? "Mode R2 — stok fisik per cabang/gudang."
      : warehouseActive
        ? (data?.stock_read_mode_label
          || "Mode warehouse standalone — stok fisik per cabang/gudang (tanpa Replica Set).")
        : (data?.stock_read_mode_label
          || "Mode kompatibilitas — stok fisik legacy perusahaan (bukan per gudang).");
  const subtitleText = modePending
    ? "Memuat persediaan…"
    : warehouseActive
      ? "Persediaan barang per cabang dan gudang."
      : "Persediaan barang (mode kompatibilitas perusahaan).";
  const showBranchAggregate = Boolean(warehouseActive && !selectedFilterWarehouse);
  const actionsLocked = tableBusy;

  const physicalHeader = "Stock";
  // Owner Excel: Produk + 8 qty columns + unit/lokasi/update (+ expand).
  // Compact Owner list: Produk | Stock | Stock Dapat Dijual | Qty Polos | Lihat Detail
  const tableColCount = 5;

  const download = () => {
    const header = [
      "Kode",
      "Produk",
      "Satuan Dasar",
      "Lokasi",
      ...EXCEL_SSI_COLUMNS.map((c) => c.label),
      "Baseline",
      "Terakhir Update",
    ];
    const lines = [header.join(",")];
    const excelCsvValue = (row, key) => {
      const v = row?.[key];
      return v == null ? "" : v;
    };
    items.forEach((row) => {
      const location = selectedFilterWarehouse
        ? warehouseLocationLabel(selectedFilterWarehouse, branches)
        : (data?.effective_branch_code || "Cabang");
      const book = bookPhysicalDisplay(row, { warehouseId: selectedFilterWarehouse?.id || "" });
      const excelVals = EXCEL_SSI_COLUMNS.map((c) => (
        c.usePhysicalDisplay
          ? (book.present ? book.qty : excelCsvValue(row, c.key))
          : excelCsvValue(row, c.key)
      ));
      const cells = [
        row.item_code,
        row.item_name,
        row.base_unit,
        location,
        ...excelVals,
        row.baseline_status || "",
        [
          row.updated_at || "",
          row.updated_at
            ? `oleh ${row.updated_by_name || "Tidak tercatat"}`
            : "",
        ].filter(Boolean).join(" "),
      ].map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`);
      lines.push(cells.join(","));
      if (!selectedFilterWarehouse && Array.isArray(row.warehouse_balances)) {
        row.warehouse_balances.forEach((wb) => {
          const whLabel = warehouseLocationLabel({
            id: wb.warehouse_id,
            code: wb.warehouse_code,
            name: wb.warehouse_name,
            branch_id: effectiveBranchId,
          }, branches);
          const qty = wb.balance_present ? wb.physical_qty : "";
          const detail = [
            row.item_code,
            row.item_name,
            row.base_unit,
            whLabel,
            "", // saldo awal — branch demand scope
            "", // pembelian
            qty, // stock per gudang
            "",
            "",
            "",
            "",
            "",
            wb.baseline_status || (wb.balance_present ? "" : "unreconciled"),
            [
              wb.updated_at || "",
              wb.updated_at
                ? `oleh ${wb.updated_by_name || "Tidak tercatat"}`
                : "",
            ].filter(Boolean).join(" "),
          ].map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`);
          lines.push(detail.join(","));
        });
      }
    });
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `stock-saat-ini-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const branchLabel = (() => {
    const row = branches.find((b) => b.id === effectiveBranchId);
    if (row?.code && row?.name && row.code !== row.name) return `${row.name} (${row.code})`;
    return row?.code || data?.effective_branch_code || row?.name || "Cabang";
  })();

  const renderPhysicalCell = (it, primary, physicalText, book) => {
    const hints = primary.hints || [];
    const flag = primary.flag;
    const statusKey = primary.text === "Belum lengkap"
      ? "incomplete"
      : primary.text === "Belum saldo"
        ? "missing"
        : (flag === "Defisit" ? "deficit" : "qty");
    const chip = !primary.present ? (
      <span className={`stock-status-badge stock-status-badge--${statusKey}`}>{physicalText}</span>
    ) : (
      <span className={`stock-qty-chip${primary.negative ? " is-deficit" : ""}`}>{physicalText}</span>
    );
    const flagEl = flag ? (
      <span
        className="stock-status-badge stock-status-badge--deficit"
        data-testid={`stock-flag-${it.master_item_id}`}
        title={hints.join(" · ")}
      >
        {flag}
      </span>
    ) : null;
    // Inline koreksi hanya untuk saldo yang sudah ada. Tanpa balance → Saldo Awal.
    if (canAdjust && !actionsLocked && inlineItemId === it.master_item_id && singleWarehouse && book.present) {
      return (
        <InlinePhysicalEditor
          item={it}
          saving={savingAdjust}
          onCancel={() => setInlineItemId(null)}
          onSave={(target, setCellError, close) => saveInline(it, target, setCellError, close)}
        />
      );
    }
    if (canAdjust && !actionsLocked) {
      return (
        <span className="stock-physical-cell-inner">
          <button
            type="button"
            className="stock-physical-btn"
            data-testid={`stock-adjust-${it.master_item_id}`}
            onClick={() => beginAdjust(it, { balancePresent: Boolean(book.present) })}
          >
            {chip}
          </button>
          {flagEl}
        </span>
      );
    }
    return (
      <span className="stock-physical-cell-inner">
        {chip}
        {flagEl}
      </span>
    );
  };

  const whStatusLabel = (wb) => {
    if (!wb.balance_present) return { text: "Belum saldo", tone: "missing" };
    if (wb.saldo_buku_negatif) return { text: "Defisit", tone: "deficit" };
    if (wb.baseline_status === "unreconciled") return { text: "Belum valid", tone: "incomplete" };
    return { text: "Valid", tone: "valid" };
  };

  const clearCatalogScope = () => {
    // Drop rows immediately so another branch/warehouse is never shown as current.
    // Keep mode/permission envelope so Cabang/Gudang chrome does not collapse mid-switch.
    setData((prev) => (
      prev
        ? { ...prev, items: [], total: 0, page: 1, page_count: 1 }
        : prev
    ));
    itemsRef.current = [];
    setRefreshing(false);
    setLoading(true);
    setErr("");
    setInlineItemId(null);
    setAdjustItem(null);
    setDetailItem(null);
    setStatusSummary(null);
  };

  const onBranchChange = (next) => {
    clearCatalogScope();
    setBranchId(next);
    setWarehouseId("");
    setPage(1);
  };

  const onWarehouseChange = (next) => {
    clearCatalogScope();
    setWarehouseId(next);
    setPage(1);
  };

  const formatBelumTerkirim = (it) => (
    (it.belum_terkirim_status === "BELUM_TERVERIFIKASI"
      || it.belum_terkirim_status === "CUTOVER_PENDING")
      ? "Belum terverifikasi"
      : fmtQty(it.qty_belum_terkirim)
  );

  const formatTersedia = (it) => (
    it.qty_tersedia_valid === false ? "Belum valid" : fmtQty(it.qty_tersedia)
  );

  return (
    <div className="prod-page master-items-page stock-page stock-page--mockup" data-testid="stock-saat-ini-page">
      <div className="page-heading prod-heading stock-page-heading">
        <div className="prod-heading-copy">
          <div className="stock-title-row">
            <h1 data-testid="stock-title">Stock Saat Ini</h1>
            {modePending ? (
              <span
                className="stock-mode-chip stock-mode-chip--pending"
                data-testid="stock-mode-chip-pending"
                title={modeFullLabel}
              >
                …
              </span>
            ) : (
              <span
                className={`stock-mode-chip stock-mode-chip--${r2Active ? "r2" : warehouseActive ? "warehouse" : "compat"}`}
                data-testid="stock-mode-chip"
                title={modeFullLabel}
              >
                {modeChipLabel}
              </span>
            )}
          </div>
          <p className="subhead" data-testid="stock-subtitle">{subtitleText}</p>
        </div>
        <div className="stock-heading-actions">
          <button
            type="button"
            className="stock-btn-outline"
            onClick={() => { setOpeningSeed(null); setOpeningOpen(true); }}
            disabled={!canAdjust}
            title={!canAdjust ? (data?.adjust_blocked_reason || "Saldo Awal belum tersedia pada mode ini") : undefined}
            data-testid="saldo-awal-button"
          >
            Saldo Awal
          </button>
          {warehouseActive ? (
            <Link className="stock-btn-outline" to={opnameHref} data-testid="stock-opname-button">
              Stock Opname
            </Link>
          ) : (
            <button
              type="button"
              className="stock-btn-outline"
              disabled
              title={data?.adjust_blocked_reason || "Stock Opname memerlukan mesin gudang (R2 atau warehouse standalone)."}
              data-testid="stock-opname-button"
            >
              Stock Opname
            </button>
          )}
          {canExport && (
            <button className="stock-btn-primary" onClick={download} data-testid="stock-export-button">
              <Download size={14} /> Export
            </button>
          )}
        </div>
      </div>

      {err && (
        <div className="signal-strip" data-testid="stock-error" role="alert">
          <div><AlertCircle size={16} /><span>{err}</span></div>
        </div>
      )}

      <div className="stock-filter-bar" data-testid="stock-filters">
            <div className="stock-filter-field stock-filter-field--branch" data-testid="stock-branch-filter-slot">
              {canSwitchBranches && warehouseActive ? (
                <BranchScopeSelect
                  branches={branches}
                  value={effectiveBranchId}
                  canSwitch
                  effectiveCode={data?.effective_branch_code || branchLabel}
                  onChange={onBranchChange}
                  testId="stock-branch-select"
                />
              ) : (
                <label className="stock-filter-ro" data-testid="stock-branch-select-fixed">
                  <span>Cabang</span>
                  <div className="stock-filter-ro-value">{branchLabel}</div>
                </label>
              )}
            </div>
            {warehouseActive ? (
              <label
                className="stock-filter-field stock-filter-field--warehouse"
                data-testid="stock-warehouse-filter"
              >
                <span>Gudang</span>
                <select
                  value={warehouseId}
                  onChange={(e) => onWarehouseChange(e.target.value)}
                  data-testid="stock-warehouse-select"
                  aria-label="Filter gudang"
                  disabled={actionsLocked}
                >
                  <option value="">Semua Gudang</option>
                  {filterWarehouses.map((wh) => (
                    <option key={wh.id} value={wh.id}>
                      {wh.code ? `${wh.code} — ${wh.name || wh.code}` : (wh.name || wh.id)}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <label
                className="stock-filter-field stock-filter-field--warehouse"
                data-testid="stock-warehouse-filter"
              >
                <span>Gudang</span>
                <div className="stock-filter-ro-value" data-testid="stock-warehouse-placeholder" aria-disabled="true">
                  —
                </div>
              </label>
            )}
            <form className="stock-filter-search" onSubmit={doSearch}>
              <Search size={16} aria-hidden="true" />
              <input
                type="text"
                value={qInput}
                onChange={(e) => setQInput(e.target.value)}
                placeholder="Cari kode, nama, atau Accurate ID…"
                data-testid="stock-search"
                aria-label="Cari kode / nama / Accurate ID"
              />
              {q && (
                <button type="button" className="stock-search-clear" onClick={clearSearch} aria-label="Bersihkan">
                  <X size={14} />
                </button>
              )}
            </form>
            <label className="stock-filter-field stock-filter-field--pagesize">
              <span className="sr-only">Per halaman</span>
              <select
                value={pageSize}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
                data-testid="stock-page-size"
                aria-label="Jumlah baris per halaman"
              >
                <option value={25}>25 / halaman</option>
                <option value={50}>50 / halaman</option>
                <option value={100}>100 / halaman</option>
              </select>
            </label>
            <button
              type="button"
              className={`stock-filter-advanced${advancedOpen ? " is-open" : ""}`}
              data-testid="stock-filter-advanced"
              aria-expanded={advancedOpen}
              onClick={() => setAdvancedOpen((v) => !v)}
            >
              <SlidersHorizontal size={15} aria-hidden="true" />
              Filter lanjutan
            </button>
          </div>

          {advancedOpen && (
            <div className="stock-advanced-panel" data-testid="stock-advanced-panel">
              <p className="muted small">
                Stock Dapat Dijual = Stock − (PO aktif − qty dikirim).
                Nilai negatif = kebutuhan stok (tidak memblokir operasional).
              </p>
            </div>
          )}

          <div className="stock-legend-bar" data-testid="stock-legend-bar">
            <div className="stock-legend-lead">
              <Info size={15} aria-hidden="true" />
              <span>Nilai negatif menunjukkan defisit stok.</span>
            </div>
            <button
              type="button"
              className="stock-legend-toggle"
              data-testid="stock-legend-toggle"
              aria-expanded={legendOpen}
              onClick={() => setLegendOpen((v) => !v)}
            >
              Lihat penjelasan lengkap
              <ChevronDown size={14} className={legendOpen ? "is-open" : undefined} aria-hidden="true" />
            </button>
          </div>

          {(statusSummary || statusSummaryErr) && (
            <div className="stock-status-summary" data-testid="stock-status-summary" role="status">
              <div className="stock-status-summary-bar">
                <p className="stock-status-summary-title">Diagnostik status (read-only)</p>
                <button
                  type="button"
                  className="stock-status-summary-detail-toggle"
                  data-testid="stock-status-summary-toggle"
                  aria-expanded={statusSummaryOpen}
                  onClick={() => setStatusSummaryOpen((v) => !v)}
                >
                  {statusSummaryOpen ? "Sembunyikan" : "Tampilkan"}
                  <ChevronDown size={14} className={statusSummaryOpen ? "is-open" : undefined} aria-hidden="true" />
                </button>
              </div>
              <div hidden={!statusSummaryOpen} data-testid="stock-status-summary-body">
                {statusSummaryErr ? (
                  <p className="muted small" data-testid="stock-status-summary-error">{statusSummaryErr}</p>
                ) : (
                  <>
                    <div className="stock-status-summary-grid" data-testid="stock-status-summary-counts">
                      <span data-testid="summary-physical-ok">Fisik OK: {statusSummary.counts?.items_physical_ok ?? 0}</span>
                      <span data-testid="summary-physical-incomplete">Belum lengkap: {statusSummary.counts?.items_physical_incomplete ?? 0}</span>
                      <span data-testid="summary-physical-missing">Belum saldo: {statusSummary.counts?.items_physical_missing ?? 0}</span>
                      <span data-testid="summary-slots-missing">Slot tanpa saldo: {statusSummary.counts?.warehouse_slots_missing_balance ?? 0}</span>
                      <span data-testid="summary-slots-unreconciled">Slot unreconciled: {statusSummary.counts?.warehouse_slots_unreconciled ?? 0}</span>
                      <span data-testid="summary-belum-unverified">Belum Terkirim unverified: {statusSummary.counts?.items_belum_terkirim_unverified ?? 0}</span>
                      <span data-testid="summary-tersedia-invalid">Tersedia invalid: {statusSummary.counts?.items_qty_tersedia_invalid ?? 0}</span>
                    </div>
                    <button
                      type="button"
                      className="stock-status-summary-detail-toggle"
                      data-testid="stock-status-summary-detail-toggle"
                      aria-expanded={statusDetailOpen}
                      onClick={() => setStatusDetailOpen((v) => !v)}
                    >
                      Detail
                      <ChevronDown size={14} className={statusDetailOpen ? "is-open" : undefined} aria-hidden="true" />
                    </button>
                    <div
                      className="stock-status-summary-detail"
                      data-testid="stock-status-summary-detail"
                      hidden={!statusDetailOpen}
                    >
                      {(statusSummary.operational_actions || []).length > 0 && (
                        <ul className="stock-status-summary-actions" data-testid="stock-status-summary-actions">
                          {statusSummary.operational_actions.map((line) => (
                            <li key={line}>{line}</li>
                          ))}
                        </ul>
                      )}
                      <p className="muted small">
                        Angka diagnostik tidak mengubah saldo dan tidak memaksa status verified/nol.
                        {statusSummary.truncated ? " (Cuplikan terpotong — total item &gt; scan.)" : ""}
                      </p>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {data && (
            <div
              className="stock-context-info stock-legend-detail"
              data-testid="stock-context-info"
              hidden={!legendOpen}
            >
              <p className="stock-legend-detail-title">Penjelasan status stok</p>
              <div className="stock-legend-detail-list">
                <p><span className="stock-qty-chip">1.500</span> Stok fisik tersedia dan valid.</p>
                <p><span className="stock-status-badge stock-status-badge--missing">Belum saldo</span> Belum ada saldo fisik untuk gudang ini.</p>
                <p><span className="stock-status-badge stock-status-badge--unverified">Belum terverifikasi</span> Bukti transaksi aktivitas (cetak/kirim) belum cukup.</p>
                <p><span className="stock-status-badge stock-status-badge--incomplete">Belum lengkap</span> Ada satu atau lebih gudang yang belum valid.</p>
                <p><span className="stock-status-badge stock-status-badge--indicative">Indikatif</span> Angka dihitung, tetapi baseline stok belum lengkap atau bukti WIP/rusak/polos belum tercakup — bukan status terverifikasi.</p>
                <p><span className="stock-status-badge stock-status-badge--deficit">Defisit</span> Saldo fisik kurang dari 0.</p>
                <p><span className="stock-legend-invalid">Belum valid</span> Qty tersedia tidak dapat dihitung karena data tidak lengkap.</p>
              </div>
              <p className="muted small" data-testid="stock-read-mode">{modeFullLabel}</p>
              <p className="muted small" data-testid="stock-excel-formula-note">
                Rumus Excel Owner (PCS): Stock Dapat Dijual = Stock − (PO aktif − qty dikirim);
                Qty Polos = Stock − Tercetak Belum Kirim; Tercetak Belum Kirim = Qty Tercetak − Cetak Sudah Dikirim.
                Nilai negatif = defisit (tidak memblokir SO).
              </p>
              {!warehouseActive && (
                <p className="muted small" data-testid="stock-compat-branch-note">
                  {data.physical_qty_note
                    || "Angka Stock Fisik adalah gabungan legacy perusahaan — bukan saldo gudang."}
                </p>
              )}
              {!warehouseActive && !canAdjust && (
                <p className="muted small" data-testid="stock-adjust-blocked-reason">
                  {data.adjust_blocked_reason
                    || "Koreksi/Saldo Awal/Opname gudang memerlukan r2_authority_active atau warehouse_stock_standalone."}
                </p>
              )}
              {warehouseActive && (
                <p className="muted small" data-testid="stock-book-vs-count-note">
                  Kolom Excel lingkup cabang (PO / tercetak / kirim). Saldo gudang belum ada = Belum saldo pada Stock; bukti aktivitas kurang = Belum terverifikasi.
                </p>
              )}
            </div>
          )}

          {(refreshing || (searchPending && items.length > 0)) && (
            <p className="stock-refreshing muted small" data-testid="stock-refreshing" role="status">
              Menyegarkan…
            </p>
          )}

          <div
            className={`prod-table-wrap master-table-wrap stock-table-wrap stock-table-desktop stock-table-compact${actionsLocked && items.length > 0 ? " is-refreshing" : ""}`}
          >
            <table className="prod-table master-items-table stock-dense-table" data-testid="stock-table">
              <thead>
                <tr>
                  <th className="col-text">Produk</th>
                  <th
                    className="col-fit num"
                    data-testid="stock-physical-header"
                    data-physical-scope={warehouseActive ? (selectedFilterWarehouse ? "WAREHOUSE" : "BRANCH") : (modePending ? "PENDING" : "COMPANY")}
                    title="Saldo buku PCS"
                  >
                    Stock
                  </th>
                  <th className="col-fit num" data-testid="stock-siap-jual-header" title="Stock − (PO aktif − qty dikirim); boleh negatif">
                    Stock Dapat Dijual
                  </th>
                  <th className="col-fit num" data-testid="stock-polos-header" title="Stock − Tercetak Belum Kirim (PCS)">
                    Qty Polos
                  </th>
                  <th className="col-fit" data-testid="stock-detail-header">Aksi</th>
                </tr>
              </thead>
              <tbody data-testid="stock-tbody" data-search-state={tableView} data-refreshing={refreshing ? "true" : "false"}>
                {tableView === "loading" && (
                  <tr data-testid="stock-loading">
                    <td colSpan={tableColCount} className="muted small">Memuat…</td>
                  </tr>
                )}
                {tableView === "empty" && (
                  <tr>
                    <td colSpan={tableColCount} className="muted small" data-testid="stock-empty">{emptyText}</td>
                  </tr>
                )}
                {tableView === "results" && items.map((it) => {
                  const selectedWhId = selectedFilterWarehouse?.id || "";
                  const primary = stockPhysicalPrimaryDisplay(it, {
                    warehouseId: selectedWhId,
                    branchAggregate: showBranchAggregate,
                  });
                  const physicalText = primary.present ? fmtQty(primary.qty) : primary.text;
                  const rowNegative = Boolean(primary.negative);
                  const physicalHints = primary.hints || [];
                  const compactFlag = primary.flag;
                  const book = bookPhysicalDisplay(it, { warehouseId: selectedWhId });
                  const dapatCol = EXCEL_SSI_COLUMNS.find((c) => c.key === "stock_dapat_dijual");
                  const polosCol = EXCEL_SSI_COLUMNS.find((c) => c.key === "qty_polos");
                  const dapatDisp = resolveDapatDijualDisplay(it, primary);
                  const dapatNeg = Number(dapatDisp.qty) < 0;
                  return (
                  <tr
                    key={it.master_item_id}
                    data-testid={`stock-row-${it.master_item_id}`}
                    className={rowNegative || dapatNeg ? "stock-row-has-deficit" : undefined}
                  >
                    <td className="col-text" data-testid={`stock-item-${it.master_item_id}`}>
                      <div className="stock-item-primary">{it.item_name || "—"}</div>
                      <div className="stock-item-secondary muted small">
                        <span className="mono" data-testid={`stock-code-${it.master_item_id}`}>{it.item_code || "—"}</span>
                      </div>
                    </td>
                    <td
                      className={`col-fit num mono${rowNegative ? " stock-book-negative" : ""}`}
                      data-testid={`stock-physical-${it.master_item_id}`}
                      data-negative={rowNegative ? "true" : "false"}
                      data-balance-present={primary.present ? "true" : "false"}
                      data-physical-status={primary.text || (compactFlag || "qty")}
                      data-baseline-label={it.baseline_label || ""}
                      data-baseline-status={it.baseline_status || ""}
                      title={physicalHints.length ? physicalHints.join(" · ") : "Saldo buku PCS"}
                    >
                      {renderPhysicalCell(it, primary, physicalText, book)}
                    </td>
                    {excelQtyCell(dapatCol, it, "stock_dapat_dijual", primary)}
                    {excelQtyCell(polosCol, it, "qty_polos", primary)}
                    <td className="col-fit">
                      <button
                        type="button"
                        className="stock-btn-outline sm"
                        data-testid={`stock-detail-${it.master_item_id}`}
                        onClick={() => setDetailItem(it)}
                        disabled={actionsLocked}
                      >
                        Lihat Detail
                      </button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div
            className={`stock-mobile-list${actionsLocked && items.length > 0 ? " is-refreshing" : ""}`}
            data-testid="stock-mobile-list"
            data-search-state={tableView}
          >
            {tableView === "loading" && (
              <div className="stock-mobile-card muted small" data-testid="stock-mobile-loading">Memuat…</div>
            )}
            {tableView === "empty" && (
              <div className="stock-mobile-card muted small" data-testid="stock-mobile-empty">{emptyText}</div>
            )}
            {tableView === "results" && items.map((it) => {
              const selectedWhId = selectedFilterWarehouse?.id || "";
              const primary = stockPhysicalPrimaryDisplay(it, {
                warehouseId: selectedWhId,
                branchAggregate: showBranchAggregate,
              });
              const physicalText = primary.present ? fmtQty(primary.qty) : primary.text;
              const book = bookPhysicalDisplay(it, { warehouseId: selectedWhId });
              const statusKey = !primary.present
                ? (primary.text === "Belum lengkap" ? "incomplete" : "missing")
                : (primary.negative ? "deficit" : "qty");
              const dapatDisp = resolveDapatDijualDisplay(it, primary);
              const polosDisp = resolvePolosDisplay(it, primary);
              const dapatNeg = Number(dapatDisp.qty) < 0;
              return (
                <article
                  key={`m-${it.master_item_id}`}
                  className={`stock-mobile-card${primary.negative || dapatNeg ? " is-deficit" : ""}`}
                  data-testid={`stock-mobile-card-${it.master_item_id}`}
                >
                  <div className="stock-mobile-card-head">
                    <div>
                      <div className="stock-item-primary">{it.item_name || "—"}</div>
                      <div className="stock-item-secondary muted small">
                        <span className="mono">{it.item_code || "—"}</span>
                      </div>
                    </div>
                    <div className="stock-mobile-physical">
                      {renderPhysicalCell(it, primary, physicalText, book)}
                    </div>
                  </div>
                  <div className="stock-mobile-metrics stock-mobile-metrics--compact" data-status={statusKey}>
                    <div>
                      <span>Stock Dapat Dijual</span>
                      <strong
                        className={dapatNeg ? "stock-book-negative" : undefined}
                        data-testid={`stock-siap-jual-mobile-${it.master_item_id}`}
                        data-condition-status={dapatDisp.status}
                        title={dapatDisp.title}
                      >
                        {dapatDisp.mode === "unverified"
                          ? "Belum terverifikasi"
                          : (
                            <>
                              {dapatDisp.qty == null ? "—" : fmtQty(dapatDisp.qty)}
                              {dapatDisp.mode === "indicative" ? (
                                <span className="stock-status-badge stock-status-badge--indicative"> Indikatif</span>
                              ) : null}
                            </>
                          )}
                      </strong>
                    </div>
                    <div>
                      <span>Qty Polos</span>
                      <strong
                        data-testid={`stock-polos-mobile-${it.master_item_id}`}
                        data-condition-status={polosDisp.status}
                        title={polosDisp.title}
                      >
                        {polosDisp.mode === "unverified"
                          ? "Belum terverifikasi"
                          : (
                            <>
                              {fmtQty(polosDisp.qty)}
                              <span className="stock-status-badge stock-status-badge--indicative"> Indikatif</span>
                            </>
                          )}
                      </strong>
                    </div>
                  </div>
                  <div className="stock-mobile-footer">
                    <button
                      type="button"
                      className="stock-btn-outline sm"
                      data-testid={`stock-detail-mobile-${it.master_item_id}`}
                      onClick={() => setDetailItem(it)}
                      disabled={actionsLocked}
                    >
                      Lihat Detail
                    </button>
                  </div>
                </article>
              );
            })}
          </div>

      {pageCount > 1 && (
        <div className="master-pager stock-pager" data-testid="stock-pager">
          <button className="stock-btn-outline sm" disabled={page <= 1 || tableBusy} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
          <span className="muted small">Halaman {data?.page || page} dari {pageCount} · {data?.total || 0} baris</span>
          <button className="stock-btn-outline sm" disabled={page >= pageCount || tableBusy} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
        </div>
      )}

      {detailItem && (
        <StockSaatIniDetailDrawer
          item={detailItem}
          physicalPrimary={stockPhysicalPrimaryDisplay(detailItem, {
            warehouseId: selectedFilterWarehouse?.id || "",
            branchAggregate: showBranchAggregate,
          })}
          branches={branches}
          branchId={branchId}
          warehouseId={warehouseId}
          canAdjust={canAdjust}
          canOpening={canOpening}
          adjustBlockedReason={data?.adjust_blocked_reason}
          onClose={() => setDetailItem(null)}
          onOpenOpening={(row) => {
            setDetailItem(null);
            const wh = singleWarehouse
              || (warehouseId ? adjustWarehouses.find((w) => w.id === warehouseId) : null)
              || null;
            setOpeningSeed({
              itemId: row.master_item_id,
              branchId: wh?.branch_id || effectiveBranchId || branchId || "",
              warehouseId: wh?.id || warehouseId || "",
            });
            setOpeningOpen(true);
          }}
          onOpenAdjust={(row) => {
            setDetailItem(null);
            beginAdjust(row, { balancePresent: true });
          }}
        />
      )}

      {openingOpen && (
        <SaldoAwalModal
          canSubmit={canOpening}
          branches={branches}
          warehouses={warehouses}
          catalogItems={items}
          initialSeed={openingSeed}
          onClose={() => { setOpeningOpen(false); setOpeningSeed(null); }}
          onSaved={reloadAfterMutation}
        />
      )}
      {adjustItem && adjustWarehouses.length > 1 && (
        <PhysicalAdjustModal
          item={adjustItem}
          branches={branches}
          warehouses={adjustWarehouses}
          onClose={() => setAdjustItem(null)}
          onSaved={reloadAfterMutation}
        />
      )}
    </div>
  );
}

function InlinePhysicalEditor({ item, saving, onCancel, onSave }) {
  const [draft, setDraft] = useState(() => String(item.physical_qty ?? 0));
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const save = () => {
    if (saving) return;
    const raw = String(draft).trim();
    if (!/^\d+$/.test(raw)) {
      setError("Target stok fisik harus bilangan 0 atau lebih.");
      return;
    }
    onSave(Number(raw), setError, onCancel);
  };

  return (
    <span data-testid={`stock-inline-${item.master_item_id}`} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <input
        ref={inputRef}
        value={draft}
        inputMode="numeric"
        aria-label="Target Stock Fisik"
        data-testid={`stock-inline-input-${item.master_item_id}`}
        disabled={saving}
        style={{ width: 72 }}
        onChange={(e) => {
          const next = e.target.value;
          if (next === "" || /^\d+$/.test(next)) {
            setDraft(next);
            setError("");
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            save();
          } else if (e.key === "Escape") {
            e.preventDefault();
            if (!saving) onCancel();
          }
        }}
      />
      <button type="button" className="ghost sm" aria-label="Simpan" data-testid={`stock-inline-save-${item.master_item_id}`} disabled={saving} onClick={save}>
        {saving ? "…" : <Check size={14} />}
      </button>
      <button type="button" className="ghost sm" aria-label="Batal" data-testid={`stock-inline-cancel-${item.master_item_id}`} disabled={saving} onClick={onCancel}>
        <X size={14} />
      </button>
      {error && <span role="alert" data-testid={`stock-inline-error-${item.master_item_id}`}>{error}</span>}
    </span>
  );
}

function PhysicalAdjustModal({ item, branches = [], warehouses, onClose, onSaved }) {
  const [warehouseId, setWarehouseId] = useState(() => preselectedWarehouseId(warehouses));
  const [currentQty, setCurrentQty] = useState(null);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [idempotencyKey] = useState(newKey);
  const warehouse = warehouses.find((row) => row.id === warehouseId);
  const parsed = Number(String(target).replace(",", "."));
  const targetOk = target !== "" && Number.isFinite(parsed) && parsed >= 0 && Number.isInteger(parsed);
  const ready = Boolean(warehouseId && targetOk && reason.trim() && !saving);

  useEffect(() => {
    if (!warehouseId) {
      setCurrentQty(null);
      return undefined;
    }
    let cancelled = false;
    api.physicalStock(item.master_item_id, { branch_id: warehouse?.branch_id, warehouse_id: warehouseId })
      .then((res) => {
        if (cancelled) return;
        const row = (res.items || []).find((entry) => entry.warehouse_id === warehouseId);
        setCurrentQty(row ? row.physical_qty : 0);
      })
      .catch(() => {
        if (!cancelled) setError("Gagal memuat stok fisik gudang");
      });
    return () => { cancelled = true; };
  }, [item.master_item_id, warehouseId, warehouse?.branch_id]);

  const submit = async (event) => {
    event.preventDefault();
    if (!ready) return;
    setSaving(true);
    setError("");
    try {
      await api.createPhysicalAdjustment(item.master_item_id, {
        branch_id: warehouse.branch_id,
        warehouse_id: warehouseId,
        target_physical_qty: parsed,
        reason: reason.trim(),
        idempotency_key: idempotencyKey,
      });
      await onSaved();
      onClose();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setError(typeof det === "object" ? det.message : (det || "Gagal menyimpan koreksi"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="stock-adjust-overlay" data-testid="stock-adjust-modal" role="dialog" aria-modal="true" aria-labelledby="stock-adjust-title">
      <form className="stock-adjust-dialog" onSubmit={submit}>
        <header className="stock-adjust-header">
          <h2 id="stock-adjust-title">Koreksi Stock Fisik</h2>
          <button type="button" className="stock-adjust-close" onClick={onClose} aria-label="Tutup">
            <X size={16} />
          </button>
        </header>
        <p className="stock-adjust-identity">
          <span className="mono">{item.item_code || "—"}</span>
          <span aria-hidden="true"> — </span>
          <span>{item.item_name || "—"}</span>
        </p>
        <p className="stock-adjust-hint" data-testid="stock-adjust-hint">
          Penyesuaian saldo buku saat ini (MANUAL_ADJUSTMENT, PCS) — delta beraudit,
          boleh diulang. Bukan menimpa Saldo Awal / OPENING_BALANCE historis.
          Target = stok fisik gudang yang dipilih, bukan agregat seluruh gudang.
        </p>
        {error && (
          <div className="signal-strip" role="alert">
            <div><AlertCircle size={16} /><span>{error}</span></div>
          </div>
        )}
        <label className="stock-adjust-field">
          <span>Gudang</span>
          <select value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)} data-testid="stock-adjust-warehouse">
            <option value="">Pilih gudang</option>
            {warehouses.map((row) => (
              <option key={row.id} value={row.id}>{warehouseLocationLabel(row, branches)}</option>
            ))}
          </select>
        </label>
        <label className="stock-adjust-field">
          <span>Stok fisik saat ini</span>
          <div className="stock-adjust-readonly" data-testid="stock-adjust-current">
            {currentQty == null ? "—" : fmtQty(currentQty)}
          </div>
        </label>
        <label className="stock-adjust-field">
          <span>Target Stock Fisik</span>
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            inputMode="numeric"
            placeholder="0"
            data-testid="stock-adjust-target"
          />
        </label>
        <label className="stock-adjust-field">
          <span>Alasan Koreksi <em>(wajib)</em></span>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={4}
            placeholder="Jelaskan alasan koreksi…"
            data-testid="stock-adjust-reason"
          />
        </label>
        <div className="stock-adjust-actions">
          <button type="button" className="stock-btn-outline" onClick={onClose} disabled={saving}>
            Batal
          </button>
          <button type="submit" className="stock-btn-primary" disabled={!ready} data-testid="stock-adjust-submit">
            {saving ? "Menyimpan…" : "Simpan Koreksi"}
          </button>
        </div>
      </form>
    </div>
  );
}

function importMessage(ex, fallback) {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string" && det.trim()) return det;
  if (det && typeof det.message === "string" && det.message.trim()) return det.message;
  return fallback;
}

function catalogToPickerItems(rows) {
  const seen = new Set();
  const out = [];
  for (const row of rows || []) {
    const id = String(row?.master_item_id || row?.id || "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({
      id,
      item_code: row.item_code || "",
      item_name: row.item_name || row.name || "",
      unit: row.base_unit || row.unit || "PCS",
    });
  }
  return out;
}

function SaldoAwalModal({
  canSubmit,
  branches,
  warehouses,
  catalogItems = [],
  initialSeed = null,
  onClose,
  onSaved,
}) {
  const [tab, setTab] = useState("manual"); // manual | import
  const [items, setItems] = useState(() => catalogToPickerItems(catalogItems));
  const [itemsLoading, setItemsLoading] = useState(true);
  const [itemsError, setItemsError] = useState("");
  const [itemsDenied, setItemsDenied] = useState(false);
  const [itemId, setItemId] = useState(() => String(initialSeed?.itemId || ""));
  const [branchId, setBranchId] = useState(() => String(initialSeed?.branchId || ""));
  const [warehouseId, setWarehouseId] = useState(() => String(initialSeed?.warehouseId || ""));
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState(newKey);
  const [preview, setPreview] = useState(null);
  const [importKey, setImportKey] = useState("");
  const [selectedKeys, setSelectedKeys] = useState(() => new Set());
  const [lockedLines, setLockedLines] = useState(null);
  /** idle | submitting | needs_resume */
  const [importTxn, setImportTxn] = useState("idle");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [importNotice, setImportNotice] = useState("");
  const [templateWarehouseId, setTemplateWarehouseId] = useState("");
  const [showFieldErrors, setShowFieldErrors] = useState(false);
  const options = warehouseChoices(warehouses, branchId).filter(isOperationalWarehouse);
  const selected = items.find((row) => row.id === itemId);
  const baseUnit = (selected?.unit || "PCS").toUpperCase();
  const fieldErrors = openingFieldErrors({ itemId, branchId, warehouseId, qty });

  useEffect(() => {
    let cancelled = false;
    setItemsLoading(true);
    setItemsError("");
    setItemsDenied(false);
    // Prefer the same catalog the Stock Saat Ini table shows, then enrich via master list.
    const fromCatalog = catalogToPickerItems(catalogItems);
    if (fromCatalog.length) setItems(fromCatalog);
    api.masterItems({ page_size: 200 })
      .then((res) => {
        if (cancelled) return;
        const fromApi = (res.items || []).map((row) => ({
          id: row.id,
          item_code: row.item_code || "",
          item_name: row.item_name || row.name || "",
          unit: row.unit || "PCS",
        }));
        // Union by id — catalog first so visible stock rows stay selectable even if
        // master list is filtered/paged differently.
        const byId = new Map();
        for (const row of [...fromCatalog, ...fromApi]) {
          if (row?.id && !byId.has(row.id)) byId.set(row.id, row);
        }
        setItems([...byId.values()]);
      })
      .catch((ex) => {
        if (cancelled) return;
        const statusCode = ex?.response?.status;
        if (statusCode === 401 || statusCode === 403) {
          setItemsDenied(true);
          setItemsError("Tidak ada izin membaca Barang/Jasa (master_items.view).");
        } else {
          setItemsError(typeof ex?.response?.data?.detail === "string"
            ? ex.response.data.detail
            : "Gagal memuat daftar Barang/Jasa.");
        }
        if (!fromCatalog.length) setItems([]);
      })
      .finally(() => { if (!cancelled) setItemsLoading(false); });
    return () => { cancelled = true; };
  }, [catalogItems]);

  useEffect(() => {
    if (warehouseId && !options.some((row) => row.id === warehouseId)) setWarehouseId("");
  }, [options, warehouseId]);

  useEffect(() => {
    if (!itemId || !branchId || !warehouseId) {
      setStatus(null);
      return undefined;
    }
    let cancelled = false;
    setStatusLoading(true);
    setError("");
    api.physicalOpeningBalance(itemId, { branch_id: branchId, warehouse_id: warehouseId })
      .then((res) => { if (!cancelled) setStatus(res); })
      .catch((ex) => {
        if (cancelled) return;
        const det = ex.response?.data?.detail;
        setStatus(null);
        setError(typeof det === "object" ? det.message : (det || "Gagal memeriksa saldo awal"));
      })
      .finally(() => { if (!cancelled) setStatusLoading(false); });
    return () => { cancelled = true; };
  }, [itemId, branchId, warehouseId]);

  const established = Boolean(status?.established);
  const ready = canSubmit && !established && !statusLoading && openingReady({ itemId, branchId, warehouseId, qty });
  const branch = branches.find((row) => row.id === branchId);
  const warehouse = options.find((row) => row.id === warehouseId);

  const templateWarehouses = operationalOpeningWarehouses(branches, warehouses);
  const downloadTemplate = async () => {
    setImportError("");
    if (!templateWarehouseId) {
      setImportError("Pilih gudang sebelum mengunduh template.");
      return;
    }
    try {
      const blob = await api.physicalOpeningImportTemplate({ warehouse_id: templateWarehouseId });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "saldo-awal-stock-fisik.xlsx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (ex) {
      setImportError(importMessage(ex, "Gagal mengunduh template"));
    }
  };

  const importSummary = summarizeImportRows(preview?.rows || [], selectedKeys);
  const selectionLocked = importTxn === "needs_resume" || importTxn === "submitting" || importing;
  const canConfirmImport = Boolean(
    importKey
    && !importing
    && importTxn !== "submitting"
    && (
      (importTxn === "needs_resume" && Array.isArray(lockedLines) && lockedLines.length > 0)
      || (importTxn === "idle" && importSummary.selectedCount > 0)
    ),
  );

  const toggleReadyRow = (row) => {
    if (!isReadyRow(row) || selectionLocked) return;
    const key = rowSelectKey(row);
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const onImportFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (importTxn === "needs_resume" || importing) {
      setImportError("Selesaikan atau lanjutkan transaksi impor yang belum selesai sebelum mengunggah file baru.");
      return;
    }
    setImportError("");
    setImportNotice("");
    setPreview(null);
    setSelectedKeys(new Set());
    setLockedLines(null);
    setImportTxn("idle");
    try {
      const result = await api.physicalOpeningImportPreview(file);
      setPreview(result);
      setSelectedKeys(defaultSelectedKeys(result?.rows || []));
      setImportKey(newKey());
    } catch (ex) {
      setImportError(importMessage(ex, "File tidak dapat diperiksa"));
    }
  };

  const confirmImport = async () => {
    if (!canConfirmImport || !importKey || importing) return;
    const lines = importTxn === "needs_resume" && Array.isArray(lockedLines)
      ? lockedLines
      : buildConfirmLines(importSummary.selectedRows);
    if (!lines.length) return;
    setImporting(true);
    setImportError("");
    setImportNotice("");
    setImportTxn("submitting");
    setLockedLines(lines);
    try {
      const result = await api.physicalOpeningImportConfirm({
        idempotency_key: importKey,
        lines,
      });
      const kind = classifyImportConfirmResponse(result);
      if (kind === "confirmed") {
        setPreview(null);
        setImportKey("");
        setSelectedKeys(new Set());
        setLockedLines(null);
        setImportTxn("idle");
        setImportNotice("Impor saldo awal berhasil dikonfirmasi.");
        await onSaved();
        return;
      }
      if (kind === "processing") {
        setImportTxn("needs_resume");
        setImportError("Status batch: PROCESSING. Klik Lanjutkan untuk menunggu/menyelesaikan transaksi yang sama.");
        return;
      }
      // Backend did not return a terminal status we recognize — keep key/payload, do not invent success.
      setImportTxn("needs_resume");
      setImportError(IMPORT_RESUME_SAFE_MESSAGE);
    } catch (ex) {
      setImportTxn("needs_resume");
      const detail = ex?.response?.data?.detail;
      const batchStatus = typeof detail === "object"
        ? String(detail?.import?.status || detail?.status || "").toUpperCase()
        : "";
      if (batchStatus === "PROCESSING") {
        setImportError(`Status batch: PROCESSING. ${importMessage(ex, "Impor terhenti.")} Klik Lanjutkan dengan key yang sama.`);
      } else {
        // Gap: most failures do not embed PROCESSING — keep safe resume copy + server message.
        setImportError(`${importMessage(ex, "Impor saldo awal gagal")}. ${IMPORT_RESUME_SAFE_MESSAGE}`);
      }
    } finally {
      setImporting(false);
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    setShowFieldErrors(true);
    if (Object.keys(fieldErrors).length || !ready || saving) {
      if (Object.keys(fieldErrors).length) {
        setError("Lengkapi field yang ditandai sebelum menetapkan saldo awal.");
      }
      return;
    }
    setSaving(true);
    setError("");
    try {
      await api.createPhysicalOpeningBalance(itemId, {
        branch_id: branchId,
        warehouse_id: warehouseId,
        physical_qty: Number(String(qty).replace(",", ".")),
        reason: reason.trim(),
        idempotency_key: idempotencyKey,
      });
      const next = await api.physicalOpeningBalance(itemId, { branch_id: branchId, warehouse_id: warehouseId });
      setStatus(next);
      setIdempotencyKey(newKey());
      await onSaved();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setError(typeof det === "object" ? det.message : (det || "Gagal menetapkan saldo awal"));
    } finally {
      setSaving(false);
    }
  };

  const itemsStateMessage = (() => {
    if (itemsLoading && items.length === 0) return { testId: "saldo-awal-items-loading", text: "Memuat daftar Barang/Jasa…" };
    if (itemsDenied) return { testId: "saldo-awal-items-denied", text: itemsError || "Tidak ada izin membaca Barang/Jasa." };
    if (itemsError && items.length === 0) return { testId: "saldo-awal-items-error", text: itemsError };
    if (!itemsLoading && items.length === 0) {
      return { testId: "saldo-awal-empty-items", text: "Belum ada Barang/Jasa pada environment preview." };
    }
    return null;
  })();

  return (
    <div className="mutation-drawer-overlay saldo-awal-overlay" data-testid="saldo-awal-modal">
      <div
        className={`saldo-awal-panel${tab === "import" ? " saldo-awal-panel--import" : ""}`}
        data-testid="saldo-awal-panel"
      >
        <div className="saldo-awal-panel-head">
          <h2 data-testid="saldo-awal-title">Saldo Awal Stock Fisik</h2>
          <button type="button" className="ghost sm" onClick={onClose} aria-label="Tutup">Tutup</button>
        </div>

        <div className="stock-heading-actions saldo-awal-tabs" role="tablist" aria-label="Alur saldo awal" data-testid="saldo-awal-tabs">
          <button
            type="button"
            className={tab === "manual" ? "primary" : "ghost"}
            role="tab"
            aria-selected={tab === "manual"}
            data-testid="saldo-awal-tab-manual"
            onClick={() => setTab("manual")}
          >
            Input Manual
          </button>
          <button
            type="button"
            className={tab === "import" ? "primary" : "ghost"}
            role="tab"
            aria-selected={tab === "import"}
            data-testid="saldo-awal-tab-import"
            onClick={() => setTab("import")}
          >
            Import Excel
          </button>
        </div>

        {tab === "import" && (
          <div className="saldo-awal-import-panel" data-testid="saldo-awal-import-panel">
            <p className="muted small">
              Unduh template per gudang, isi qty fisik, lalu impor. Hanya baris Siap yang dapat dipilih dan dikonfirmasi
              (subset diizinkan meski file masih berisi baris ditolak). Saldo yang sudah ditetapkan tidak diinisialisasi ulang.
            </p>
            <div className="saldo-awal-fields saldo-awal-fields--import">
              <label className="saldo-awal-field">
                <span className="saldo-awal-label">Gudang</span>
                <select value={templateWarehouseId} onChange={(e) => setTemplateWarehouseId(e.target.value)} data-testid="opening-import-warehouse">
                  <option value="">Pilih gudang</option>
                  {templateWarehouses.map((row) => (
                    <option key={row.id} value={row.id}>{warehouseLocationLabel(row, branches)}</option>
                  ))}
                </select>
              </label>
              <div className="saldo-awal-import-actions">
                <button type="button" className="ghost" onClick={downloadTemplate} disabled={!templateWarehouseId} data-testid="opening-import-template">Download Template</button>
                <label className={`ghost saldo-awal-file-btn${selectionLocked ? " is-disabled" : ""}`}>
                  Pilih File Excel
                  <input
                    type="file"
                    accept=".xlsx,.csv,text/csv"
                    onChange={onImportFile}
                    data-testid="opening-import-file"
                    disabled={selectionLocked}
                  />
                </label>
              </div>
            </div>
            {importNotice && !importError && (
              <div className="signal-strip" role="status" data-testid="opening-import-notice">
                <div><Info size={16} /><span>{importNotice}</span></div>
              </div>
            )}
            {importError && (
              <div className="signal-strip" role="alert" data-testid="opening-import-error">
                <div><AlertCircle size={16} /><span>{importError}</span></div>
              </div>
            )}
            {importTxn === "needs_resume" && (
              <p className="muted small" data-testid="opening-import-resume-hint">
                Transaksi impor menunggu penyelesaian. Pilihan baris dikunci. Idempotency key tetap sama.
              </p>
            )}
            {preview && (
              <>
                <div className="saldo-awal-import-summary" data-testid="opening-import-summary">
                  <span data-testid="opening-import-count-ready">Siap: {importSummary.readyCount}</span>
                  <span data-testid="opening-import-count-selected">Terpilih: {importSummary.selectedCount}</span>
                  <span data-testid="opening-import-count-rejected">Ditolak: {importSummary.rejectedCount}</span>
                </div>
                <div className="saldo-awal-preview-wrap" data-testid="opening-import-preview-wrap">
                  <table className="prod-table saldo-awal-import-table" data-testid="opening-import-preview">
                    <thead>
                      <tr>
                        <th className="saldo-awal-import-check-col">Pilih</th>
                        <th>Kode</th>
                        <th>Nama Barang</th>
                        <th className="saldo-awal-import-col-center">Satuan</th>
                        <th>Gudang</th>
                        <th className="saldo-awal-import-col-center">Stock Fisik Excel</th>
                        <th>Status</th>
                        <th>Alasan</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(preview.rows || []).map((row) => {
                        const ready = isReadyRow(row);
                        const key = rowSelectKey(row);
                        const checked = ready && selectedKeys.has(key);
                        return (
                          <tr key={`${row.row_number}-${row.item_code}`} data-testid={`opening-import-row-${row.row_number}`}>
                            <td className="saldo-awal-import-col-center">
                              <input
                                type="checkbox"
                                checked={checked}
                                disabled={!ready || selectionLocked}
                                onChange={() => toggleReadyRow(row)}
                                data-testid={`opening-import-check-${row.row_number}`}
                                aria-label={ready ? `Pilih ${row.item_code || row.row_number}` : "Baris tidak dapat dipilih"}
                              />
                            </td>
                            <td className="mono">{row.item_code || "—"}</td>
                            <td>{row.item_name || "—"}</td>
                            <td className="saldo-awal-import-col-center">{row.unit || "—"}</td>
                            <td>{row.warehouse_name || "—"}</td>
                            <td className="saldo-awal-import-col-center mono">{row.physical_qty == null ? "—" : fmtQty(row.physical_qty)}</td>
                            <td data-testid={`opening-import-status-${row.row_number}`}>
                              <span className={`saldo-awal-import-status${ready ? " is-ready" : " is-rejected"}`}>
                                {row.status || "—"}
                              </span>
                            </td>
                            <td className="saldo-awal-import-reason" data-testid={`opening-import-reason-${row.row_number}`}>
                              {row.reason || "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <div className="saldo-awal-import-confirm-row">
              <button
                type="button"
                className="primary"
                disabled={!canConfirmImport}
                onClick={confirmImport}
                data-testid="opening-import-confirm"
              >
                {importing
                  ? "Menyimpan…"
                  : (importTxn === "needs_resume" ? "Lanjutkan" : "Konfirmasi Saldo Awal")}
              </button>
            </div>
          </div>
        )}

        {tab === "manual" && (
          <form onSubmit={submit} className="saldo-awal-form" data-testid="saldo-awal-form" noValidate>
            <p className="muted small">
              Urutan: Barang → Cabang → Gudang → Qty → Satuan → Catatan.
              Tidak ada double posting; kombinasi yang sudah established tidak dapat diinisialisasi ulang.
            </p>
            {itemsStateMessage && (
              <p data-testid={itemsStateMessage.testId} className={itemsDenied || itemsError ? "signal-strip" : "muted"}>
                {itemsStateMessage.text}
              </p>
            )}
            {itemsError && items.length > 0 && (
              <div className="signal-strip" role="status" data-testid="saldo-awal-items-partial">
                <div><AlertCircle size={16} /><span>{itemsError} Menampilkan daftar dari katalog Stock Saat Ini.</span></div>
              </div>
            )}
            {error && (
              <div className="signal-strip" role="alert" data-testid="saldo-awal-form-error">
                <div><AlertCircle size={16} /><span>{error}</span></div>
              </div>
            )}
            <div className="saldo-awal-fields" data-testid="saldo-awal-fields">
              <label className="saldo-awal-field saldo-awal-field--full">
                <span className="saldo-awal-label">Barang</span>
                <select
                  value={itemId}
                  onChange={(e) => { setItemId(e.target.value); setShowFieldErrors(false); }}
                  data-testid="saldo-awal-item"
                  aria-invalid={showFieldErrors && Boolean(fieldErrors.itemId)}
                  disabled={established || (itemsLoading && items.length === 0)}
                >
                  <option value="">{itemsLoading && items.length === 0 ? "Memuat…" : "Pilih barang"}</option>
                  {items.map((row) => (
                    <option key={row.id} value={row.id}>{row.item_code || "—"} — {row.item_name || row.name || row.id}</option>
                  ))}
                </select>
                {showFieldErrors && fieldErrors.itemId && (
                  <span className="saldo-awal-field-error" data-testid="saldo-awal-error-item">{fieldErrors.itemId}</span>
                )}
              </label>
              <label className="saldo-awal-field">
                <span className="saldo-awal-label">Cabang</span>
                <select
                  value={branchId}
                  onChange={(e) => { setBranchId(e.target.value); setShowFieldErrors(false); }}
                  data-testid="saldo-awal-branch"
                  aria-invalid={showFieldErrors && Boolean(fieldErrors.branchId)}
                  disabled={established}
                >
                  <option value="">Pilih cabang</option>
                  {branches.map((row) => (
                    <option key={row.id} value={row.id}>{row.code} — {row.name}</option>
                  ))}
                </select>
                {showFieldErrors && fieldErrors.branchId && (
                  <span className="saldo-awal-field-error" data-testid="saldo-awal-error-branch">{fieldErrors.branchId}</span>
                )}
              </label>
              <label className="saldo-awal-field">
                <span className="saldo-awal-label">Gudang</span>
                <select
                  value={warehouseId}
                  onChange={(e) => { setWarehouseId(e.target.value); setShowFieldErrors(false); }}
                  data-testid="saldo-awal-warehouse"
                  aria-invalid={showFieldErrors && Boolean(fieldErrors.warehouseId)}
                  disabled={established}
                >
                  <option value="">Pilih gudang</option>
                  {options.map((row) => (
                    <option key={row.id} value={row.id}>{warehouseLocationLabel(row, branches)}</option>
                  ))}
                </select>
                {showFieldErrors && fieldErrors.warehouseId && (
                  <span className="saldo-awal-field-error" data-testid="saldo-awal-error-warehouse">{fieldErrors.warehouseId}</span>
                )}
              </label>
              <p className="muted small" data-testid="saldo-awal-delta-hint">
                Saldo Awal = OPENING_BALANCE sekali per item/cabang/gudang (PCS).
                Koreksi berikutnya memakai Penyesuaian (delta MANUAL_ADJUSTMENT), bukan menimpa ledger historis.
              </p>
              {established ? (
                <div className="saldo-awal-established" data-testid="saldo-awal-established">
                  <strong>SALDO AWAL SUDAH DITETAPKAN</strong>
                  <dl className="saldo-awal-established-grid">
                    <div><dt>Barang</dt><dd>{selected?.item_name || selected?.item_code || itemId}</dd></div>
                    <div><dt>Cabang</dt><dd>{branch?.name || branch?.code || branchId}</dd></div>
                    <div><dt>Gudang</dt><dd>{warehouse?.name || warehouse?.code || warehouseId}</dd></div>
                    <div><dt>Saldo Awal</dt><dd>{fmtQty(status.opening_qty)} {baseUnit}</dd></div>
                    <div><dt>Satuan</dt><dd>{baseUnit}</dd></div>
                    <div><dt>Waktu Penetapan</dt><dd>{status.occurred_at ? formatDate(status.occurred_at) : "—"}</dd></div>
                  </dl>
                </div>
              ) : (
                <>
                  <label className="saldo-awal-field">
                    <span className="saldo-awal-label">Qty</span>
                    <input
                      value={qty}
                      onChange={(e) => { setQty(e.target.value); setShowFieldErrors(false); }}
                      inputMode="decimal"
                      data-testid="saldo-awal-qty"
                      aria-invalid={showFieldErrors && Boolean(fieldErrors.qty)}
                      placeholder="0"
                    />
                    {showFieldErrors && fieldErrors.qty && (
                      <span className="saldo-awal-field-error" data-testid="saldo-awal-error-qty">{fieldErrors.qty}</span>
                    )}
                  </label>
                  <label className="saldo-awal-field">
                    <span className="saldo-awal-label">Satuan</span>
                    <input value={baseUnit} readOnly data-testid="saldo-awal-unit" />
                  </label>
                  <label className="saldo-awal-field saldo-awal-field--full">
                    <span className="saldo-awal-label">Catatan</span>
                    <input value={reason} onChange={(e) => setReason(e.target.value)} data-testid="saldo-awal-reason" placeholder="Opsional" />
                  </label>
                  <div className="saldo-awal-actions">
                    <button type="submit" className="primary" disabled={saving || statusLoading || !canSubmit} data-testid="saldo-awal-submit">
                      {saving ? "Menyimpan…" : "Tetapkan Saldo Awal"}
                    </button>
                  </div>
                </>
              )}
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
