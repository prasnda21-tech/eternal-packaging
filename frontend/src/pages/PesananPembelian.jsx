import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  AlertTriangle,
  Building2,
  CalendarDays,
  FileText,
  MapPin,
  Plus,
  Search,
  Wallet,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  applyCreatePoBranchFetchResult,
  canShowLegacyPoBranchComplete,
  errDetail,
  filterActiveBranchesForPoComplete,
  formatRupiah,
  friendlyLoadError,
  newIdempotencyKey,
  PO_CREATE_BRANCH,
  purchaseBranchLabel,
  purchaseStatusPillClass,
  shortReceivingStatus,
  todayYmd,
  purchaseRowInteractionProps,
} from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import TransactionUomSelect from "../components/TransactionUomSelect";
import {
  buildTransactionUomOptions,
  defaultTransactionUom,
  normalizeUom,
} from "../lib/transactionUomOptions";
import PurchasingFlowNav from "./PurchasingFlowNav";
import PurchaseCancelModal from "./PurchaseCancelModal";
import PurchaseActivityHistory from "./PurchaseActivityHistory";
import PurchaseDocuments from "./PurchaseDocuments";
import "./purchasePages.css";

function emptyPoLine() {
  return {
    key: `${Date.now()}-${Math.random()}`,
    request_line_id: "",
    master_item_id: "",
    accurate_item_id: "",
    item_name: "",
    item_code: "",
    unit: "PCS",
    uom_options: [{ uom: "PCS", factor_to_base: 1, label: "PCS (dasar)" }],
    ordered_qty: "",
    unit_price: "",
    remaining_hint: null,
  };
}

async function uomFieldsForMaster(it) {
  const fallbackUnit = normalizeUom(it?.unit || "PCS");
  const mid = it?.id || it?.master_item_id || "";
  if (!mid) {
    return {
      unit: fallbackUnit,
      uom_options: buildTransactionUomOptions(null, { unit: fallbackUnit }),
    };
  }
  try {
    const detail = await api.masterItemDetail(mid);
    const options = buildTransactionUomOptions(detail?.unit_setting, detail?.item || it);
    return {
      unit: defaultTransactionUom(options, fallbackUnit),
      uom_options: options,
    };
  } catch {
    return {
      unit: fallbackUnit,
      uom_options: buildTransactionUomOptions(null, { unit: fallbackUnit }),
    };
  }
}

export default function PesananPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_orders.create");
  const canEditBranch = hasPerm("purchase_orders.edit_branch");
  const queryRequestId = searchParams.get("request_id") || "";
  const wantCreate = searchParams.get("action") === "create" || Boolean(queryRequestId);
  const [view, setView] = useState(wantCreate && canCreate ? "create" : "list");
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [detail, setDetail] = useState(null);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelErr, setCancelErr] = useState("");

  const [branchModalOpen, setBranchModalOpen] = useState(false);
  const [branchOptions, setBranchOptions] = useState([]);
  const [branchOptionsErr, setBranchOptionsErr] = useState("");
  const [branchOptionsLoading, setBranchOptionsLoading] = useState(false);
  const [branchIdPick, setBranchIdPick] = useState("");
  const [branchReason, setBranchReason] = useState("");
  const [branchConfirmReady, setBranchConfirmReady] = useState(false);
  const [branchBusy, setBranchBusy] = useState(false);
  const [branchErr, setBranchErr] = useState("");
  const branchSubmitLockRef = useRef(false);

  const [mode, setMode] = useState(queryRequestId ? "from_request" : "direct");
  const [eligible, setEligible] = useState([]);
  const [requestId, setRequestId] = useState(canCreate ? queryRequestId : "");
  /** YGY-first create display from master branches API (code/name only — never hardcode id). */
  const [createBranchLabel, setCreateBranchLabel] = useState(PO_CREATE_BRANCH.loading);
  const createBranchFetchGen = useRef(0);
  const [supplier, setSupplier] = useState("");
  const [orderDate, setOrderDate] = useState(todayYmd());
  const [lines, setLines] = useState([emptyPoLine()]);
  const [busy, setBusy] = useState(false);
  const [advances, setAdvances] = useState([]);
  const [advanceSummary, setAdvanceSummary] = useState(null);
  const [advanceAmount, setAdvanceAmount] = useState("");
  const [advanceDate, setAdvanceDate] = useState(todayYmd());
  const [advanceBusy, setAdvanceBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.listPurchaseOrders({ q });
      setRows(res.orders || []);
    } catch (ex) { setErr(friendlyLoadError(ex, "Tidak dapat memuat pesanan. Periksa koneksi server lalu coba lagi.")); }
    finally { setLoading(false); }
  }, [q]);

  useEffect(() => { if (view === "list") load(); }, [load, view]);
  useEffect(() => { setPage(1); }, [q]);

  const loadEligible = useCallback(async () => {
    try {
      const res = await api.listPurchaseRequests({ eligible_for_po: true });
      setEligible(res.requests || []);
    } catch { setEligible([]); }
  }, []);

  const applyRequestPrefill = useCallback(async (id) => {
    if (!id) {
      setLines([emptyPoLine()]);
      return;
    }
    setErr("");
    try {
      const p = await api.purchaseOrderPreview(id);
      const next = (p.lines || [])
        .filter((l) => Number(l.remaining_order_qty) > 0)
        .map((l) => ({
          key: l.request_line_id || `${Date.now()}-${Math.random()}`,
          request_line_id: l.request_line_id,
          master_item_id: l.master_item_id || "",
          accurate_item_id: l.accurate_item_id || "",
          item_name: l.item_name || "",
          item_code: l.item_code || "",
          unit: l.unit || "pcs",
          ordered_qty: l.remaining_order_qty,
          unit_price: l.unit_price || "",
          remaining_hint: l.remaining_order_qty,
        }));
      setLines(next.length ? next : [emptyPoLine()]);
    } catch (ex) {
      setErr(errDetail(ex));
      setLines([emptyPoLine()]);
    }
  }, []);

  useEffect(() => {
    if (view !== "create") return;
    if (!canCreate) {
      setView("list");
      setErr("Anda tidak memiliki izin membuat Pesanan Pembelian.");
      return;
    }
    if (mode === "from_request") {
      loadEligible();
      if (requestId) applyRequestPrefill(requestId);
    } else {
      setRequestId("");
      setLines([emptyPoLine()]);
    }
  }, [view, mode, requestId, loadEligible, applyRequestPrefill, canCreate]);

  useEffect(() => {
    if (view !== "create") return undefined;
    const gen = ++createBranchFetchGen.current;
    let cancelled = false;
    setCreateBranchLabel(PO_CREATE_BRANCH.loading);
    api.branches()
      .then((res) => {
        const next = applyCreatePoBranchFetchResult(gen, createBranchFetchGen.current, cancelled, {
          kind: "ok",
          items: res?.items || [],
        });
        if (next != null) setCreateBranchLabel(next);
      })
      .catch((ex) => {
        const status = ex?.response?.status;
        const outcome = Number(status) === 403
          ? { kind: "http", status: 403 }
          : { kind: "error" };
        const next = applyCreatePoBranchFetchResult(gen, createBranchFetchGen.current, cancelled, outcome);
        if (next != null) setCreateBranchLabel(next);
      });
    return () => { cancelled = true; };
  }, [view]);

  const setLine = (i, patch) => setLines((p) => p.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const addLine = () => setLines((p) => [...p, emptyPoLine()]);
  const removeLine = (i) => setLines((p) => (p.length <= 1 ? p : p.filter((_, idx) => idx !== i)));

  const pickItem = async (i, it) => {
    if (!it) {
      return setLine(i, {
        master_item_id: "",
        accurate_item_id: "",
        item_name: "",
        item_code: "",
        unit: "PCS",
        uom_options: [{ uom: "PCS", factor_to_base: 1, label: "PCS (dasar)" }],
        request_line_id: "",
        remaining_hint: null,
      });
    }
    const mid = it.id || it.master_item_id || "";
    const uom = await uomFieldsForMaster(it);
    setLine(i, {
      master_item_id: mid,
      accurate_item_id: it.accurate_item_id || "",
      item_name: it.item_name || "",
      item_code: it.item_code || "",
      unit: uom.unit,
      uom_options: uom.uom_options,
      // swapping item clears PR fulfillment link
      request_line_id: "",
      remaining_hint: null,
    });
  };

  const save = async () => {
    if (busy || !canCreate) return;
    if (!supplier.trim()) {
      setErr("Supplier wajib diisi.");
      return;
    }
    if (lines.some((l) => Number(l.ordered_qty) > 0 && !l.master_item_id)) {
      setErr("Setiap baris harus memilih item dari Master Item.");
      return;
    }
    setBusy(true); setErr(""); setOk("");
    try {
      const payloadLines = lines
        .filter((l) => Number(l.ordered_qty) > 0)
        .map((l) => ({
          request_line_id: mode === "from_request" ? (l.request_line_id || undefined) : undefined,
          master_item_id: l.master_item_id,
          accurate_item_id: l.accurate_item_id,
          item_code: l.item_code,
          item_name: l.item_name,
          unit: normalizeUom(l.unit),
          ordered_qty: l.ordered_qty,
          unit_price: l.unit_price,
        }));
      const res = await api.createPurchaseOrder({
        request_id: mode === "from_request" ? requestId : undefined,
        supplier,
        order_date: orderDate,
        lines: payloadLines,
        idempotency_key: newIdempotencyKey(),
      });
      setDetail(res.order);
      setView("detail");
      setAdvances([]);
      setAdvanceSummary(null);
      setOk(`Pesanan Pembelian ${res.order?.po_number || ""} berhasil disimpan.`);
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setBusy(false); }
  };

  const loadAdvances = useCallback(async (poId) => {
    if (!poId) return;
    const res = await api.listPurchaseSupplierAdvances({ purchase_order_id: poId });
    setAdvances(res.advances || []);
    setAdvanceSummary(res.summary || null);
  }, []);

  const openDetail = async (id) => {
    try {
      const res = await api.getPurchaseOrder(id);
      setDetail(res.order);
      setView("detail");
      await loadAdvances(res.order?.id || id);
    } catch (ex) { setErr(errDetail(ex)); }
  };

  const createAdvance = async () => {
    if (advanceBusy || !detail?.id || !canCreate) return;
    const amount = Number(advanceAmount);
    if (!(amount > 0)) {
      setErr("Nilai uang muka harus lebih besar dari 0.");
      return;
    }
    setAdvanceBusy(true);
    setErr("");
    try {
      await api.createPurchaseSupplierAdvance({
        purchase_order_id: detail.id,
        amount,
        payment_date: advanceDate,
        idempotency_key: newIdempotencyKey(),
      });
      setAdvanceAmount("");
      setOk("Uang muka tercatat. Ajukan lalu konfirmasi dibayar agar saldo tersedia.");
      await loadAdvances(detail.id);
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setAdvanceBusy(false); }
  };

  const advanceAction = async (row, action) => {
    if (advanceBusy) return;
    setAdvanceBusy(true);
    setErr("");
    try {
      if (action === "submit") await api.submitPurchaseSupplierAdvance(row.id);
      if (action === "confirm") await api.confirmPurchaseSupplierAdvance(row.id, { payment_date: row.payment_date });
      if (action === "cancel") await api.cancelPurchaseSupplierAdvance(row.id);
      await loadAdvances(detail.id);
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setAdvanceBusy(false); }
  };

  const openCancel = (row) => {
    setCancelErr("");
    setCancelTarget(row);
  };

  const confirmCancel = async () => {
    if (!cancelTarget || cancelBusy) return;
    setCancelBusy(true);
    setCancelErr("");
    try {
      await api.cancelPurchaseOrder(cancelTarget.id);
      setOk(`Pesanan ${cancelTarget.po_number || ""} dihapus dari daftar aktif.`);
      setCancelTarget(null);
      if (view === "detail" && detail?.id === cancelTarget.id) {
        setView("list");
        setDetail(null);
      }
      await load();
    } catch (ex) {
      setCancelErr(errDetail(ex));
    } finally {
      setCancelBusy(false);
    }
  };

  const closeBranchModal = () => {
    if (branchBusy) return;
    setBranchModalOpen(false);
    setBranchOptions([]);
    setBranchOptionsErr("");
    setBranchIdPick("");
    setBranchReason("");
    setBranchConfirmReady(false);
    setBranchErr("");
    branchSubmitLockRef.current = false;
  };

  const openBranchComplete = async () => {
    if (!canShowLegacyPoBranchComplete(detail, canEditBranch) || branchBusy) return;
    setBranchModalOpen(true);
    setBranchIdPick("");
    setBranchReason("");
    setBranchConfirmReady(false);
    setBranchErr("");
    setBranchOptionsErr("");
    setBranchOptions([]);
    setBranchOptionsLoading(true);
    try {
      const res = await api.branches();
      setBranchOptions(filterActiveBranchesForPoComplete(res?.items || []));
    } catch (ex) {
      const status = ex?.response?.status;
      setBranchOptionsErr(
        Number(status) === 403
          ? "Anda tidak memiliki akses daftar cabang."
          : errDetail(ex),
      );
    } finally {
      setBranchOptionsLoading(false);
    }
  };

  const onBranchFieldChange = (patch) => {
    if (patch.branchIdPick != null) setBranchIdPick(patch.branchIdPick);
    if (patch.branchReason != null) setBranchReason(patch.branchReason);
    setBranchConfirmReady(false);
    setBranchErr("");
  };

  const submitBranchComplete = async () => {
    if (branchBusy || branchSubmitLockRef.current || !detail?.id) return;
    const pick = String(branchIdPick || "").trim();
    const reason = String(branchReason || "").trim();
    if (!pick) {
      setBranchErr("Pilih cabang terlebih dahulu.");
      return;
    }
    if (reason.length < 3) {
      setBranchErr("Alasan perubahan wajib diisi (minimal 3 karakter).");
      return;
    }
    if (!branchConfirmReady) {
      setBranchConfirmReady(true);
      setBranchErr("");
      return;
    }
    branchSubmitLockRef.current = true;
    setBranchBusy(true);
    setBranchErr("");
    setErr("");
    try {
      const res = await api.setPurchaseOrderBranch(detail.id, {
        branch_id: pick,
        reason,
      });
      const order = res?.order || null;
      if (order) setDetail(order);
      else {
        const refreshed = await api.getPurchaseOrder(detail.id);
        setDetail(refreshed.order || refreshed);
      }
      setOk(res?.message || "Cabang Pembelian berhasil dilengkapi");
      setBranchModalOpen(false);
      setBranchOptions([]);
      setBranchIdPick("");
      setBranchReason("");
      setBranchConfirmReady(false);
      await load();
    } catch (ex) {
      const status = Number(ex?.response?.status);
      setBranchErr(errDetail(ex));
      if (status === 400 || status === 409) {
        try {
          const refreshed = await api.getPurchaseOrder(detail.id);
          const order = refreshed.order || refreshed;
          if (order) setDetail(order);
          if (String(order?.branch_id || "").trim()) {
            setBranchModalOpen(false);
            setOk("Cabang Pembelian sudah terisi. Tampilan diperbarui.");
          }
        } catch {
          /* keep modal error */
        }
      }
      if (status === 403) {
        setBranchErr(errDetail(ex) || "Anda tidak memiliki izin melengkapi cabang.");
      }
    } finally {
      setBranchBusy(false);
      branchSubmitLockRef.current = false;
    }
  };

  const openCreate = () => {
    setMode(queryRequestId ? "from_request" : "direct");
    setRequestId(queryRequestId || "");
    setSupplier("");
    setOrderDate(todayYmd());
    setLines([emptyPoLine()]);
    setView("create");
  };

  const detailLineCount = (detail?.lines || []).length;
  const recvProgress = detail?.receiving_progress || {};
  const invProgress = detail?.invoice_progress || {};
  const pageCount = Math.max(1, Math.ceil((rows.length || 0) / pageSize) || 1);
  const safePage = Math.min(page, pageCount);
  const pageRows = rows.slice((safePage - 1) * pageSize, safePage * pageSize);

  return (
    <div
      className={`prod-page purchase-page${view === "detail" ? " is-po-doc" : ""}`}
      data-testid="pesanan-pembelian-page"
    >
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="pesanan-pembelian-title">
            {view === "create" ? "Buat Pesanan Pembelian" : view === "detail" ? "Detail Pesanan Pembelian" : "Pesanan Pembelian"}
          </h1>
          <p className="subhead">
            {view === "create"
              ? "Pilih sumber pesanan dan tentukan barang yang akan dibeli."
              : view === "detail"
                ? "Ringkasan pesanan, progress penerimaan/faktur, uang muka, dan dokumen."
              : "Buat pesanan langsung atau berdasarkan permintaan pembelian."}
          </p>
        </div>
        {view === "list" && canCreate && (
          <button className="primary" data-testid="btn-create-po" onClick={openCreate}><Plus size={14} /> Buat Pesanan</button>
        )}
        {view !== "list" && <button className="ghost" onClick={() => { setView("list"); load(); }}>Kembali ke daftar</button>}
      </div>
      <PurchasingFlowNav />
      {err && (
        <div className="purchase-error-panel" role="alert">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" /><span>{err}</span>
          </div>
          {view === "list" && <button type="button" className="ghost sm" onClick={load}>Coba Lagi</button>}
        </div>
      )}
      {ok && <p className="purchase-inline-ok" data-testid="po-success">{ok}</p>}

      {view === "list" && (
        <>
          <div className="purchase-search-bar is-toolbar">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
              <Search size={14} />
              <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="po-search" placeholder="Cari no PO / supplier / permintaan" />
            </form>
            <select
              value={pageSize}
              onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
              data-testid="po-page-size"
              aria-label="Jumlah baris per halaman"
            >
              <option value={25}>25 / halaman</option>
              <option value={50}>50 / halaman</option>
              <option value={100}>100 / halaman</option>
            </select>
          </div>
          <div className="purchase-table-wrap">
            <table className="prod-table purchase-table purchase-table-po purchase-table-excel" data-testid="po-table">
              <colgroup>
                <col className="col-po" />
                <col className="col-supplier" />
                <col className="col-date" />
                <col className="col-money" />
                <col className="col-status-recv" />
                <col className="col-status-inv" />
              </colgroup>
              <thead>
                <tr>
                  <th>No PO</th>
                  <th>Supplier</th>
                  <th>Tanggal</th>
                  <th className="num">Total</th>
                  <th className="cell-status th-wrap">Status Penerimaan</th>
                  <th className="cell-status th-wrap">Status Faktur</th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr className="purchase-empty-row"><td colSpan={6} className="muted small">Memuat…</td></tr>}
                {!loading && rows.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={6} className="muted small">Belum ada pesanan pembelian.</td>
                  </tr>
                )}
                {!loading && pageRows.map((r) => (
                  <tr
                    key={r.id}
                    {...purchaseRowInteractionProps(() => openDetail(r.id), { testId: `po-row-${r.id}` })}
                  >
                    <td className="mono cell-clip">
                      {r.po_number}
                      {r.has_early_invoice && <span className="purchase-badge-early" data-testid="badge-early-invoice">Faktur mendahului barang</span>}
                    </td>
                    <td className="cell-clip" title={r.supplier}>{r.supplier}</td>
                    <td className="nowrap">{r.order_date}</td>
                    <td className="num">{formatRupiah(r.grand_total)}</td>
                    <td className="cell-status">
                      <span className={`purchase-status-badge ${purchaseStatusPillClass(r.receiving_status)}`}>
                        {shortReceivingStatus(r.receiving_status)}
                      </span>
                    </td>
                    <td className="cell-status">
                      <span className={`purchase-status-badge ${purchaseStatusPillClass(r.invoice_status)}`}>
                        {r.invoice_status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="purchase-pager" data-testid="po-pager">
            <span className="muted small" data-testid="po-pager-summary">
              {rows.length === 0
                ? "0 pesanan"
                : `Menampilkan ${(safePage - 1) * pageSize + 1}–${Math.min(safePage * pageSize, rows.length)} dari ${rows.length}`}
            </span>
            <div className="purchase-pager-nav">
              <button
                type="button"
                className="ghost sm"
                disabled={safePage <= 1 || loading}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                data-testid="po-pager-prev"
              >
                Sebelumnya
              </button>
              <span className="muted small" data-testid="po-pager-page">{safePage} / {pageCount}</span>
              <button
                type="button"
                className="ghost sm"
                disabled={safePage >= pageCount || loading}
                onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                data-testid="po-pager-next"
              >
                Berikutnya
              </button>
            </div>
          </div>
        </>
      )}

      {view === "create" && (
        <div className="card purchase-detail-card" data-testid="po-create-form">
          <div className="purchase-mode-toggle" role="tablist" aria-label="Mode pesanan">
            <button
              type="button"
              className={mode === "direct" ? "is-active" : ""}
              data-testid="po-mode-direct"
              onClick={() => setMode("direct")}
            >
              Pesanan Langsung
            </button>
            <button
              type="button"
              className={mode === "from_request" ? "is-active" : ""}
              data-testid="po-mode-from-request"
              onClick={() => setMode("from_request")}
            >
              Dari Permintaan
            </button>
          </div>

          {mode === "from_request" && (
            <FormField id="po-request" label="Permintaan" value={requestId}>
              <select value={requestId} onChange={(e) => setRequestId(e.target.value)} data-testid="request-selector">
                <option value="">Pilih permintaan (opsional prefill)</option>
                {eligible.map((r) => <option key={r.id} value={r.id}>{r.request_number} · sisa {fmtQty(r.total_remaining_order_qty)}</option>)}
              </select>
            </FormField>
          )}

          <FormProgress
            percentage={Math.round(([supplier, orderDate, lines.some((l) => l.master_item_id && Number(l.ordered_qty) > 0)].filter(Boolean).length / 3) * 100)}
            completed={[supplier, orderDate, lines.some((l) => l.master_item_id && Number(l.ordered_qty) > 0)].filter(Boolean).length}
            total={3}
          />
          <FormSection
            id="po-header"
            title="Informasi Pesanan"
            status={computeStatus([
              { value: supplier, isRequired: true },
              { value: orderDate, isRequired: true },
            ])}
          >
            <div className="purchase-form-grid">
              <FormField id="po-supplier-field" label="Supplier" isRequired value={supplier}>
                <input value={supplier} onChange={(e) => setSupplier(e.target.value)} data-testid="po-supplier" />
              </FormField>
              <FormField id="po-date-field" label="Tanggal" isRequired value={orderDate}>
                <input type="date" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} data-testid="po-date" />
              </FormField>
              <FormField id="po-branch-field" label="Cabang Pembelian" value={createBranchLabel}>
                <input
                  value={createBranchLabel}
                  readOnly
                  tabIndex={-1}
                  data-testid="po-create-branch"
                  aria-readonly="true"
                />
              </FormField>
            </div>
          </FormSection>
          <FormSection
            id="po-lines"
            title="Detail Item"
            status={lines.some((l) => l.master_item_id && Number(l.ordered_qty) > 0) ? "complete" : "incomplete"}
          >
            <div className="purchase-table-wrap">
              <table className="prod-table purchase-lines-table purchase-lines-with-price" data-testid="po-lines-table">
                <colgroup>
                  <col className="col-item-line" />
                  <col className="col-code-line" />
                  <col className="col-unit-line" />
                  <col className="col-qty-line" />
                  <col className="col-price-line" />
                  <col className="col-action-line" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Kode</th>
                    <th>Satuan</th>
                    <th className="num">Qty</th>
                    <th className="num">Harga</th>
                    <th>Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={l.key}>
                      <td>
                        <MasterItemSearchSelect
                          testId={`po-line-item-${i}`}
                          value={
                            l.master_item_id
                              ? {
                                  id: l.master_item_id,
                                  master_item_id: l.master_item_id,
                                  accurate_item_id: l.accurate_item_id,
                                  item_name: l.item_name,
                                  item_code: l.item_code,
                                  unit: l.unit,
                                }
                              : null
                          }
                          onChange={(item) => pickItem(i, item)}
                          placeholder="Cari Master Item…"
                        />
                        {l.request_line_id && l.remaining_hint != null && (
                          <span className="muted small">Sisa PR: {fmtQty(l.remaining_hint)}</span>
                        )}
                      </td>
                      <td className="mono">{l.item_code || "—"}</td>
                      <td className="unit-cell">
                        <TransactionUomSelect
                          value={l.unit}
                          options={l.uom_options}
                          testId={`po-line-unit-${i}`}
                          onChange={(unit) => setLine(i, { unit })}
                        />
                      </td>
                      <td className="qty-cell">
                        <input
                          type="number"
                          min="0"
                          value={l.ordered_qty}
                          onChange={(e) => setLine(i, { ordered_qty: e.target.value })}
                          data-testid="qty-input"
                        />
                      </td>
                      <td className="qty-cell">
                        <input
                          type="number"
                          min="0"
                          value={l.unit_price}
                          onChange={(e) => setLine(i, { unit_price: e.target.value })}
                          data-testid="unit-price-input"
                        />
                      </td>
                      <td className="action-cell">
                        <button type="button" className="ghost sm" onClick={() => removeLine(i)}>Hapus</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button className="ghost purchase-add-line" type="button" onClick={addLine} data-testid="add-po-line">Tambah baris</button>
          </FormSection>
          <div className="purchase-form-actions">
            <button className="ghost" type="button" onClick={() => setView("list")}>Batal</button>
            <button className="primary" disabled={busy} onClick={save} data-testid="save-po">{busy ? "Menyimpan…" : "Simpan Pesanan"}</button>
          </div>
        </div>
      )}

      {view === "detail" && detail && (
        <div className="fpb-final" data-testid="po-detail">
          <section className="fpb-meta" data-testid="po-detail-header">
            <div className="fpb-meta-fields">
              <div className="fpb-meta-item">
                <span className="fpb-k"><FileText size={12} aria-hidden /> No PO</span>
                <span className="fpb-v mono" data-testid="detail-po-number">
                  {detail.po_number}
                  {detail.has_early_invoice && <span className="purchase-badge-early">Faktur mendahului barang</span>}
                </span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><FileText size={12} aria-hidden /> Permintaan</span>
                <span className="fpb-v mono">{detail.request_number || "—"}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Building2 size={12} aria-hidden /> Pemasok</span>
                <span className="fpb-v">{detail.supplier || "—"}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><MapPin size={12} aria-hidden /> Cabang</span>
                <span className="fpb-v" data-testid="po-detail-branch">{purchaseBranchLabel(detail)}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><CalendarDays size={12} aria-hidden /> Tanggal</span>
                <span className="fpb-v">{detail.order_date || "—"}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Wallet size={12} aria-hidden /> Status Penerimaan</span>
                <span className={`purchase-status-badge ${purchaseStatusPillClass(detail.receiving_status)}`}>
                  {shortReceivingStatus(detail.receiving_status)}
                </span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Wallet size={12} aria-hidden /> Status Faktur</span>
                <span className={`purchase-status-badge ${purchaseStatusPillClass(detail.invoice_status)}`}>
                  {detail.invoice_status}
                </span>
              </div>
            </div>
          </section>

          <div className="fpb-body">
            <section className={`fpb-lines${detailLineCount > 10 ? " is-scrollable" : ""}`} aria-label="Rincian Barang">
              <div className="fpb-lines-head">
                <h3>Rincian Barang <span className="fpb-lines-count">({detailLineCount} item)</span></h3>
              </div>
              <div className="fpb-table-scroll">
                <table className="fpb-lines-table" data-testid="po-detail-lines">
                  <thead>
                    <tr>
                      <th className="col-no">No</th>
                      <th className="col-name">Nama Barang</th>
                      <th className="col-code">Kode</th>
                      <th className="col-qty num">Qty</th>
                      <th className="col-unit">Satuan</th>
                      <th className="col-price num">Harga (Rp)</th>
                      <th className="col-total num">Subtotal (Rp)</th>
                      <th className="col-recv num">Diterima</th>
                      <th className="col-inv num">Difaktur</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(detail.lines || []).length === 0 && (
                      <tr><td colSpan={9} className="muted">Tidak ada baris item.</td></tr>
                    )}
                    {(detail.lines || []).map((l, idx) => (
                      <tr key={l.line_id}>
                        <td className="col-no num">{idx + 1}</td>
                        <td className="col-name" title={l.item_name}>{l.item_name || "—"}</td>
                        <td className="col-code mono">{l.item_code || "—"}</td>
                        <td className="col-qty num">{fmtQty(l.ordered_qty)}</td>
                        <td className="col-unit">{l.unit || "pcs"}</td>
                        <td className="col-price num">{formatRupiah(l.unit_price)}</td>
                        <td className="col-total num">{formatRupiah(l.line_subtotal)}</td>
                        <td className="col-recv num">{fmtQty(l.cumulative_received_qty)} / sisa {fmtQty(l.remaining_receivable_qty)}</td>
                        <td className="col-inv num">{fmtQty(l.cumulative_invoiced_qty)} / sisa {fmtQty(l.remaining_invoice_qty)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <aside className="fpb-side">
              <section className="fpb-side-card" data-testid="po-detail-summary">
                <h3>Ringkasan Nilai</h3>
                <dl>
                  <div><dt>Nilai Pesanan</dt><dd data-testid="po-summary-grand">{formatRupiah(detail.grand_total)}</dd></div>
                  <div>
                    <dt>Qty Diterima</dt>
                    <dd data-testid="po-summary-received">
                      {fmtQty(recvProgress.received_qty)} / {fmtQty(recvProgress.ordered_qty)}
                    </dd>
                  </div>
                  <div>
                    <dt>Qty Difaktur</dt>
                    <dd data-testid="po-summary-invoiced">
                      {fmtQty(invProgress.invoiced_qty)} / {fmtQty(invProgress.ordered_qty)}
                    </dd>
                  </div>
                  <div className="is-total"><dt>Total</dt><dd>{formatRupiah(detail.grand_total)}</dd></div>
                </dl>
              </section>
              <section className="fpb-side-card" data-testid="po-detail-info">
                <h3>Info Lainnya</h3>
                <div className="fpb-side-fields">
                  <div className="fpb-kv"><span>No. Referensi</span><b className="mono">{detail.request_number || detail.po_number || "—"}</b></div>
                  <div className="fpb-kv"><span>Mata Uang</span><b>IDR</b></div>
                  {detail.notes ? (
                    <div className="fpb-notes">
                      <span>Keterangan</span>
                      <p>{detail.notes}</p>
                    </div>
                  ) : null}
                </div>
              </section>
            </aside>
          </div>

          <section className="fpb-lower is-po-lower" aria-label="Uang muka dan dokumen">
            <div className="fpb-lower-card" data-testid="po-uang-muka">
              <h4>Uang Muka</h4>
              <div className="fpb-kv"><span>Nilai Uang Muka</span><b data-testid="uang-muka-paid">{formatRupiah(advanceSummary?.total_paid_amount || 0)}</b></div>
              <div className="fpb-kv"><span>Tersedia / Belum Dialokasikan</span><b data-testid="uang-muka-available">{formatRupiah(advanceSummary?.available_amount || 0)}</b></div>
              {(advances || []).map((row) => (
                <div key={row.id} className="fpb-advance-row" data-testid="uang-muka-row">
                  <span>{formatRupiah(row.original_amount)}</span>
                  <span data-testid="uang-muka-status">{row.status_label}</span>
                  <span>{row.payment_date || "—"}</span>
                  <span data-testid="uang-muka-row-available">{formatRupiah(row.available_amount || 0)}</span>
                  <span>
                    {row.can_submit && <button type="button" className="ghost sm" onClick={() => advanceAction(row, "submit")}>Ajukan</button>}
                    {row.can_confirm_paid && <button type="button" className="ghost sm" onClick={() => advanceAction(row, "confirm")}>Konfirmasi</button>}
                    {row.can_cancel && <button type="button" className="ghost sm" onClick={() => advanceAction(row, "cancel")}>Batalkan</button>}
                  </span>
                </div>
              ))}
              {canCreate && (
                <div className="fpb-advance-create">
                  <input
                    type="number"
                    min="1"
                    value={advanceAmount}
                    onChange={(e) => setAdvanceAmount(e.target.value)}
                    placeholder="Nilai uang muka"
                    data-testid="uang-muka-amount"
                  />
                  <input type="date" value={advanceDate} onChange={(e) => setAdvanceDate(e.target.value)} data-testid="uang-muka-date" />
                  <button type="button" className="primary" disabled={advanceBusy} onClick={createAdvance} data-testid="uang-muka-create">
                    Buat Uang Muka
                  </button>
                </div>
              )}
            </div>
            <div className="fpb-lower-card" data-testid="detail-panel-docs">
              <PurchaseDocuments
                entityType="purchase_order"
                entityId={detail.id}
                canEdit={canCreate}
                hint="Nota / penawaran supplier."
                testId="po-documents"
              />
            </div>
          </section>

          <section className="fpb-lower is-po-activity" aria-label="Riwayat aktivitas">
            <div className="fpb-lower-card" data-testid="detail-panel-history">
              <PurchaseActivityHistory doc={detail} testId="po-activity" />
            </div>
          </section>

          <footer className="fpb-actions-bar" data-testid="po-detail-actions">
            <div className="fpb-actions-left">
              <button type="button" className="ghost" onClick={() => { setView("list"); load(); }}>Kembali</button>
            </div>
            <div className="fpb-actions-right">
              {canShowLegacyPoBranchComplete(detail, canEditBranch) && (
                <button
                  type="button"
                  className="primary"
                  data-testid="btn-lengkapi-cabang-po"
                  disabled={branchBusy}
                  onClick={openBranchComplete}
                >
                  Lengkapi Cabang
                </button>
              )}
              {canCreate && (
                <button
                  type="button"
                  className="ghost danger"
                  data-testid="btn-hapus-po-detail"
                  disabled={detail.can_cancel === false}
                  title={detail.can_cancel === false ? (detail.cancel_block_reason || "") : undefined}
                  onClick={() => openCancel(detail)}
                >
                  Hapus
                </button>
              )}
              {Number(detail.invoice_progress?.remaining_qty) > 0 && hasPerm("purchase_invoices.create") && (
                <Link className="primary" data-testid="btn-buat-faktur" to={`/pembelian/faktur-pembelian?purchase_order_id=${detail.id}&action=create`}>Buat Faktur</Link>
              )}
              {Number(detail.receiving_progress?.remaining_qty) > 0 && (
                <Link className="ghost" to={`/pembelian/penerimaan-barang?purchase_order_id=${detail.id}&action=receive`}>Terima Barang</Link>
              )}
            </div>
          </footer>
        </div>
      )}

      {branchModalOpen && (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          data-testid="po-lengkapi-cabang-modal"
          onClick={branchBusy ? undefined : closeBranchModal}
        >
          <div className="modal-inner purchase-branch-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <h3><MapPin size={18} aria-hidden /> Lengkapi Cabang Pembelian</h3>
                <p className="purchase-modal-helper">
                  Tetapkan cabang operasional untuk PO lama. Cabang tidak dapat diubah lagi setelah disimpan.
                </p>
              </div>
              <button type="button" className="ghost sm" onClick={closeBranchModal} disabled={branchBusy} aria-label="Tutup">×</button>
            </div>
            <div className="purchase-modal-body">
              <div className="purchase-branch-warn" data-testid="po-branch-legacy-warn">
                <AlertTriangle size={16} aria-hidden />
                <span>Khusus PO legacy tanpa cabang. Pastikan cabang sesuai lokasi penerimaan barang.</span>
              </div>
              {branchOptionsLoading ? (
                <p className="muted small" data-testid="po-branch-options-loading">Memuat cabang…</p>
              ) : null}
              {branchOptionsErr ? (
                <p className="error" data-testid="po-branch-options-error">{branchOptionsErr}</p>
              ) : null}
              {!branchOptionsLoading && !branchOptionsErr && branchOptions.length === 0 ? (
                <p className="muted small" data-testid="po-branch-options-empty">Tidak ada cabang aktif yang dapat dipilih.</p>
              ) : null}
              <div className="purchase-branch-stack">
                <label htmlFor="po-complete-branch-select">
                  Cabang
                  <select
                    id="po-complete-branch-select"
                    data-testid="po-complete-branch-select"
                    value={branchIdPick}
                    disabled={branchBusy || branchOptionsLoading || Boolean(branchOptionsErr)}
                    onChange={(e) => onBranchFieldChange({ branchIdPick: e.target.value })}
                  >
                    <option value="">Pilih cabang…</option>
                    {branchOptions.map((b) => (
                      <option key={b.id} value={b.id}>
                        {purchaseBranchLabel(b)}
                      </option>
                    ))}
                  </select>
                </label>
                <label htmlFor="po-complete-branch-reason">
                  Alasan
                  <textarea
                    id="po-complete-branch-reason"
                    data-testid="po-complete-branch-reason"
                    value={branchReason}
                    disabled={branchBusy}
                    rows={3}
                    placeholder="Minimal 3 karakter"
                    onChange={(e) => onBranchFieldChange({ branchReason: e.target.value })}
                  />
                </label>
              </div>
              {branchConfirmReady ? (
                <p className="muted small" data-testid="po-branch-confirm-summary">
                  Konfirmasi: cabang{" "}
                  <strong>{purchaseBranchLabel(branchOptions.find((b) => b.id === branchIdPick) || { id: branchIdPick })}</strong>
                  {" "}akan disimpan. Tindakan ini tidak dapat diulang.
                </p>
              ) : null}
              {branchErr ? (
                <p className="error" data-testid="po-branch-complete-error">{branchErr}</p>
              ) : null}
            </div>
            <div className="purchase-modal-actions">
              <button type="button" className="ghost" onClick={closeBranchModal} disabled={branchBusy} data-testid="po-branch-complete-batal">
                Batal
              </button>
              <button
                type="button"
                className="primary"
                data-testid="po-branch-complete-save"
                disabled={
                  branchBusy
                  || branchOptionsLoading
                  || Boolean(branchOptionsErr)
                  || !String(branchIdPick || "").trim()
                  || String(branchReason || "").trim().length < 3
                }
                onClick={submitBranchComplete}
              >
                {branchBusy
                  ? "Menyimpan…"
                  : (branchConfirmReady ? "Simpan dan Lanjutkan" : "Lanjutkan")}
              </button>
            </div>
          </div>
        </div>
      )}

      <PurchaseCancelModal
        open={Boolean(cancelTarget)}
        title="Hapus Pesanan Pembelian?"
        body="Dokumen akan dikeluarkan dari daftar aktif. Riwayat pembatalan tetap tersimpan."
        busy={cancelBusy}
        error={cancelErr}
        onCancel={() => !cancelBusy && setCancelTarget(null)}
        onConfirm={confirmCancel}
      />
    </div>
  );
}
