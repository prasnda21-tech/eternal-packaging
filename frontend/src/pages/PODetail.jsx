import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
import { AlertCircle, AlertTriangle, ArrowLeft, CalendarClock, Check, ChevronDown, Copy, Eye, FileCheck2, FileText, List, MapPin, MessageSquare, RefreshCw, RotateCcw, ScanLine, ShieldOff, Trash2, Truck, X, XCircle, Zap } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { STAGES, canAdvance, formatDate, formatDisplayDate, formatDisplayDateTime } from "../lib/constants";
import { getWorkflowReady, resolveOperationalStage } from "../lib/stageState";
import { isApprovalDesignComplete, isCustomerDesignApproved } from "../lib/designEvidence";
import Timeline from "../components/Timeline";
import OperatorSplitPanel from "../components/OperatorSplitPanel";
import PackingPanel from "../components/PackingPanel";
import SuratJalanPanel from "../components/SuratJalanPanel";
import AttachmentsPanel from "../components/AttachmentsPanel";
import DeliveryProofPanel from "../components/DeliveryProofPanel";
import SuratJalanViewButton from "../components/SuratJalanViewButton";
import CustomerApprovalPanel from "../components/CustomerApprovalPanel";
import HelperPrepPanel from "../components/HelperPrepPanel";
import ProductionRoutePanel, { LineRouteEditor } from "../components/ProductionRoutePanel";
import LoadingPanel from "../components/LoadingPanel";
import OutletLocationModal from "../components/OutletLocationModal";
import EditPoInfoModal from "../components/EditPoInfoModal";
import StageBar from "../components/StageBar";
import { MetricCell, MetricRow, QtyOpsHead, statusClassFor } from "../components/qtyUi";
import { terkirimQtySummary } from "../lib/deliveryHistory";
import { aksiPoSalesDeliveryState } from "../lib/aksiPoSalesDelivery";
import useAutoRefresh from "../lib/useAutoRefresh";
import {
  accordionIndexForFocus,
  clampWorkflowStageIndex,
  deskPanelOwnsStage,
  parsePoDetailStageParam,
  poDetailStagePanelTitle,
  PO_DETAIL_VISUAL_STEPS,
  shouldShowPoActiveStagePanel,
  syncPoDetailStageQuery,
  visualCanonicalIndex,
  visualStepForInternalIndex,
} from "../lib/poDetailStageNav";
import { salesOrderLineColor } from "../lib/salesOrderLineColor";
import { enrichWorkspaceTab } from "../lib/workspaceTabEnrichment";
import AppendQtyEntries from "../components/AppendQtyEntries";
import ConfirmActionModal from "../components/ConfirmActionModal";
import { newActionId } from "../lib/workflowEntries";
import { MAX_PAYMENT_PROOFS, canAddPaymentProof, paymentProofCount } from "../lib/paymentProof";
import InAppDocumentViewer from "../components/InAppDocumentViewer";
import { canViewSalesReceiptPaymentProof, orderReceiptProofStamp, paymentEvidenceBadge, receiptHasPaymentProof, receiptProofStatusLabel } from "../lib/salesReceiptPaymentProof";
import { dataUrlToBlob, paymentProofIsImage, paymentProofIsPdf, paymentProofSource } from "../lib/paymentProofOpen";
import { canManageUrgent as canManageUrgentPerm } from "../lib/poUrgent";
import { formatApiError } from "../lib/errors";
import { schedulePoHref } from "../lib/workPanel";
import { resolvePoBackTarget, storageUserKey } from "../lib/poListReturn";
import { readPoDetailRetention, writePoDetailRetention } from "../lib/poDetailRetention";
import LineProductReplaceControl from "../components/LineProductReplaceControl";
import PoDetailFocusPanel from "../components/poDetail/PoDetailFocusPanel";
import PoStageSection from "../components/poDetail/PoStageSection";
import PoSummaryDrawer from "../components/poDetail/PoSummaryDrawer";
import PoStagePickerSheet from "../components/poDetail/PoStagePickerSheet";
import { PoStageAccordionProvider, usePoStageAccordion } from "../components/poDetail/PoStageAccordionContext";

function PengirimanDeliveryProofPanel({ order, onUpdated }) {
  const { open } = usePoStageAccordion(14);
  return <DeliveryProofPanel order={order} onUpdated={onUpdated} isOpen={open} />;
}

export default function PODetail() {
  const { id } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { user, hasPerm } = useAuth();
  const retainedOrder = readPoDetailRetention({ user, orderId: id, hasPerm });
  const [order, setOrder] = useState(retainedOrder);
  const [loading, setLoading] = useState(!retainedOrder);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [toast, setToast] = useState("");

  // Delete PO modal state (Req 5)
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteImpact, setDeleteImpact] = useState(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  // Payment upload state (Req wiring)
  const [payUploading, setPayUploading] = useState(false);
  const [payUploadErr, setPayUploadErr] = useState("");
  const [salesReceipts, setSalesReceipts] = useState([]);
  const [salesReceiptsLoading, setSalesReceiptsLoading] = useState(false);
  const [salesReceiptsErr, setSalesReceiptsErr] = useState("");
  const [proofViewerReceipt, setProofViewerReceipt] = useState(null);
  const [receiptRefreshTick, setReceiptRefreshTick] = useState(0);
  const [screenerDraft, setScreenerDraft] = useState("");
  const [screenerNotes, setScreenerNotes] = useState("");
  const [actionConfirm, setActionConfirm] = useState(null);
  const [trackingCopyMsg, setTrackingCopyMsg] = useState("");
  const [trackingBusy, setTrackingBusy] = useState(false);
  const [locRow, setLocRow] = useState(null);
  const [editInfoOpen, setEditInfoOpen] = useState(false);
  const [dangerActionsOpen, setDangerActionsOpen] = useState(false);
  const [selectedWorkflowStage, setSelectedWorkflowStage] = useState(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [stageSheetOpen, setStageSheetOpen] = useState(false);
  const selectedForOrderRef = useRef(null);

  const commitOrder = (nextOrder) => {
    setOrder(nextOrder);
    if (nextOrder?.id) {
      writePoDetailRetention({
        user,
        orderId: nextOrder.id,
        hasPerm,
        order: nextOrder,
      });
    }
  };

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2500);
  };

  // INITIAL REGISTRATION (existence only): route /po/:id must create
  // po-detail:<order_id> even when location.state is null and order is unloaded.
  // Uses the same openOrActivate bridge — does not change tab identity.
  useLayoutEffect(() => {
    if (!id) return;
    const st = location.state && typeof location.state === "object" ? location.state : {};
    enrichWorkspaceTab(`/po/${id}`, st);
  }, [id, location.state]);

  useEffect(() => {
    let cancelled = false;
    const retained = readPoDetailRetention({ user, orderId: id, hasPerm });
    if (retained) {
      commitOrder(retained);
      setLoading(false);
    } else {
      commitOrder(null);
      setLoading(true);
    }
    setErr("");
    api.getOrder(id).then((d) => {
      if (!cancelled && d?.order) commitOrder(d.order);
    }).catch((e) => {
      if (cancelled) return;
      // Workflow 06-12 bugfix: never setErr(object) — React akan crash render
      // dan menyebabkan halaman blank. Backend bisa return detail berupa object.
      const d = e.response?.data?.detail;
      if (typeof d === "string") setErr(d);
      else if (d && typeof d === "object") setErr(d.message || JSON.stringify(d));
      else setErr("Gagal memuat PO");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
    // hasPerm is stable for the current AuthProvider principal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, user?.id, user?.email, user?.sub]);

  // Enrich the EXISTING dynamic PO tab label in place after order load.
  // Do NOT navigate/replace — that previously raced the workspace tab lifecycle
  // (and could replay a stale order.id across /po/:id changes), making the PO
  // history tab disappear or fight route registration.
  useEffect(() => {
    if (!order?.id || !id || String(order.id) !== String(id)) return;
    const customer = String(order.customer || "").trim();
    const poNumber = String(order.po_number || "").trim();
    if (!customer && !poNumber) return;
    enrichWorkspaceTab(`/po/${order.id}`, {
      ...(poNumber ? { po_number: poNumber } : {}),
      ...(customer ? { customer } : {}),
    });
  }, [id, order?.id, order?.customer, order?.po_number]);

  useEffect(() => {
    setScreenerDraft("");
    setScreenerNotes(order?.screener_info?.notes || "");
  }, [order?.id, order?.screener_info?.updated_at]);

  // Display-only stage workspace. Refresh must not reset the viewed stage.
  useEffect(() => {
    if (!order?.id) return;
    const switched = selectedForOrderRef.current !== order.id;
    if (switched) selectedForOrderRef.current = order.id;
    setSelectedWorkflowStage((prev) => {
      if (!switched && prev != null) return prev;
      const search = location.search || (typeof window !== "undefined" ? window.location.search : "");
      const fromQuery = parsePoDetailStageParam(search);
      if (fromQuery != null) return fromQuery;
      return clampWorkflowStageIndex(resolveOperationalStage(order, order.current_stage), 0);
    });
  }, [order, location.search]);

  useEffect(() => {
    const onPop = () => {
      const fromQuery = parsePoDetailStageParam(typeof window !== "undefined" ? window.location.search : "");
      if (fromQuery != null) setSelectedWorkflowStage(fromQuery);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Canonicalize ?stage= on first load / refresh without adding history.
  useEffect(() => {
    if (selectedWorkflowStage == null) return;
    if (typeof window === "undefined") return;
    const current = parsePoDetailStageParam(window.location.search);
    if (current !== selectedWorkflowStage) {
      syncPoDetailStageQuery(selectedWorkflowStage, { push: false });
    }
  }, [order?.id, selectedWorkflowStage]);

  useEffect(() => {
    const bump = () => setReceiptRefreshTick((n) => n + 1);
    window.addEventListener("focus", bump);
    window.addEventListener("eternal:sales-receipt-saved", bump);
    return () => {
      window.removeEventListener("focus", bump);
      window.removeEventListener("eternal:sales-receipt-saved", bump);
    };
  }, []);

  // Start with route id (parallel to getOrder) — do not wait for order payload.
  // Payment stamps and a saved penerimaan refresh this list without a manual reload.
  useEffect(() => {
    if (!id) {
      setSalesReceipts([]);
      return;
    }
    let cancelled = false;
    setSalesReceiptsLoading(true);
    setSalesReceiptsErr("");
    api.listSalesReceipts({ po_id: id })
      .then((res) => {
        if (!cancelled) setSalesReceipts(res.receipts || []);
      })
      .catch((e) => {
        if (cancelled) return;
        const d = e.response?.data?.detail;
        setSalesReceiptsErr(typeof d === "string" ? d : (d?.message || "Gagal memuat Penerimaan Penjualan"));
        setSalesReceipts([]);
      })
      .finally(() => {
        if (!cancelled) setSalesReceiptsLoading(false);
      });
    return () => { cancelled = true; };
  }, [id, order?.payment_fact?.updated_at, order?.payment_approved_at, orderReceiptProofStamp(order), receiptRefreshTick]);

  // Batch 2: auto refresh 30s untuk PODetail — hanya update server-backed state
  // (order), tidak menyentuh state form lokal panel-panel (Helper draft,
  // Deadline draft, Notes, Screener draft, signature dsb). Panel-panel memakai
  // guard `useEffect([order.id, x.updated_at])` sehingga draft baru direset
  // kalau server-side timestamp berubah — draft belum-di-save tetap aman.
  const { lastUpdated: poLastUpdated, isPolling: poIsPolling, refreshNow: refreshPO } = useAutoRefresh(
    () => api.getOrder(id),
    { intervalMs: 30_000, enabled: !!order, onData: (d) => { if (d?.order) commitOrder(d.order); } }
  );

  const loadReceiptProof = useCallback(({ download = false, signal } = {}) => {
    const receiptId = proofViewerReceipt?.id;
    if (!receiptId) {
      const error = new Error("missing");
      error.status = 404;
      return Promise.reject(error);
    }
    return api.downloadSalesReceiptPaymentProof(receiptId, download, { signal });
  }, [proofViewerReceipt?.id]);

  if (!hasPerm("po.view")) {
    const href = schedulePoHref(id, hasPerm);
    if (href.includes("/kerja")) return <Navigate to={href} replace />;
    return <Navigate to="/" replace />;
  }

  if (loading) return <div className="loading" data-testid="po-loading">Memuat detail PO...</div>;
  if (err) return <div className="error-page" data-testid="po-error" style={{whiteSpace:"pre-line"}}>{String(err)}</div>;
  if (!order) return null;

  const role = user?.role;
  const isCrm = role === "crm";
  const canAdvanceNow = !isCrm && canAdvance(role, order.current_stage);
  const ready = getWorkflowReady(order);
  const screenerLocked = !ready.screener;
  const screenSaved = order.screener_info?.screen_qty;
  const screenerFilled = screenSaved !== null && screenSaved !== undefined && String(screenSaved).trim() !== "";

  const run = async (fn, msg) => {
    setBusy(true);
    setErr("");
    try {
      const res = await fn();
      commitOrder(res.order);
      if (msg) showToast(msg);
    } catch (e) {
      const d = e.response?.data?.detail;
      // Workflow 06-12 bugfix: backend kirim prereq_reasons untuk transition gate.
      if (d && typeof d === "object" && Array.isArray(d.prereq_reasons)) {
        setErr(`${d.message}\n• ${d.prereq_reasons.join("\n• ")}`);
      } else if (typeof d === "string") {
        setErr(d);
      } else if (d && d.message) {
        setErr(d.message);
      } else {
        setErr("Aksi gagal");
      }
    } finally {
      setBusy(false);
    }
  };

  const approveDesign = () => run(() => api.approveDesign(order.id, true, note), "Design di-approve");
  const rejectDesign = () => run(() => api.approveDesign(order.id, false, note), "Approval design dibatalkan");
  const approvePayment = () => run(() => api.approvePayment(order.id, true, note), "Payment di-approve");
  const rejectPayment = () => run(() => api.approvePayment(order.id, false, note), "Approval payment dibatalkan");
  const approveSample = () => {
    setActionConfirm({
      kind: "sample-approve",
      stage: "Konfirmasi Sample",
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };
  const rejectSample = () => run(() => api.approveSample(order.id, false, note), "Approval sampel dibatalkan");

  const uploadPaymentProof = async (file) => {
    setPayUploadErr("");
    if (!file) return;
    const allowed = ["image/jpeg", "image/jpg", "image/png", "application/pdf"];
    if (!allowed.includes(file.type)) {
      setPayUploadErr("Format file tidak didukung. Gunakan JPG, PNG, atau PDF.");
      return;
    }
    if (file.size > 4 * 1024 * 1024) {
      setPayUploadErr("Ukuran file terlalu besar (maks 4MB).");
      return;
    }
    setPayUploading(true);
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(r.result);
        r.onerror = () => reject(new Error("Gagal membaca file"));
        r.readAsDataURL(file);
      });
      const result = await api.addAttachment(order.id, { kind: "payment", name: file.name, data_url: dataUrl });
      if (result?.order) commitOrder(result.order);
      else {
        const d = await api.getOrder(order.id);
        commitOrder(d.order);
      }
      setToast(`Bukti pembayaran "${file.name}" diupload`);
      setTimeout(() => setToast(""), 2400);
    } catch (e) {
      const status = e.response?.status;
      const detail = e.response?.data?.detail;
      if (status === 403) setPayUploadErr("Anda tidak memiliki izin untuk mengupload bukti pembayaran.");
      else setPayUploadErr(detail || "Upload bukti pembayaran gagal. Silakan coba kembali.");
    } finally {
      setPayUploading(false);
    }
  };
  const toggleUrgent = () => run(() => api.setUrgent(order.id, order.priority !== "urgent"), "Prioritas diperbarui");
  const toggleBufferStock = () => run(
    () => api.setBufferStock(order.id, !order.buffer_stock),
    order.buffer_stock ? "Buffer Stock dibatalkan" : "Ditandai Buffer Stock"
  );
  const saveNote = async () => {
    if (!note.trim()) return;
    await run(() => api.addNote(order.id, note.trim()), "Catatan disimpan");
    setNote("");
  };

  const executeActionConfirm = async () => {
    if (!actionConfirm || busy) return;
    setBusy(true);
    try {
      if (actionConfirm.kind === "screener") {
        const res = await api.updateScreenerInfo(order.id, {
          qty: actionConfirm.qty,
          notes: actionConfirm.notes,
          action_id: actionConfirm.actionId,
        });
        commitOrder(res.order);
        setScreenerDraft("");
        showToast("Info Screener disimpan");
      } else if (actionConfirm.kind === "sample-approve") {
        const res = await api.approveSample(order.id, true, note);
        commitOrder(res.order);
        showToast("Sampel di-approve");
      }
      setActionConfirm(null);
    } catch (e) {
      const msg = formatApiError(e, "Gagal mencatat aktivitas Screener");
      setActionConfirm((prev) => prev ? { ...prev, error: msg } : prev);
    } finally {
      setBusy(false);
    }
  };
  const canApproveDesign = hasPerm("design.approve");
  const canApprovePayment = hasPerm("payment.approve");
  const canApproveSample = hasPerm("operator.sample_approve") || hasPerm("schedule.edit");
  const canUploadPayment = hasPerm("payment.upload");
  const canViewReceiptProof = canViewSalesReceiptPaymentProof(hasPerm);
  const canUploadDesign = hasPerm("design.upload");
  const canUploadSample = hasPerm("operator.sample_upload");
  const canManageUrgent = canManageUrgentPerm(hasPerm);
  const canManageBufferStock = hasPerm("buffer_stock.manage");
  const canEditSchedule = hasPerm("schedule.edit");
  const canMarkPacked = hasPerm("packing.update");
  const canMarkDelivered = hasPerm("delivery.update");
  const canManageSuratJalan = hasPerm("delivery_note.manage");
  const canViewSjFile = hasPerm("delivery_note.view_file");
  const canLoadingUpdate = hasPerm("loading.update") || hasPerm("loading.confirm_match") || hasPerm("loading.report_discrepancy");
  const canBypassDesign = hasPerm("design.bypass_approval");
  const canBypassPayment = hasPerm("payment.bypass_approval");
  const canRollback = hasPerm("workflow.rollback");
  const canCancel = hasPerm("po.cancel");
  const canDelete = hasPerm("po.delete");
  const canHelperPrep = hasPerm("helper.prepare");
  const canScreenerUpdate = hasPerm("screen.update");
  const canViewCustomerApproval = hasPerm("customer_approval.view");
  const canManageCustomerApproval = hasPerm("customer_approval.manage");
  const canCopyTrackingLink = hasPerm("po.view");
  const canEditRoute = hasPerm("production.route.edit");
  const canReplaceLineProduct = hasPerm("po.edit");
  const canEditPoInfo = hasPerm("po.edit");
  const canSetLocation = hasPerm("delivery.location.manage");
  const canCreateDelivery = hasPerm("sales.delivery.create") || hasPerm("sales_order.create") || hasPerm("po.edit") || user?.role === "admin";
  const canRepeatOrder = hasPerm("sales_order.create") || hasPerm("po.edit") || user?.role === "admin";
  const {
    showProcess: canProcessSalesDelivery,
    showAllQtyDone: salesDeliveryAllQtyDone,
  } = aksiPoSalesDeliveryState(order, { canCreateDelivery });
  // Canonical remain lives in order.sales_delivery_summary (via aksiPoSalesDeliveryState).
  const salesDeliveryTotalRemaining = (order.sales_delivery_summary || {}).total_remaining;
  const terkirimSummary = terkirimQtySummary(order);
  const actualStage = clampWorkflowStageIndex(resolveOperationalStage(order, order.current_stage), 0);
  const viewedStage = selectedWorkflowStage == null ? actualStage : clampWorkflowStageIndex(selectedWorkflowStage, actualStage);
  const viewedVisualStep = visualStepForInternalIndex(viewedStage);
  const viewingOtherStage = visualCanonicalIndex(viewedStage) !== visualCanonicalIndex(actualStage);
  const accordionOpenIndex = accordionIndexForFocus(viewedStage);
  const selectWorkflowStage = (idx) => {
    const next = clampWorkflowStageIndex(idx, actualStage);
    setSelectedWorkflowStage(next);
    setStageSheetOpen(false);
    syncPoDetailStageQuery(next, { push: true });
  };
  const lineItemCount = order.item_count ?? (order.line_items || []).length;
  // "Read-only" hint hanya muncul jika user benar-benar tidak punya action perm apapun
  // di halaman ini — Effective Permission based, bukan role-based.
  const hasAnyActionPerm = (
    canApproveDesign || canApprovePayment || canUploadPayment || canUploadDesign ||
    canManageUrgent || canManageBufferStock || canEditSchedule || canMarkPacked || canMarkDelivered ||
    canBypassDesign || canBypassPayment || canRollback || canCancel || canDelete ||
    canHelperPrep || canScreenerUpdate || canAdvanceNow || canApproveSample ||
    canManageSuratJalan || canCreateDelivery || canEditPoInfo || canReplaceLineProduct || canRepeatOrder
  );

  const copyTrackingLink = async () => {
    if (!order?.id || trackingBusy) return;
    setTrackingBusy(true);
    try {
      const r = await api.orderTrackingLink(order.id);
      const url = `${window.location.origin}${r.path || `/tracking/${r.token}`}`;
      await navigator.clipboard.writeText(url);
      setTrackingCopyMsg("Link tracking disalin");
      setTimeout(() => setTrackingCopyMsg(""), 2200);
    } catch (e) {
      const d = e.response?.data?.detail;
      setTrackingCopyMsg(typeof d === "string" ? d : "Gagal menyalin link tracking");
      setTimeout(() => setTrackingCopyMsg(""), 2800);
    } finally {
      setTrackingBusy(false);
    }
  };

  const doBypassDesign = async () => {
    const reason = window.prompt("Alasan bypass Approval Design (wajib):");
    if (!reason || !reason.trim()) return;
    await run(() => api.bypassDesign(order.id, reason.trim()), "Approval Design di-bypass");
  };
  const doBypassPayment = async () => {
    const reason = window.prompt("Alasan bypass Approval Payment (wajib):");
    if (!reason || !reason.trim()) return;
    await run(() => api.bypassPayment(order.id, reason.trim()), "Approval Payment di-bypass");
  };
  const doRollback = async () => {
    const to = window.prompt(`Rollback ke tahap apa? Ketik angka 0..${order.current_stage - 1}\n\n${STAGES.map((s, i) => `${i} = ${s}`).join("\n")}`);
    if (to === null) return;
    const toStage = parseInt(to, 10);
    if (!Number.isInteger(toStage) || toStage < 0 || toStage >= order.current_stage) { alert("Stage tujuan tidak valid."); return; }
    const reason = window.prompt("Alasan rollback (wajib):");
    if (!reason || !reason.trim()) return;
    await run(() => api.rollbackStage(order.id, toStage, reason.trim()), `Workflow dikembalikan ke ${STAGES[toStage]}`);
  };
  const saveScreener = () => {
    const q = Math.max(0, parseInt(screenerDraft || "0", 10));
    if (!Number.isFinite(q) || q <= 0) { alert("Qty tidak valid"); return; }
    setActionConfirm({
      kind: "screener",
      stage: "Screener",
      qty: q,
      actionId: newActionId(),
      notes: screenerNotes,
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };
  const doCancelPO = async () => {
    if (order.cancelled) { alert("PO sudah dibatalkan"); return; }
    const reason = window.prompt(`Batalkan PO ${order.po_number}?\nAlasan pembatalan (wajib):`);
    if (!reason || !reason.trim()) return;
    if (!window.confirm(`Anda akan membatalkan PO ${order.po_number} untuk ${order.customer}. Data tetap tersimpan sebagai histori. Lanjutkan?`)) return;
    await run(() => api.cancelPO(order.id, reason.trim()), "PO dibatalkan");
  };
  const openDeleteModal = async () => {
    setDeleteError("");
    setDeleteReason("");
    setDeleteConfirm("");
    setDeleteImpact(null);
    setShowDeleteModal(true);
    try {
      const d = await api.deleteImpact(order.id);
      setDeleteImpact(d);
    } catch (ex) {
      setDeleteError(ex.response?.data?.detail || "Tidak dapat memeriksa dampak");
    }
  };
  const closeDeleteModal = () => {
    if (deleting) return;
    setShowDeleteModal(false);
    setDeleteError("");
  };
  const submitDeletePO = async () => {
    setDeleteError("");
    const reason = deleteReason.trim();
    const confirm = deleteConfirm.trim();
    if (!reason) { setDeleteError("Alasan penghapusan wajib diisi."); return; }
    if (!confirm) { setDeleteError("Konfirmasi wajib diisi. Ketik nomor PO atau 'HAPUS'."); return; }
    if (confirm !== order.po_number && confirm.toUpperCase() !== "HAPUS") {
      setDeleteError(`Konfirmasi tidak cocok. Ketik "${order.po_number}" atau "HAPUS".`);
      return;
    }
    setDeleting(true);
    try {
      await api.deletePO(order.id, reason, confirm);
      setShowDeleteModal(false);
      // Redirect to dashboard with success toast passed via state
      navigate("/", { replace: true, state: { toast: `PO ${order.po_number} berhasil dihapus` } });
    } catch (ex) {
      const d = ex.response?.data?.detail;
      setDeleteError((typeof d === "object" ? d?.message : d) || "Gagal menghapus PO");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div
      className="po-detail po-detail-industrial po-detail-stage-workspace"
      data-testid="po-detail-page"
      data-actual-stage={actualStage}
      data-viewed-stage={viewedStage}
    >
      <div className="detail-header po-stage-ws-header">
        <div className="po-hero-title-row">
          <div className="po-hero-identity">
            <h1 className="po-hero-customer" data-testid="detail-customer-name">
              {order.customer || "Customer belum tersedia"}
            </h1>
            <div className="po-hero-identity-meta">
              <p className="po-hero-number" data-testid="detail-po-number">{order.po_number}</p>
              {(order.branch_name || order.branch) ? (
                <>
                  <span className="po-hero-dot" aria-hidden="true">·</span>
                  <span className="po-hero-branch" data-testid="detail-branch">{order.branch_name || order.branch}</span>
                </>
              ) : null}
              {order.outlet ? (
                <>
                  <span className="po-hero-dot" aria-hidden="true">·</span>
                  <p className="po-hero-outlet" data-testid="detail-outlet">{order.outlet}</p>
                </>
              ) : (
                <p className="po-hero-outlet" data-testid="detail-outlet" hidden>—</p>
              )}
            </div>
          </div>
          <div className="po-hero-title-actions">
            <button type="button" className="ghost sm po-hero-refresh" onClick={refreshPO} disabled={poIsPolling} data-testid="po-refresh-button">
              <RefreshCw size={11} /> Refresh
            </button>
            <button
              type="button"
              className="ghost sm"
              data-testid="btn-po-summary"
              aria-expanded={summaryOpen}
              onClick={() => setSummaryOpen(true)}
            >
              <List size={14} /> Ringkasan PO
            </button>
            <button
              type="button"
              className="ghost sm po-hero-back"
              onClick={() => {
                const target = resolvePoBackTarget({
                  location,
                  userId: storageUserKey(user),
                  orderId: id,
                  mobileFallback: "/m/tasks",
                  webFallback: "/",
                });
                navigate(target);
              }}
              data-testid="back-button"
            >
              <ArrowLeft size={16} /> Kembali ke Daftar PO
            </button>
          </div>
        </div>
        <div className="po-hero-toolbar" data-testid="po-hero-toolbar">
          <span className={order.priority === "urgent" ? "urgent-badge" : "standard-badge"} data-testid="detail-priority">
            {order.priority === "urgent" ? "URGENT" : "NORMAL"}
          </span>
          <span className="pill sm" data-testid="header-order-kind">
            {order.requires_printing === false ? "Non-Cetak" : "Cetak"}
          </span>
          {canRepeatOrder && !order.cancelled && (
            <Link
              to={`/penjualan/pesanan-penjualan?repeat_order_id=${encodeURIComponent(order.id)}`}
              className="ghost sm"
              data-testid="btn-repeat-order-detail"
              title="Buat pesanan baru dari PO ini"
            >
              Repeat Order
            </Link>
          )}
          {canManageUrgent && (
            <button type="button" className="ghost sm" onClick={toggleUrgent} disabled={busy} data-testid="toggle-urgent-button">
              <Zap size={14} /> {order.priority === "urgent" ? "Set normal" : "Set urgent"}
            </button>
          )}
          {order.buffer_stock && (
            <span className="pill sm" data-testid="buffer-stock-badge">
              BUFFER STOCK
            </span>
          )}
          {canManageBufferStock && (
            <button type="button" className="ghost sm" onClick={toggleBufferStock} disabled={busy} data-testid="toggle-buffer-stock-button">
              {order.buffer_stock ? "Unset Buffer Stock" : "Set Buffer Stock"}
            </button>
          )}
          <span className="po-hero-sedang" data-testid="po-actual-stage-label">
            Tahap aktif saat ini: <strong data-testid="detail-stage">{actualStage + 1}. {STAGES[actualStage]}</strong>
          </span>
          <span className="po-hero-qty" data-testid="po-header-total-qty">
            Qty: <strong>{(order.total_quantity ?? 0).toLocaleString("id-ID")}</strong>
          </span>
          <span className="po-hero-lines" data-testid="po-header-line-count">
            Line: <strong>{lineItemCount}</strong>
          </span>
          <span className="po-hero-ship-date" data-testid="header-tanggal-pengiriman">
            Tanggal Pengiriman:{" "}
            <strong data-testid="detail-tanggal-pengiriman-header">
              {formatDate(
                order.tanggal_pengiriman
                || order.effective_delivery_date
                || order.delivery_deadline_effective
                || order.scheduled_delivery_date
                || order.delivery_deadline_manual
              )}
            </strong>
            {order.tanggal_pengiriman_source === "manual" || order.delivery_deadline_source === "manual" ? (
              <span className="muted small"> Manual</span>
            ) : (order.tanggal_pengiriman || order.delivery_deadline_effective) ? (
              <span className="muted small"> Otomatis</span>
            ) : null}
          </span>
          <span data-testid="po-last-updated" className="po-hero-updated">
            Update: {poLastUpdated ? new Date(poLastUpdated).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }) : "-"}
          </span>
        </div>
      </div>

      <div className="po-mobile-stage-chrome" data-testid="po-mobile-stage-chrome">
        <div>
          <p className="po-mobile-stage-kicker">Tahap {viewedVisualStep.displayNumber} dari 15</p>
          <strong data-testid="po-mobile-stage-name">{viewedVisualStep.label}</strong>
        </div>
        <button
          type="button"
          className="ghost sm"
          data-testid="btn-open-stage-sheet"
          onClick={() => setStageSheetOpen(true)}
        >
          Semua tahap
        </button>
      </div>

      <div className="po-stage-bar-sticky" data-testid="po-stage-bar-sticky">
        <div className="po-stage-bar-block">
          <StageBar
            stage={actualStage}
            order={order}
            showLabels
            viewedStage={viewedStage}
            steps={PO_DETAIL_VISUAL_STEPS}
            onStageClick={selectWorkflowStage}
          />
        </div>
      </div>

      <section className="po-stage-ws-chrome" data-testid="po-stage-panel">
        <header className="po-stage-ws-chrome-head">
          <div>
            <span className="po-stage-ws-kicker">Workspace tahap</span>
            <h2 data-testid="po-viewed-stage-title">{poDetailStagePanelTitle(viewedStage)}</h2>
          </div>
          {viewingOtherStage && (
            <button
              type="button"
              className="ghost sm"
              data-testid="btn-back-to-active-stage"
              onClick={() => selectWorkflowStage(actualStage)}
            >
              Kembali ke Tahap Aktif
            </button>
          )}
        </header>
      <section className="po-kpi-strip" data-testid="po-kpi-strip" hidden={viewedStage !== 0}>
        <MetricRow columns={5}>
          <MetricCell label="Total PO" value={order.total_quantity} testid="kpi-total-po" />
          <MetricCell className="kpi-accent-target" label="Target Cetak" value={order.print_qty_target ?? order.print_qty_total} testid="kpi-print-target" />
          <MetricCell className="kpi-accent-done" label="Selesai Cetak" value={order.print_completed_qty} testid="kpi-print-done" />
          <MetricCell className="kpi-accent-remain" label="Sisa Cetak" value={order.print_qty_remaining} testid="kpi-print-remain" />
          <MetricCell className="kpi-accent-packed" label="Packing" value={order.packing_info?.qty_packed || 0} testid="kpi-packed" />
        </MetricRow>
        <div className="po-kpi-status-row" data-testid="po-kpi-status-row">
          <span>
            Payment:{" "}
            <strong className={order.payment_status === "Approved" ? "status-ok" : "status-wait"} data-testid="header-payment-status">
              {order.payment_status === "Approved" ? "Approved" : "Menunggu"}
            </strong>
          </span>
          <span>
            Design:{" "}
            <strong className={(order.design_status === "Approved" || isCustomerDesignApproved(order)) ? "status-ok" : "status-wait"} data-testid="header-design-status">
              {(order.design_status === "Approved" || isCustomerDesignApproved(order)) ? "Approved" : "Menunggu"}
            </strong>
          </span>
        </div>
      </section>

      {shouldShowPoActiveStagePanel(viewedStage) ? (
      <PoDetailFocusPanel
        order={order}
        viewedStage={viewedStage}
        actualStage={actualStage}
        onGotoStage={selectWorkflowStage}
        primaryAction={
          canProcessSalesDelivery ? (
            <Link
              to={`/penjualan/pengiriman-pesanan?po_id=${order.id}&action=create`}
              className="po-action-primary"
              data-testid="btn-proses-pengiriman-pesanan-focus"
            >
              <Truck size={14} /> Proses ke Pengiriman Pesanan
            </Link>
          ) : null
        }
      />
      ) : null}

      {/* HOLD banner (Req 2) */}
      {order.hold && (
        <div className="hold-banner" data-testid="hold-banner">
          <AlertCircle size={18}/>
          <div>
            <strong>PO HOLD — belum bisa masuk Penjadwalan</strong>
            <ul>{(order.hold_reasons || []).map((r, i) => <li key={i} data-testid={`hold-reason-${i}`}>{r}</li>)}</ul>
          </div>
        </div>
      )}
      {(order.design_bypassed || order.payment_bypassed) && (
        <div className="bypass-banner" data-testid="bypass-banner">
          <ShieldOff size={16}/>
          <div>
            {order.design_bypassed && <p data-testid="design-bypass-info"><b>DESIGN BYPASS</b> · {order.design_bypassed_by_name || order.design_bypassed_by} · {formatDate(order.design_bypassed_at)} · alasan: {order.design_bypass_reason}</p>}
            {order.payment_bypassed && <p data-testid="payment-bypass-info"><b>PAYMENT BYPASS</b> · {order.payment_bypassed_by_name || order.payment_bypassed_by} · {formatDate(order.payment_bypassed_at)} · alasan: {order.payment_bypass_reason}</p>}
          </div>
        </div>
      )}

      {err && <div className="error" data-testid="advance-error" style={{whiteSpace:"pre-line", margin: "16px 0"}}>{err}</div>}
      {isCrm && !hasAnyActionPerm && <p className="muted small" data-testid="crm-readonly-note">Mode CRM read-only — Anda hanya dapat melihat progres.</p>}

      {/* Cancelled banner */}
      {order.cancelled && (
        <div className="cancel-banner" data-testid="cancelled-banner">
          <XCircle size={18}/>
          <div>
            <strong>PO Dibatalkan</strong>
            <p>Dibatalkan oleh {order.cancelled_by_name || order.cancelled_by} · {formatDate(order.cancelled_at)}</p>
            <p className="muted small">Alasan: {order.cancel_reason}</p>
          </div>
        </div>
      )}

      <div className="po-workflow po-stage-ws-stack" data-testid="po-workflow">
      <PoStageAccordionProvider openStageIndex={accordionOpenIndex} onToggleStage={selectWorkflowStage}>
      {/* 01 Order Masuk */}
      <div id="po-stage-order-masuk" data-testid="workflow-slot-0" data-workflow-stage="0" className={deskPanelOwnsStage("0", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("0", viewedStage)}>
      <div className="po-meta-aksi-row" data-testid="po-meta-aksi-row">
      {/* Order Info */}
      <section className="panel po-info-panel" data-testid="detail-info">
        <header className="panel-head">
          <h3>Informasi PO</h3>
          {canEditPoInfo && !order.cancelled && (
            <button
              type="button"
              className="ghost sm"
              data-testid="po-edit-info"
              onClick={() => setEditInfoOpen(true)}
            >
              Edit Informasi
            </button>
          )}
        </header>
        <div className="po-info-grid">
        <div>
          <span className="lbl">PO / Sales Order</span>
          <strong className="mono">{order.po_number || <span className="po-info-empty">—</span>}</strong>
        </div>
        <div>
          <span className="lbl">Customer</span>
          <strong>{order.customer || <span className="po-info-empty">—</span>}</strong>
        </div>
        <div>
          <span className="lbl">Outlet</span>
          <strong>{order.outlet || <span className="po-info-empty">—</span>}</strong>
          {canSetLocation && (
            <button
              type="button"
              className="ghost sm po-info-edit"
              data-testid="po-atur-lokasi"
              onClick={async () => {
                const row = {
                  order_id: order.id,
                  customer: order.customer || order.customer_name || "",
                  outlet: order.outlet || "",
                  outlet_name: order.outlet || "",
                };
                try {
                  const d = await api.listDeliveryOutlets(row.customer);
                  const o = (d.outlets || [])[0];
                  if (o) {
                    row.outlet_id = o.id;
                    row.outlet_name = o.name || order.outlet || "";
                    row.lat = o.lat;
                    row.lng = o.lng;
                    row.place_id = o.place_id || o.google_place_id;
                    row.formatted_address = o.formatted_address;
                  }
                } catch {
                  /* modal still opens */
                }
                setLocRow(row);
              }}
            >
              {order.outlet ? "Edit Lokasi" : "Atur Lokasi"}
            </button>
          )}
        </div>
        <div>
          <span className="lbl">Tanggal PO</span>
          <strong>{formatDate(order.order_date)}</strong>
        </div>
        <div>
          <span className="lbl">Tanggal Pengiriman</span>
          <strong data-testid="detail-tanggal-pengiriman">
            {formatDate(
              order.tanggal_pengiriman
              || order.effective_delivery_date
              || order.delivery_deadline_effective
              || order.scheduled_delivery_date
              || order.delivery_deadline_manual
            )}
            {order.tanggal_pengiriman_source === "manual" || order.delivery_deadline_source === "manual" ? (
              <span className="muted small" style={{ marginLeft: 6 }}>Manual</span>
            ) : (order.tanggal_pengiriman || order.delivery_deadline_effective) ? (
              <span className="muted small" style={{ marginLeft: 6 }}>Otomatis</span>
            ) : null}
          </strong>
        </div>
        <div>
          <span className="lbl">Estimasi kirim</span>
          <strong data-testid="detail-estimated-delivery">{formatDate(order.estimated_delivery)}</strong>
        </div>
        <div>
          <span className="lbl">Penerima</span>
          <strong>{order.recipient || <span className="po-info-empty">—</span>}</strong>
        </div>
        <div>
          <span className="lbl">Sales</span>
          <strong data-testid="detail-sales">
            {order.sales || order.sales_user || <em className="muted">Perlu verifikasi</em>}
          </strong>
        </div>
        <div className="po-info-address">
          <span className="lbl"><MapPin size={12} /> Alamat</span>
          <strong data-testid="detail-address">{order.address || <span className="po-info-empty">—</span>}</strong>
        </div>
        <div>
          <span className="lbl">Ekspedisi</span>
          <strong>{order.expedition || <span className="po-info-empty">—</span>}</strong>
        </div>
        <div>
          <span className="lbl">Zona kirim</span>
          <strong>{order.delivery_zone || <span className="po-info-empty">—</span>}</strong>
        </div>
        <div>
          <span className="lbl">User / Pembuat PO</span>
          <strong data-testid="detail-created-by-user">
            {order.created_by_user || <em className="muted">Perlu verifikasi</em>}
          </strong>
        </div>
        <div>
          <span className="lbl">Termin bayar</span>
          <strong>{order.payment_terms || <span className="po-info-empty">—</span>}</strong>
        </div>
        <div>
          <span className="lbl">Status Payment</span>
          <strong className={order.payment_status === "Approved" ? "status-ok" : "status-wait"} data-testid="payment-status">
            {order.payment_status === "Approved" ? "Approved" : "Menunggu approval"}
          </strong>
        </div>
        <div>
          <span className="lbl">Status Design</span>
          <strong className={(order.design_status === "Approved" || isCustomerDesignApproved(order)) ? "status-ok" : "status-wait"} data-testid="design-status">
            {(order.design_status === "Approved" || isCustomerDesignApproved(order))
              ? "Approved"
              : (order.design_files?.length ? "Menunggu approval" : "Belum ada design")}
          </strong>
        </div>
        {order.imported_by && (
          <div>
            <span className="lbl">Diimport oleh (Admin)</span>
            <strong data-testid="detail-imported-by">
              {order.imported_by_name || order.imported_by}
              {order.imported_at && <span className="muted small"> · {formatDate(order.imported_at)}</span>}
            </strong>
          </div>
        )}
        {order.accurate_id && (
          <div>
            <span className="lbl">Accurate ID</span>
            <strong className="mono" data-testid="detail-accurate-id">{order.accurate_id}</strong>
          </div>
        )}
        {order.notes && (
          <div className="po-info-address" data-testid="detail-keterangan">
            <span className="lbl">Keterangan</span>
            <strong style={{whiteSpace:"pre-wrap"}}>{order.notes}</strong>
          </div>
        )}
        {order.source_pdf?.storage_path && (
          <div>
            <span className="lbl">PDF asli import</span>
            <strong>
              <a
                href={`${process.env.REACT_APP_BACKEND_URL}/api/files/${order.source_pdf.storage_path}`}
                target="_blank" rel="noopener noreferrer"
                data-testid="detail-source-pdf"
                className="link-inline"
              >
                <FileText size={13}/> {order.source_pdf.original_filename || "Lihat PDF"}
              </a>
            </strong>
          </div>
        )}
        {order.urgency_approved_by && (
          <div>
            <span className="lbl">Urgent disetujui oleh</span>
            <strong>{order.urgency_approved_by}</strong>
          </div>
        )}
        </div>
      </section>

      <section className="panel po-actions-panel" data-testid="po-actions-panel">
        <header className="panel-head">
          <h3>Aksi PO</h3>
          <span className="muted small">Operasional · dokumen · pengecualian</span>
        </header>
        <div className="po-actions-ops" data-testid="po-actions-ops">
          {canCopyTrackingLink && (
            <div className="po-tracking-actions" data-testid="tracking-customer-panel">
              <button
                className="ghost sm"
                onClick={copyTrackingLink}
                disabled={trackingBusy || order.cancelled}
                data-testid="copy-tracking-link"
              >
                <Copy size={14} /> Live Tracking
              </button>
              {trackingCopyMsg && <span className="muted small" data-testid="tracking-copy-msg">{trackingCopyMsg}</span>}
            </div>
          )}
          {canProcessSalesDelivery ? (
            <Link
              to={`/penjualan/pengiriman-pesanan?po_id=${order.id}&action=create`}
              className="po-action-primary"
              data-testid="btn-proses-pengiriman-pesanan"
            >
              <Truck size={16} /> PROSES KE PENGIRIMAN PESANAN
            </Link>
          ) : salesDeliveryAllQtyDone ? (
            <span className="po-action-done" data-testid="badge-pengiriman-selesai">
              <Check size={14} /> Seluruh Qty Selesai Dibuatkan SJ
            </span>
          ) : null}
        </div>
        <div className="po-actions-admin-docs" data-testid="po-actions-admin-docs">
          <span className="po-actions-group-label">Dokumen</span>
          <Link
            to={`/penjualan/faktur-penjualan?po_id=${order.id}&action=create`}
            className="po-action-doc"
            data-testid="btn-buat-faktur-penjualan"
          >
            <FileText size={16} /> BUAT FAKTUR
          </Link>
        </div>
        {(canRollback || canCancel || canDelete) && (
          <div className="po-actions-admin">
            <button
              type="button"
              className="po-actions-danger-toggle"
              aria-expanded={dangerActionsOpen}
              data-testid="po-actions-danger-toggle"
              onClick={() => setDangerActionsOpen((v) => !v)}
            >
              Aksi berbahaya / pengecualian
              <ChevronDown size={14} className={`po-stage-chevron${dangerActionsOpen ? " is-open" : ""}`} aria-hidden="true" />
            </button>
            <div className="po-actions-danger-body" hidden={!dangerActionsOpen} data-testid="po-actions-danger-body">
              <div className="po-actions-row">
                {canRollback && order.current_stage > 0 && (
                  <button className="ghost" onClick={doRollback} disabled={busy} data-testid="rollback-button" title="Butuh permission workflow.rollback">
                    <RotateCcw size={14}/> Rollback
                  </button>
                )}
                {canCancel && !order.cancelled && (
                  <button className="ghost" onClick={doCancelPO} disabled={busy} data-testid="cancel-po-button" title="Butuh permission po.cancel">
                    <XCircle size={16}/> Batalkan PO
                  </button>
                )}
                {canDelete && (
                  <button className="ghost danger" onClick={openDeleteModal} disabled={busy || deleting} data-testid="delete-po-button" title="Butuh permission po.delete">
                    <Trash2 size={16}/> Hapus Permanen PO
                  </button>
                )}
              </div>
              <p className="muted small po-actions-hint">
                <b>Batalkan PO</b> menyimpan histori &amp; audit (tab <em>Cancelled</em>).
                {" "}<b>Hapus Permanen</b> hanya untuk PO test/duplicate.
              </p>
            </div>
          </div>
        )}
      </section>
      </div>

      <section className="panel po-line-items-panel" data-testid="line-items-panel">
        <header className="panel-head">
          <h3>Line Item ({order.item_count ?? 0})</h3>
          <span className="pill" data-testid="scope-total-po">Total PO: {(order.total_quantity ?? 0).toLocaleString("id-ID")} pcs</span>
          <span className="pill sm" data-testid="scope-print-qty">
            Cetak: {Number(order.print_qty_total ?? 0).toLocaleString("id-ID")}
          </span>
          <span className="pill sm" data-testid="scope-nonprint-qty">
            Non-Cetak: {Math.max(Number(order.total_quantity ?? 0) - Number(order.print_qty_total ?? 0), 0).toLocaleString("id-ID")}
          </span>
        </header>
        <div className="line-items-table-wrap">
          <table className="line-items-table" data-testid="line-items-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Kode</th>
                <th className="num">Qty</th>
                <th>Warna</th>
                <th>Cetak / Non-Cetak</th>
                <th>Status</th>
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {(order.line_items || []).map((li, i) => {
                const process = String(li.production_process || "").toLowerCase();
                const statusLabel =
                  process === "non_print"
                    ? "Non-Cetak"
                    : process === "print"
                      ? "Cetak"
                      : "Belum diklasifikasi";
                const statusTone =
                  process === "non_print"
                    ? "is-nonprint"
                    : process === "print"
                      ? "is-print"
                      : "is-unset";
                return (
                  <tr key={i} className="line-item-row" data-testid={`line-item-${i}`}>
                    <td className="li-name">
                      <strong>{li.name}</strong>
                      {li.notes ? <span className="li-notes">Catatan sablon: {li.notes}</span> : null}
                    </td>
                    <td className="mono" data-testid={`line-item-code-${i}`}>{li.item_code}</td>
                    <td className="num" data-testid={`line-item-qty-${i}`}>
                      {(li.quantity ?? 0).toLocaleString("id-ID")}
                      <span className="muted small"> {li.unit}</span>
                    </td>
                    <td data-testid={`line-item-color-${i}`}>{salesOrderLineColor(li) || "-"}</td>
                    <td className="li-route-cell">
                      <LineRouteEditor
                        order={order}
                        lineIdx={i}
                        canEdit={canEditRoute}
                        onUpdated={setOrder}
                      />
                    </td>
                    <td>
                      <span
                        className={`li-process ${statusTone}`}
                        data-testid={`line-item-process-${i}`}
                      >
                        {statusLabel}
                      </span>
                    </td>
                    <td className="li-actions">
                      <LineProductReplaceControl
                        order={order}
                        lineIdx={i}
                        canEdit={canReplaceLineProduct}
                        onUpdated={setOrder}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <ProductionRoutePanel order={order} canEdit={canEditRoute} onUpdated={setOrder} compact />
      </div>

      <div className="workflow-group" data-testid="workflow-group-2">
        <p className="workflow-group-label">A · DESIGN &amp; PAYMENT</p>
      {/* 02–03 Design + Approval Design (visual workspace only; internal stages stay 2 and 3) */}
      <section
        id="po-stage-design"
        className={"panel po-pair-card po-phase-design" + (deskPanelOwnsStage("design", viewedStage) ? " is-desk-viewed" : "")}
        data-testid="design-approval-pair"
        hidden={!deskPanelOwnsStage("design", viewedStage)}
      >
      <PoStageSection stageIndex={1} order={order} title="Design" hasError={!!err}>
        <header className="panel-head">
          <h3>DESIGN — 2&amp;3</h3>
          <DesignStatusBadge order={order} />
        </header>
        <div className="po-pair-grid">
          <div className="po-pair-col" data-testid="workflow-slot-1" data-workflow-stage="1">
            <h4 className="po-pair-col-title">Data / Input Design</h4>
            <AttachmentsPanel
              order={order}
              user={user}
              onUpdated={setOrder}
              section="design"
              designSlot={(
                <div className="design-actions" data-testid="design-actions" style={{display:"flex",flexWrap:"wrap",gap:8,marginTop:10,alignItems:"center"}}>
                  <DesignStatusBadge order={order} />
                </div>
              )}
            />
          </div>
          <div className="po-pair-col" data-testid="workflow-slot-2" data-workflow-stage="2">
            <div data-testid="approval-design-panel">
              <h4 className="po-pair-col-title">Design / Approval</h4>
              <h4 className="po-pair-col-title">Internal Approval</h4>
              <div className="workflow-stage-actions" data-testid="approval-design-actions">
                {canApproveDesign && (order.design_files?.length || 0) > 0 && !isApprovalDesignComplete(order) && (
                  <button className="primary" onClick={approveDesign} disabled={busy} data-testid="approve-design-button">
                    <FileCheck2 size={16} /> Approve Design
                  </button>
                )}
                {canApproveDesign && order.design_status === "Approved" && (order.current_stage || 0) < 12 && (
                  <button className="ghost" onClick={rejectDesign} disabled={busy} data-testid="revoke-design-button">
                    Batalkan approve design
                  </button>
                )}
                {canBypassDesign && !isApprovalDesignComplete(order) && (
                  <button className="ghost" onClick={doBypassDesign} disabled={busy} data-testid="bypass-design-button" title="Butuh permission design.bypass_approval">
                    <ShieldOff size={14}/> Bypass Design
                  </button>
                )}
                {!canApproveDesign && !canBypassDesign && !isApprovalDesignComplete(order) && (
                  <p className="muted small" data-testid="design-readonly-note">Anda hanya dapat melihat status approval design (read-only).</p>
                )}
              </div>
              <CustomerApprovalPanel
                order={order}
                canView={canViewCustomerApproval}
                canManage={canManageCustomerApproval}
                onOrderUpdated={setOrder}
              />
            </div>
          </div>
        </div>
      </PoStageSection>
      </section>

      {/* 04–05 Payment + Approval Payment (visual pair only) */}
      <section id="po-stage-payment" className={"panel payment-panel po-pair-card po-phase-payment" + (deskPanelOwnsStage("payment", viewedStage) ? " is-desk-viewed" : "")} data-testid="payment-approval-pair" hidden={!deskPanelOwnsStage("payment", viewedStage)}>
        <PoStageSection stageIndex={3} order={order} title="Payment &amp; Approval" hasError={!!payUploadErr || !!salesReceiptsErr}>
        <header className="panel-head">
          <h3>PAYMENT — 4&amp;5</h3>
          <span className="muted small" data-testid="payment-proof-count">
            Bukti Payment {paymentProofCount(order.payment_files)} / {MAX_PAYMENT_PROOFS} file
          </span>
          <PaymentStatusBadge order={order} receipts={salesReceipts} />
        </header>
        <div className="po-pair-grid">
        <div className="po-pair-col" data-testid="workflow-slot-3" data-workflow-stage="3">
        <div data-testid="payment-panel">
        <h4 className="po-pair-col-title">Bukti Payment</h4>
        <div className="payment-body">
          <PaymentFilesList files={order.payment_files || []} order={order} hasReceiptProof={salesReceipts.some(receiptHasPaymentProof)} />
          {payUploadErr && (
            <div className="error" data-testid="payment-upload-error">{payUploadErr}</div>
          )}
          <div className="payment-actions">
            {canUploadPayment && !order.cancelled && (
              canAddPaymentProof(order.payment_files) ? (
              <label className={"primary btn-file" + (payUploading ? " disabled" : "")} data-testid="upload-payment-label">
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/jpg,application/pdf"
                  disabled={payUploading}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = "";
                    if (f) uploadPaymentProof(f);
                  }}
                  data-testid="upload-payment-input"
                  style={{display:"none"}}
                />
                <FileText size={16} /> {payUploading ? "Mengupload..." : "Tambah Bukti"}
              </label>
              ) : (
                <p className="muted small" data-testid="payment-max-reached">Maksimal {MAX_PAYMENT_PROOFS} file.</p>
              )
            )}
            {!canUploadPayment && (
              <p className="muted small" data-testid="payment-readonly-note">Anda hanya dapat melihat bukti pembayaran (read-only).</p>
            )}
          </div>
        </div>
        </div>
        </div>

      <div className="po-pair-col" data-testid="workflow-slot-4" data-workflow-stage="4">
      <div data-testid="approval-payment-panel">
        <h4 className="po-pair-col-title">Approval Payment</h4>
        <div className="sales-receipts-payment" data-testid="sales-receipts-payment-section">
          <h4 style={{ margin: "0 0 8px 0", fontSize: 13 }}>Penerimaan Penjualan</h4>
          {salesReceiptsLoading && (
            <p className="muted small" data-testid="sales-receipts-loading">Memuat penerimaan…</p>
          )}
          {salesReceiptsErr && (
            <div className="error" data-testid="sales-receipts-error">{salesReceiptsErr}</div>
          )}
          {!salesReceiptsLoading && !salesReceiptsErr && salesReceipts.length === 0 && (
            <p className="muted small" data-testid="sales-receipts-empty">
              Belum ada Penerimaan Penjualan untuk PO ini.
            </p>
          )}
          {salesReceipts.length > 0 && (
            <ul className="payment-files" data-testid="sales-receipts-payment-list">
              {salesReceipts.map((r) => {
                const hasProof = receiptHasPaymentProof(r);
                return (
                  <li key={r.id} data-testid={`sales-receipt-payment-row-${r.id}`} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", justifyContent: "space-between" }}>
                    <div style={{ minWidth: 0 }}>
                      <div className="mono small">{r.receipt_number || r.id}</div>
                      <div className="muted small">
                        {formatDisplayDate(r.receipt_date || r.created_at)}
                        {" · "}
                        Rp {Number(r.amount || 0).toLocaleString("id-ID")}
                        {" · "}
                        {receiptProofStatusLabel(r)}
                      </div>
                    </div>
                    <div>
                      {hasProof && canViewReceiptProof ? (
                        <button
                          type="button"
                          className="ghost sm"
                          data-testid={`btn-view-receipt-proof-${r.id}`}
                          onClick={() => setProofViewerReceipt(r)}
                        >
                          <Eye size={14} /> Lihat Bukti Payment
                        </button>
                      ) : !hasProof ? (
                        <span className="muted small" data-testid={`receipt-no-proof-${r.id}`}>
                          Tidak ada bukti payment
                        </span>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="workflow-stage-actions" data-testid="approval-payment-actions">
          {canApprovePayment && order.payment_files?.length > 0 && order.payment_status !== "Approved" && !order.payment_bypassed && (
            <button className="primary" onClick={approvePayment} disabled={busy} data-testid="approve-payment-button">
              <FileCheck2 size={16} /> Approve Payment
            </button>
          )}
          {canApprovePayment && order.payment_status === "Approved" && (order.current_stage || 0) < 12 && (
            <button className="ghost" onClick={rejectPayment} disabled={busy} data-testid="revoke-payment-button">
              Batalkan approve payment
            </button>
          )}
          {canBypassPayment && !order.payment_bypassed && order.payment_status !== "Approved" && (
            <button className="ghost" onClick={doBypassPayment} disabled={busy} data-testid="bypass-payment-button" title="Butuh permission payment.bypass_approval">
              <ShieldOff size={14}/> Bypass Payment
            </button>
          )}
        </div>
        {(order.payment_approval_history || []).length > 0 && (
          <details className="payment-history" data-testid="payment-history">
            <summary>Histori Approval Payment ({(order.payment_approval_history || []).length})</summary>
            <ul>
              {(order.payment_approval_history || []).slice().reverse().map((h, i) => (
                <li key={i}>
                  <span className="mono small">{formatDate(h.at)}</span>
                  <span> · {h.action}</span>
                  {h.by_name && <span className="muted small"> · oleh {h.by_name}</span>}
                  {h.note && <span className="muted small"> · {h.note}</span>}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
      </div>
        </div>
      </PoStageSection>
      </section>
      </div>

      {/* 06 Penjadwalan + Pembagian Jadwal (visual pair only) */}
      <div className="workflow-group" data-testid="workflow-group-prep">
        <p className="workflow-group-label">B · PERSIAPAN PRODUKSI</p>
      <div id="po-stage-penjadwalan" data-testid="workflow-slot-5" data-workflow-stage="5" className={deskPanelOwnsStage("5", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("5", viewedStage)}>
      <PoStageSection stageIndex={5} order={order} title="Penjadwalan">
      <section className="panel qty-ops-panel po-pair-card po-phase-scheduling" data-testid="penjadwalan-panel">
        <QtyOpsHead icon={CalendarClock} title="Penjadwalan">
          <span className="muted small">Head of Production</span>
        </QtyOpsHead>
        <div className="po-pair-grid po-pair-grid-schedule">
          <div className="po-pair-col">
            <h4 className="po-pair-col-title">Penjadwalan</h4>
            <p className="qty-ops-note">
              Simpan pembagian operator. Screener dan Persiapan Helper terbuka otomatis setelah save berhasil.
            </p>
          </div>
          <div className="po-pair-col">
            <OperatorSplitPanel order={order} canEdit={canEditSchedule} onUpdated={setOrder} mode="schedule" />
          </div>
        </div>
      </section>
      </PoStageSection>
      </div>

      {/* 07 Screener */}
      <div id="po-stage-screener" data-testid="workflow-slot-6" data-workflow-stage="6" className={deskPanelOwnsStage("6", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("6", viewedStage)}>
      <PoStageSection
        stageIndex={6}
        order={order}
        title="Screener"
      >
      <section className="panel qty-ops-panel" data-testid="screener-panel">
        <QtyOpsHead icon={ScanLine} title="Screener">
          <span className={`qty-status ${statusClassFor(screenerLocked ? "IDLE" : (screenerFilled ? "SAVED" : "READY"))}`}>
            {screenerLocked ? "IDLE" : (screenerFilled ? "SAVED" : "READY")}
          </span>
          <span className="muted small">Pencatatan aktivitas — bukan cap produksi</span>
        </QtyOpsHead>
        <div className="qty-ops-body">
        <AppendQtyEntries
          entries={order.screener_info?.entries}
          snapshotQty={order.screener_info?.screen_qty}
          snapshotAt={order.screener_info?.updated_at}
          snapshotName={order.screener_info?.updated_by_name}
          draft={screenerDraft}
          onDraftChange={setScreenerDraft}
          onConfirmClick={saveScreener}
          canEdit={canScreenerUpdate && !screenerLocked}
          busy={busy}
          testid="screener-append"
          inputTestid="screener-qty"
          confirmTestid="screener-save-button"
        />
        <label className="qty-notes">
          <span className="qty-notes-label">Catatan Screener</span>
          <input
            type="text"
            className="manual-input"
            value={screenerNotes}
            onChange={(e) => setScreenerNotes(e.target.value)}
            data-testid="screener-notes"
            disabled={!canScreenerUpdate || screenerLocked}
          />
        </label>
        {screenerLocked && (
          <p className="muted small" data-testid="screener-locked-note" style={{ marginTop: 10 }}>Screener tersedia otomatis setelah Penjadwalan disimpan.</p>
        )}
        {order.screener_info?.updated_at && (
          <span className="muted small" data-testid="screener-updated">Update terakhir: {order.screener_info.updated_by_name || order.screener_info.updated_by} · {formatDate(order.screener_info.updated_at)}</span>
        )}
        </div>
      </section>
      </PoStageSection>
      </div>

      {/* 08 Persiapan Helper */}
      <div id="po-stage-persiapan-helper" data-testid="workflow-slot-7" data-workflow-stage="7" className={deskPanelOwnsStage("7", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("7", viewedStage)}>
      <PoStageSection stageIndex={7} order={order} title="Persiapan Helper">
      <HelperPrepPanel order={order} canEdit={canHelperPrep} onUpdated={setOrder} />
      </PoStageSection>
      </div>

      </div>

      <div className={"workflow-group" + (deskPanelOwnsStage("sample", viewedStage) ? " is-desk-viewed" : "")} data-testid="workflow-group-5" hidden={!deskPanelOwnsStage("sample", viewedStage)}>
        <p className="workflow-group-label">C · PRODUKSI</p>
      {/* A–B Cetak Sample + Approval (visual pair only) */}
      <div data-testid="desk-panel-sample">
      <PoStageSection stageIndex={8} order={order} title="Cetak Sample &amp; Approval">
      <section className="panel po-pair-card po-phase-sample" data-testid="cetak-sample-pair">
        <header className="panel-head qty-ops-head">
          <div className="qty-ops-title">
            <h3>Cetak Sample</h3>
          </div>
          <div className="qty-ops-head-meta">
            {order.sample_approved ? (
              <span className={`qty-status ${statusClassFor("APPROVED")}`} data-testid="sample-status-badge">
                APPROVED
                {order.sample_approved_by_name ? ` — ${order.sample_approved_by_name}` : ""}
              </span>
            ) : (order.sample_approval_history || []).slice(-1)[0]?.action === "reject" ? (
              <span className={`qty-status ${statusClassFor("REJECTED")}`} data-testid="sample-status-badge">
                REJECTED
              </span>
            ) : (order.sample_files?.length || 0) > 0 ? (
              <span className={`qty-status ${statusClassFor("READY")}`} data-testid="sample-status-badge">
                READY
              </span>
            ) : (
              <span className="qty-status" data-testid="sample-status-badge">
                IDLE
              </span>
            )}
          </div>
        </header>
        <div className="po-pair-grid">
        <div id="po-stage-operator" className="po-pair-col" data-testid="workflow-slot-8" data-workflow-stage="8" hidden={viewedStage !== 8}>
      <AttachmentsPanel
        order={order}
        user={user}
        onUpdated={setOrder}
        section="sample"
      />
        </div>

      <div id="po-stage-konfirmasi-sample" className="po-pair-col" data-testid="workflow-slot-9" data-workflow-stage="9" hidden={viewedStage !== 9}>
      <div className="qty-ops-panel" data-testid="approval-sample-panel">
        <h4 className="po-pair-col-title">Approval Cetak Sample</h4>
        <div className="qty-ops-body">
        <div className="sample-actions" data-testid="sample-actions" style={{display:"flex",flexWrap:"wrap",gap:8,alignItems:"center"}}>
          {canApproveSample && (order.sample_files?.length || 0) > 0 && !order.sample_approved && (
            <>
              <button className="primary" onClick={approveSample} disabled={busy} data-testid="approve-sample-button">
                <FileCheck2 size={16} /> Approve
              </button>
              <button className="ghost" onClick={() => run(() => api.approveSample(order.id, false, note), "Sampel ditolak")} disabled={busy} data-testid="reject-sample-button">
                <XCircle size={16} /> Reject
              </button>
            </>
          )}
          {canApproveSample && order.sample_approved && (order.current_stage || 0) < 12 && (
            <button className="ghost" onClick={rejectSample} disabled={busy} data-testid="revoke-sample-button">
              Batalkan approval sampel
            </button>
          )}
          {!canApproveSample && (order.sample_files?.length || 0) > 0 && !order.sample_approved && (
            <p className="muted small" data-testid="sample-readonly-note">Butuh permission Penjadwalan untuk approval sampel cetak.</p>
          )}
        </div>
        </div>
      </div>
      </div>
        </div>
      </section>
      </PoStageSection>
      </div>
      </div>

      {/* C Operator / Qty Selesai Cetak */}
      <div id="po-stage-cetak" data-testid="workflow-slot-10" data-workflow-stage="10" className={deskPanelOwnsStage("10", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("10", viewedStage)}>
      <PoStageSection stageIndex={10} order={order} title="Qty Selesai Cetak">
      <div data-testid="cetak-panel">
      <OperatorSplitPanel order={order} canEdit={canEditSchedule} onUpdated={setOrder} mode="progress" />
      </div>
      </PoStageSection>
      </div>

      <div className="workflow-group" data-testid="workflow-group-fulfillment">
        <p className="workflow-group-label">D · QC DAN PENGIRIMAN</p>
      {/* 12 Packing */}
      <div id="po-stage-packing" data-testid="workflow-slot-11" data-workflow-stage="11" className={deskPanelOwnsStage("11", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("11", viewedStage)}>
      <PoStageSection stageIndex={11} order={order} title="Packing">
      <PackingPanel
        order={order}
        canManage={canMarkPacked}
        busy={busy}
        onSaveQty={async (lines, notes) => {
          const res = await api.savePackingInfo(order.id, lines, notes);
          commitOrder(res.order);
          showToast("Qty Packing disimpan");
        }}
      />
      </PoStageSection>
      </div>

      {/* 13 Surat Jalan */}
      <div id="po-stage-surat-jalan" data-testid="workflow-slot-12" data-workflow-stage="12" className={deskPanelOwnsStage("12", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("12", viewedStage)}>
      <PoStageSection stageIndex={12} order={order} title="Surat Jalan">
      <SuratJalanPanel
        order={order}
        canManage={canManageSuratJalan}
        canViewFile={canViewSjFile}
        canDeleteFile={hasPerm("delivery_note.delete_file")}
        allowUpload={false}
        busy={busy}
        onUpdated={(next, action) => {
          if (next) {
            commitOrder(next);
            showToast(action === "delete" ? "File Surat Jalan dihapus" : "Surat Jalan diupload");
          }
        }}
      />
      </PoStageSection>
      </div>

      {/* 14 Loading */}
      <div id="po-stage-loading" data-testid="workflow-slot-13" data-workflow-stage="13" className={deskPanelOwnsStage("13", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("13", viewedStage)}>
      <PoStageSection stageIndex={13} order={order} title="Loading">
      <LoadingPanel
        order={order}
        canManage={canLoadingUpdate}
        onUpdated={(next) => {
          if (next) {
            commitOrder(next);
            showToast("Loading dikonfirmasi");
          }
        }}
      />
      </PoStageSection>
      </div>

      {/* 15 Bukti Kirim then Terkirim */}
      <div id="po-stage-pengiriman" data-testid="workflow-slot-14" data-workflow-stage="14" className={deskPanelOwnsStage("14", viewedStage) ? "is-desk-viewed" : ""} hidden={!deskPanelOwnsStage("14", viewedStage)}>
      <PoStageSection stageIndex={14} order={order} title="Bukti Kirim &amp; Terkirim">
      <PengirimanDeliveryProofPanel order={order} onUpdated={setOrder} />
      <section className="panel" data-testid="terkirim-panel">
        <header className="panel-head">
          <h3>Terkirim</h3>
          <span className={`pill sm ${order.delivery_status === "Terkirim" ? "pill-ok" : ""}`} data-testid="delivery-status">
            {order.delivery_status || "Belum Terkirim"}
          </span>
        </header>
        <MetricRow testid="terkirim-summary">
          <MetricCell label="Total PO" value={terkirimSummary.total} testid="terkirim-total-po" />
          <MetricCell label="Sudah Terkirim" value={terkirimSummary.delivered} testid="terkirim-delivered-qty" />
          <MetricCell label="Belum Terkirim" value={terkirimSummary.remaining} testid="terkirim-remaining-qty" />
        </MetricRow>
        <p className="muted small">
          Status Terkirim hanya setelah minimal 2 foto bukti kirim di-upload, lalu Konfirmasi Terkirim.
        </p>
        <SuratJalanViewButton order={order} testid="terkirim-sj-lihat" />
      </section>
      </PoStageSection>
      </div>
      </div>
      </PoStageAccordionProvider>
      </div>
      </section>

      {/* Notes — Order Masuk workspace */}
      <div hidden={!deskPanelOwnsStage("0", viewedStage)}>
      <section className={`panel po-notes-panel${!order.notes && !note ? " is-empty" : ""}`} data-testid="notes-panel">
        <header className="panel-head"><h3>Catatan Produksi</h3></header>
        {order.notes && <pre className="notes-view">{order.notes}</pre>}
        {!order.notes && !note && <p className="muted small po-notes-empty">Belum ada catatan produksi.</p>}
        {!isCrm && (
          <div className="note-add">
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Tambahkan catatan..."
              rows={2}
              data-testid="note-input"
              className="manual-input"
            />
            <button className="ghost sm" onClick={saveNote} disabled={busy || !note.trim()} data-testid="save-note-button">
              <MessageSquare size={14} /> Simpan catatan
            </button>
          </div>
        )}
      </section>

      <Timeline timeline={order.timeline} currentStage={order.current_stage} order={order} defaultOpen={false} />
      </div>

      <PoSummaryDrawer
        open={summaryOpen}
        order={order}
        actualStage={actualStage}
        viewedStage={viewedStage}
        onClose={() => setSummaryOpen(false)}
      />
      <PoStagePickerSheet
        open={stageSheetOpen}
        order={order}
        actualStage={actualStage}
        viewedStage={viewedStage}
        onSelect={selectWorkflowStage}
        onClose={() => setStageSheetOpen(false)}
      />

      <ConfirmActionModal
        open={!!actionConfirm}
        stage={actionConfirm?.stage}
        qty={actionConfirm?.qty}
        name={user?.name || user?.email}
        time={actionConfirm?.time}
        extra={actionConfirm?.extra ? <p className="muted small">{actionConfirm.extra}</p> : null}
        busy={busy}
        error={actionConfirm?.error}
        onCancel={() => { if (!busy) setActionConfirm(null); }}
        onConfirm={executeActionConfirm}
      />

      {toast && (
        <div className="toast" data-testid="detail-toast">
          <Check size={18} />{toast}
        </div>
      )}

      {/* Delete PO Modal (Req 5) */}
      {showDeleteModal && (        <div className="modal" role="dialog" aria-modal="true" data-testid="delete-po-modal" onClick={closeDeleteModal}>
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3><AlertTriangle size={18} style={{ color: "#a53f2b", verticalAlign: "-3px", marginRight: 6 }} />Hapus Permanen PO</h3>
              <button className="icon-button" onClick={closeDeleteModal} disabled={deleting} data-testid="delete-modal-close" aria-label="Tutup">
                <X size={18}/>
              </button>
            </div>
            <div className="delete-modal-body">
              <div className="delete-warning" data-testid="delete-warning">
                <AlertTriangle size={16}/>
                <div>
                  <strong>Tindakan ini permanen dan tidak dapat dibatalkan.</strong>
                  <p className="muted small">PO akan dihapus dari database. Snapshot tetap tersimpan di audit log untuk kebutuhan investigasi.</p>
                </div>
              </div>
              <dl className="delete-summary">
                <div><dt>Nomor PO</dt><dd data-testid="delete-modal-po-number"><strong>{order.po_number}</strong></dd></div>
                <div><dt>Customer</dt><dd data-testid="delete-modal-customer">{order.customer || "-"}</dd></div>
                <div><dt>Status</dt><dd>{order.cancelled ? "Cancelled" : (order.status_label || STAGES[order.current_stage])}</dd></div>
                {deleteImpact?.impact && (
                  <>
                    <div><dt>Line items</dt><dd>{deleteImpact.impact.line_items}</dd></div>
                    <div><dt>Attachment</dt><dd>{deleteImpact.impact.design_files} desain · {deleteImpact.impact.payment_files} payment · {deleteImpact.impact.sample_files} sample · {deleteImpact.impact.delivery_proofs} bukti kirim</dd></div>
                    <div><dt>Timeline</dt><dd>{deleteImpact.impact.timeline_entries} entri</dd></div>
                    <div><dt>Operator splits</dt><dd>{deleteImpact.impact.operator_splits}</dd></div>
                  </>
                )}
              </dl>
              <label className="delete-field">
                <span>Alasan penghapusan (wajib)</span>
                <textarea
                  rows={2}
                  value={deleteReason}
                  onChange={(e) => setDeleteReason(e.target.value)}
                  placeholder="Contoh: PO test / duplicate / salah input"
                  disabled={deleting}
                  data-testid="delete-reason-input"
                />
              </label>
              <label className="delete-field">
                <span>Konfirmasi — ketik nomor PO <code>{order.po_number}</code> atau kata <code>HAPUS</code></span>
                <input
                  type="text"
                  value={deleteConfirm}
                  onChange={(e) => setDeleteConfirm(e.target.value)}
                  placeholder={order.po_number}
                  disabled={deleting}
                  data-testid="delete-confirm-input"
                  autoComplete="off"
                />
              </label>
              {deleteError && (
                <div className="error" data-testid="delete-modal-error">{deleteError}</div>
              )}
            </div>
            <div className="delete-modal-actions">
              <button className="ghost" onClick={closeDeleteModal} disabled={deleting} data-testid="delete-modal-cancel">
                Batal
              </button>
              <button
                className="primary destructive"
                onClick={submitDeletePO}
                disabled={deleting || !deleteReason.trim() || !deleteConfirm.trim()}
                data-testid="delete-modal-confirm"
              >
                <Trash2 size={16}/> {deleting ? "Menghapus..." : "Hapus Permanen"}
              </button>
            </div>
          </div>
        </div>
      )}
      <OutletLocationModal
        open={!!locRow}
        row={locRow}
        canEdit={canSetLocation}
        onClose={() => setLocRow(null)}
        onSaved={() => refreshPO()}
      />
      <EditPoInfoModal
        open={editInfoOpen}
        order={order}
        onClose={() => setEditInfoOpen(false)}
        onSaved={(updated) => {
          if (updated && typeof updated === "object") {
            commitOrder({ ...(order || {}), ...updated });
          }
          refreshPO();
          showToast("Informasi PO diperbarui");
        }}
      />
      {proofViewerReceipt && (
        <InAppDocumentViewer
          documentKey={proofViewerReceipt.id}
          title={`Bukti ${proofViewerReceipt.receipt_number || "pembayaran"}`}
          fileName={proofViewerReceipt.payment_proof?.name || "bukti-pembayaran"}
          contentType={proofViewerReceipt.payment_proof?.content_type || ""}
          loadFile={loadReceiptProof}
          allowDownload
          onClose={() => setProofViewerReceipt(null)}
          testId="po-receipt-proof-viewer"
        />
      )}
    </div>
  );
}

function PaymentStatusBadge({ order, receipts = [] }) {
  const badge = paymentEvidenceBadge(order, receipts);
  const tone = badge.tone === "ok" ? "pill-ok" : badge.tone === "bypass" ? "pill-bypass" : "pill-warn";
  return <span className={`pill sm ${tone}`} data-testid="payment-status-badge">{badge.label}</span>;
}

function DesignStatusBadge({ order }) {
  if (order.design_bypassed) return <span className="pill sm pill-bypass" data-testid="design-status-badge">Design Bypass</span>;
  const files = order.design_files || [];
  if (order.design_status === "Approved" || isCustomerDesignApproved(order)) {
    return <span className="pill sm pill-ok" data-testid="design-status-badge">Design Approved</span>;
  }
  if (files.length === 0) return <span className="pill sm pill-warn" data-testid="design-status-badge">Belum Ada File Design</span>;
  return <span className="pill sm pill-warn" data-testid="design-status-badge">Menunggu Approval Design</span>;
}

function PaymentFilesList({ files, order, hasReceiptProof = false }) {
  if (!files || !files.length) {
    return (
      <p className="muted small" data-testid="payment-files-empty">
        {hasReceiptProof
          ? "Belum ada unggahan bukti di PO ini. Bukti Penerimaan Penjualan ada di panel Approval Payment."
          : "Belum ada bukti pembayaran. Silakan upload gambar (JPG/PNG) atau PDF."}
      </p>
    );
  }
  return (
    <>
      <ul className="payment-files" data-testid="payment-files-list">
        {files.map((f, i) => (
          <PaymentFileRow key={f.id || i} file={f} index={i + 1} orderId={order.id} />
        ))}
      </ul>
      {order.payment_status === "Approved" && (
        <div className="payment-approved-info" data-testid="payment-approved-info">
          <Check size={14}/> Approved oleh <b>{order.payment_approved_by_name || order.payment_approved_by}</b>
          {order.payment_approved_at && <> · {formatDate(order.payment_approved_at)}</>}
        </div>
      )}
    </>
  );
}

export function PaymentFileRow({ file, index, orderId }) {
  const url = paymentProofSource(file);
  const name = file?.name || "";
  const ct = String(file?.content_type || "");
  const isImage = paymentProofIsImage(file, url) || ct.startsWith("image/") || /\.(jpe?g|png|gif|webp)$/i.test(name);
  const isPdf = paymentProofIsPdf(file, url) || ct.includes("pdf") || /\.pdf$/i.test(name);
  const [open, setOpen] = useState(false);
  const openId = file.id || file.name;
  const loadFile = useCallback(async ({ signal } = {}) => {
    if (url && String(url).startsWith("data:")) return dataUrlToBlob(url);
    if (!orderId || !file?.id) {
      const error = new Error("missing");
      error.status = 404;
      throw error;
    }
    return api.getOrderAttachment(orderId, "payment", file.id, { signal });
  }, [url, orderId, file?.id]);

  return (
    <li className="payment-file" data-testid={`payment-file-${openId}`}>
      <div className="payment-file-thumb">
        {isImage || isPdf ? (
          <button
            type="button"
            className={isPdf ? "pdf-thumb" : "ghost sm"}
            onClick={() => setOpen(true)}
            data-testid={`payment-file-open-${openId}`}
          >
            <FileText size={22} /> {isPdf ? "PDF" : "Lihat"}
          </button>
        ) : (
          <span className="muted small">—</span>
        )}
      </div>

      <div className="payment-file-meta">
        <strong className="mono small">{index ? `${index}. ` : ""}{file.name}</strong>
        <span className="muted small">
          Diupload oleh {file.uploaded_by_name || file.uploaded_by}
        </span>
        {file.uploaded_at && (
          <span className="muted small">{formatDate(file.uploaded_at)}</span>
        )}
      </div>
      {open && (
        <InAppDocumentViewer
          documentKey={`${orderId || "po"}:${openId}`}
          title={file.name || "Bukti pembayaran"}
          fileName={file.name || "bukti-pembayaran"}
          contentType={ct}
          loadFile={loadFile}
          allowDownload
          onClose={() => setOpen(false)}
          testId={`payment-file-viewer-${openId}`}
        />
      )}
    </li>
  );
}

