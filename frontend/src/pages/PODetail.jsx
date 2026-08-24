import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertCircle, AlertTriangle, ArrowLeft, Check, ChevronRight, FileCheck2, FileText, MapPin, MessageSquare, Package, RefreshCw, RotateCcw, ShieldOff, Trash2, Truck, UserRound, X, XCircle, Zap } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { STAGES, canAdvance, formatDate, formatDeadline, ROLES } from "../lib/constants";
import Timeline from "../components/Timeline";
import OperatorSplitPanel from "../components/OperatorSplitPanel";
import PackingPanel from "../components/PackingPanel";
import SuratJalanPanel from "../components/SuratJalanPanel";
import AttachmentsPanel from "../components/AttachmentsPanel";
import DeliveryProofPanel from "../components/DeliveryProofPanel";
import CustomerApprovalPanel from "../components/CustomerApprovalPanel";
import DeliveryDeadlinePanel from "../components/DeliveryDeadlinePanel";
import HelperPrepPanel from "../components/HelperPrepPanel";
import ProductionRoutePanel from "../components/ProductionRoutePanel";
import StageBar from "../components/StageBar";
import useAutoRefresh from "../lib/useAutoRefresh";
import StageDetailDrawer from "../components/StageDetailDrawer";

export default function PODetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user, hasPerm } = useAuth();
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
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

  // Stage detail drawer (B3 — Req 8): clickable stages open a read-only drawer
  const [stageDrawer, setStageDrawer] = useState(null); // stage index or null

  // Payment upload state (Req wiring)
  const [payUploading, setPayUploading] = useState(false);
  const [payUploadErr, setPayUploadErr] = useState("");

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2500);
  };

  useEffect(() => {
    api.getOrder(id).then((d) => setOrder(d.order)).catch((e) => {
      // Workflow 06-12 bugfix: never setErr(object) — React akan crash render
      // dan menyebabkan halaman blank. Backend bisa return detail berupa object.
      const d = e.response?.data?.detail;
      if (typeof d === "string") setErr(d);
      else if (d && typeof d === "object") setErr(d.message || JSON.stringify(d));
      else setErr("Gagal memuat PO");
    }).finally(() => setLoading(false));
  }, [id]);

  // Batch 2: auto refresh 30s untuk PODetail — hanya update server-backed state
  // (order), tidak menyentuh state form lokal panel-panel (Helper draft,
  // Deadline draft, Notes, Screener draft, signature dsb). Panel-panel memakai
  // guard `useEffect([order.id, x.updated_at])` sehingga draft baru direset
  // kalau server-side timestamp berubah — draft belum-di-save tetap aman.
  const { lastUpdated: poLastUpdated, isPolling: poIsPolling, refreshNow: refreshPO } = useAutoRefresh(
    () => api.getOrder(id),
    { intervalMs: 30_000, enabled: !!order, onData: (d) => { if (d?.order) setOrder(d.order); } }
  );

  if (loading) return <div className="loading" data-testid="po-loading">Memuat detail PO...</div>;
  if (err) return <div className="error-page" data-testid="po-error" style={{whiteSpace:"pre-line"}}>{String(err)}</div>;
  if (!order) return null;

  const role = user?.role;
  const isCrm = role === "crm";
  const canAdvanceNow = !isCrm && canAdvance(role, order.current_stage);

  const run = async (fn, msg) => {
    setBusy(true);
    setErr("");
    try {
      const res = await fn();
      setOrder(res.order);
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

  const advance = () => run(() => api.advance(order.id, { note }), "Alur kerja diperbarui");
  const approveDesign = () => run(() => api.approveDesign(order.id, true, note), "Design di-approve");
  const rejectDesign = () => run(() => api.approveDesign(order.id, false, note), "Approval design dibatalkan");
  const approvePayment = () => run(() => api.approvePayment(order.id, true, note), "Payment di-approve");
  const rejectPayment = () => run(() => api.approvePayment(order.id, false, note), "Approval payment dibatalkan");
  const approveSample = () => run(() => api.approveSample(order.id, true, note), "Sampel di-approve");
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
      if (result?.order) setOrder(result.order);
      else {
        const d = await api.getOrder(order.id);
        setOrder(d.order);
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
  const packed = async () => {
    // Task 12 bugfix: proteksi klik tidak sengaja + konfirmasi eksplisit.
    // Sekarang wajib Qty Packing valid (bukan cuma prereq masuk Packing).
    const prereqReasons = order?.packing_prereq_reasons || [];
    const qtyPacked = order?.packing_info?.qty_packed;
    const hasQty = qtyPacked !== null && qtyPacked !== undefined && String(qtyPacked).trim() !== "" && Number(qtyPacked) > 0;
    if (prereqReasons.length > 0) {
      setErr(`Belum bisa Packing:\n• ${prereqReasons.join("\n• ")}`);
      return;
    }
    if (!hasQty) {
      setErr("Belum bisa Packing:\n• Qty Packing harus diisi terlebih dahulu.");
      return;
    }
    const shortage = Math.max((order.packing_required_total || 0) - Number(qtyPacked || 0), 0);
    const msg = shortage > 0
      ? `Lanjut packing sebagian (${qtyPacked} pcs, kurang ${shortage}) ke Surat Jalan? Packing penuh belum tercapai.`
      : "Konfirmasi Packing selesai dan PO siap masuk Surat Jalan?";
    if (!window.confirm(msg)) return;
    await run(() => api.markPacked(order.id), shortage > 0 ? "Packing sebagian — masuk Surat Jalan" : "Packing selesai — masuk Surat Jalan");
  };
  const delivered = () => {
    const packed = Number(order?.packing_info?.qty_packed || 0);
    const total = Number(order?.packing_required_total || order?.total_quantity || 0);
    const already = Number(order?.qty_delivered || 0);
    if (packed < total) {
      if (!window.confirm(`Tandai terkirim sebagian ${packed} pcs (dari total ${total})? PO belum selesai penuh.`)) return;
      return run(() => api.markDelivered(order.id, packed), `Terkirim sebagian ${packed} pcs`);
    }
    if (already > 0 && already < total) {
      if (!window.confirm(`Lengkapi pengiriman hingga ${packed} pcs?`)) return;
      return run(() => api.markDelivered(order.id, packed), "Pengiriman dilengkapi");
    }
    return run(() => api.markDelivered(order.id), "Ditandai sudah terkirim");
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

  const canApproveDesign = hasPerm("design.approve");
  const canApprovePayment = hasPerm("payment.approve");
  const canApproveSample = hasPerm("operator.sample_approve");
  const canUploadPayment = hasPerm("payment.upload");
  const canUploadDesign = hasPerm("design.upload");
  const canUploadSample = hasPerm("operator.sample_upload");
  const canManageUrgent = hasPerm("urgent.manage");
  const canManageBufferStock = hasPerm("buffer_stock.manage");
  const canEditSchedule = hasPerm("schedule.edit");
  const canMarkPacked = hasPerm("packing.update");
  const canMarkDelivered = hasPerm("delivery.update");
  const canManageSuratJalan = hasPerm("delivery_note.manage");
  const canBypassDesign = hasPerm("design.bypass_approval");
  const canBypassPayment = hasPerm("payment.bypass_approval");
  const canRollback = hasPerm("workflow.rollback");
  const canCancel = hasPerm("po.cancel");
  const canDelete = hasPerm("po.delete");
  const canHelperPrep = hasPerm("helper.prepare");
  const canScreenerUpdate = hasPerm("screen.update");
  const canViewCustomerApproval = hasPerm("customer_approval.view");
  const canManageCustomerApproval = hasPerm("customer_approval.manage");
  const canEditDeliveryDeadline = hasPerm("delivery.deadline.edit");
  const canEditRoute = hasPerm("production.route.edit");
  // "Read-only" hint hanya muncul jika user benar-benar tidak punya action perm apapun
  // di halaman ini — Effective Permission based, bukan role-based.
  const hasAnyActionPerm = (
    canApproveDesign || canApprovePayment || canUploadPayment || canUploadDesign ||
    canManageUrgent || canManageBufferStock || canEditSchedule || canMarkPacked || canMarkDelivered ||
    canBypassDesign || canBypassPayment || canRollback || canCancel || canDelete ||
    canHelperPrep || canScreenerUpdate || canAdvanceNow
  );

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
  const saveHelperPrep = async () => {
    const req = parseInt(document.querySelector('[data-testid="helper-qty-required"]').value || "0", 10);
    const prep = parseInt(document.querySelector('[data-testid="helper-qty-prepared"]').value || "0", 10);
    const notes = document.querySelector('[data-testid="helper-notes"]').value || "";
    if (!(req >= 0) || !(prep >= 0)) { alert("Qty tidak boleh negatif"); return; }
    await run(() => api.updateHelperPrep(order.id, { qty_required: req, qty_prepared: prep, notes }), "Persiapan Helper disimpan");
  };
  const saveScreener = async () => {
    const q = parseInt(document.querySelector('[data-testid="screener-qty"]').value || "0", 10);
    const notes = document.querySelector('[data-testid="screener-notes"]').value || "";
    if (!(q >= 0)) { alert("Qty tidak boleh negatif"); return; }
    await run(() => api.updateScreenerInfo(order.id, { screen_qty: q, notes }), "Info Screener disimpan");
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
    <div className="po-detail" data-testid="po-detail-page">
      <div className="detail-header">
        <button className="ghost sm" onClick={() => navigate(-1)} data-testid="back-button">
          <ArrowLeft size={16} /> Kembali
        </button>
        <div className="detail-title">
          <div className="po-line">
            <h1 data-testid="detail-po-number">{order.po_number}</h1>
            <span className={order.priority === "urgent" ? "urgent-badge" : "standard-badge"} data-testid="detail-priority">
              {order.priority === "urgent" ? "URGENT" : "NORMAL"}
            </span>
            {canManageUrgent && (
              <button className="ghost sm" onClick={toggleUrgent} disabled={busy} data-testid="toggle-urgent-button">
                <Zap size={14} /> {order.priority === "urgent" ? "Set normal" : "Set urgent"}
              </button>
            )}
            {order.buffer_stock && (
              <span className="pill sm" data-testid="buffer-stock-badge" style={{ background: "#e6ecf3", color: "#3a4b60" }}>
                BUFFER STOCK
              </span>
            )}
            {canManageBufferStock && (
              <button className="ghost sm" onClick={toggleBufferStock} disabled={busy} data-testid="toggle-buffer-stock-button">
                {order.buffer_stock ? "Unset Buffer Stock" : "Set Buffer Stock"}
              </button>
            )}
          </div>
          <h2 data-testid="detail-outlet">{order.outlet}</h2>
          <p className="subhead">
            Sedang: <strong data-testid="detail-stage">{STAGES[order.current_stage]}</strong>
            <span style={{ marginLeft: 12, color: "#7a6f52", fontSize: 12 }} data-testid="po-customer-line">
              · Customer: {order.customer ? <strong>{order.customer}</strong> : <em>Customer belum tersedia</em>}
            </span>
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4, fontSize: 11.5, color: "#7a6f52" }}>
            <button className="ghost sm" onClick={refreshPO} disabled={poIsPolling} data-testid="po-refresh-button" style={{ padding: "2px 8px", fontSize: 11 }}>
              <RefreshCw size={11} /> Refresh
            </button>
            <span data-testid="po-last-updated">
              Terakhir diperbarui: {poLastUpdated ? new Date(poLastUpdated).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }) : "-"}
            </span>
          </div>
        </div>
      </div>

      <StageBar stage={order.current_stage} order={order} onStageClick={(idx) => setStageDrawer(idx)} />

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

      {/* Advance controls */}
      <section className="advance-bar" data-testid="advance-bar">
        <input
          type="text"
          placeholder="Catatan (opsional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          data-testid="advance-note-input"
          disabled={isCrm}
        />
        <div className="advance-actions">
          {canApproveDesign && order.design_status !== "Approved" && (
            <button className="primary" onClick={approveDesign} disabled={busy} data-testid="approve-design-button">
              <FileCheck2 size={16} /> Approve Design
            </button>
          )}
          {canApproveDesign && order.design_status === "Approved" && order.current_stage < 4 && (
            <button className="ghost" onClick={rejectDesign} disabled={busy} data-testid="revoke-design-button">
              Batalkan approve design
            </button>
          )}
          {canApprovePayment && order.payment_status !== "Approved" && (
            <button className="primary" onClick={approvePayment} disabled={busy} data-testid="approve-payment-button">
              <FileCheck2 size={16} /> Approve Payment
            </button>
          )}
          {canApprovePayment && order.payment_status === "Approved" && order.current_stage < 6 && (
            <button className="ghost" onClick={rejectPayment} disabled={busy} data-testid="revoke-payment-button">
              Batalkan approve payment
            </button>
          )}
          {canBypassDesign && !order.design_bypassed && order.design_status !== "Approved" && (
            <button className="ghost" onClick={doBypassDesign} disabled={busy} data-testid="bypass-design-button" title="Butuh permission design.bypass_approval">
              <ShieldOff size={14}/> Bypass Design
            </button>
          )}
          {canBypassPayment && !order.payment_bypassed && order.payment_status !== "Approved" && (
            <button className="ghost" onClick={doBypassPayment} disabled={busy} data-testid="bypass-payment-button" title="Butuh permission payment.bypass_approval">
              <ShieldOff size={14}/> Bypass Payment
            </button>
          )}
          {canRollback && order.current_stage > 0 && (
            <button className="ghost" onClick={doRollback} disabled={busy} data-testid="rollback-button" title="Butuh permission workflow.rollback">
              <RotateCcw size={14}/> Rollback
            </button>
          )}
          {canAdvanceNow && order.current_stage < STAGES.length - 1 && (
            <button className="primary" onClick={advance} disabled={busy} data-testid="advance-button">
              Lanjut → {STAGES[order.current_stage + 1]} <ChevronRight size={16} />
            </button>
          )}
          {canMarkPacked && order.current_stage === 11 && (
            <button
              className="ghost"
              onClick={packed}
              disabled={busy || !order.packing_ready || !(order?.packing_info?.qty_packed > 0)}
              data-testid="mark-packed-button"
              data-packing-ready={order.packing_ready && (order?.packing_info?.qty_packed > 0) ? "true" : "false"}
              title={
                (order?.packing_info?.qty_packed > 0)
                  ? "Selesaikan Packing dan lanjut ke Surat Jalan"
                  : "Isi Qty Packing terlebih dahulu"
              }
            >
              <Package size={16} /> Selesai Packing
            </button>
          )}
          {canMarkDelivered && order.delivery_status !== "Terkirim" && order.current_stage >= 12 && (
            <button className="ghost" onClick={delivered} disabled={busy} data-testid="mark-delivered-button">
              <Truck size={16} /> Tandai Terkirim
            </button>
          )}
        </div>
        {err && <div className="error" data-testid="advance-error" style={{whiteSpace:"pre-line"}}>{err}</div>}
        {isCrm && !hasAnyActionPerm && <p className="muted small" data-testid="crm-readonly-note">Mode CRM read-only — Anda hanya dapat melihat progres.</p>}
      </section>

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

      {/* Aksi PO — Cancel / Delete (Req 5) */}
      {(canCancel || canDelete) && (
        <section className="panel po-actions-panel" data-testid="po-actions-panel">
          <header className="panel-head">
            <h3>Aksi PO</h3>
            <span className="muted small">Tindakan administratif untuk PO ini</span>
          </header>
          <div className="po-actions-row">
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
            <b>Batalkan PO</b> menyimpan seluruh histori & audit — PO dipindah ke tab <em>Cancelled</em>.
            <br/><b>Hapus Permanen</b> hanya untuk PO test/duplicate — data dihapus dari database, snapshot tetap ada di audit log.
          </p>
        </section>
      )}

      {/* Order Info */}
      <section className="panel info-grid" data-testid="detail-info">
        <div>
          <span className="lbl">PO / Sales Order</span>
          <strong>{order.po_number}</strong>
        </div>
        <div>
          <span className="lbl">Customer</span>
          <strong>{order.customer}</strong>
        </div>
        <div>
          <span className="lbl">Outlet</span>
          <strong>{order.outlet}</strong>
        </div>
        <div>
          <span className="lbl">Tanggal PO</span>
          <strong>{formatDate(order.order_date)}</strong>
        </div>
        <div>
          <span className="lbl">Deadline</span>
          <strong data-testid="detail-deadline">{formatDeadline(order.deadline)}</strong>
        </div>
        <div>
          <span className="lbl">Estimasi kirim</span>
          <strong>{formatDate(order.estimated_delivery)}</strong>
        </div>
        <div>
          <span className="lbl">Penerima</span>
          <strong>{order.recipient}</strong>
        </div>
        <div className="span-2">
          <span className="lbl"><MapPin size={12} /> Alamat</span>
          <strong>{order.address}</strong>
        </div>
        <div>
          <span className="lbl">Sales</span>
          <strong data-testid="detail-sales">
            {order.sales || order.sales_user || <em className="muted">Perlu verifikasi</em>}
          </strong>
        </div>
        <div>
          <span className="lbl">User / Pembuat PO</span>
          <strong data-testid="detail-created-by-user">
            {order.created_by_user || <em className="muted">Perlu verifikasi</em>}
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
        <div>
          <span className="lbl">Ekspedisi</span>
          <strong>{order.expedition}</strong>
        </div>
        <div>
          <span className="lbl">Zona kirim</span>
          <strong>{order.delivery_zone}</strong>
        </div>
        <div>
          <span className="lbl">Termin bayar</span>
          <strong>{order.payment_terms}</strong>
        </div>
        <div>
          <span className="lbl">Status Design</span>
          <strong className={order.design_status === "Approved" ? "status-ok" : "status-wait"} data-testid="design-status">
            {order.design_status === "Approved"
              ? "Approved"
              : (order.design_files?.length ? "Menunggu approval" : "Belum ada design")}
          </strong>
        </div>
        <div>
          <span className="lbl">Status Payment</span>
          <strong className={order.payment_status === "Approved" ? "status-ok" : "status-wait"} data-testid="payment-status">
            {order.payment_status === "Approved" ? "Approved" : "Menunggu approval"}
          </strong>
        </div>
        {order.urgency_approved_by && (
          <div>
            <span className="lbl">Urgent disetujui oleh</span>
            <strong>{order.urgency_approved_by}</strong>
          </div>
        )}
      </section>

      {/* Payment Panel — bukti + status + upload/approve/bypass (Wiring) */}
      <section className="panel payment-panel" data-testid="payment-panel">
        <header className="panel-head">
          <h3>Bukti Pembayaran</h3>
          <PaymentStatusBadge order={order} />
        </header>
        <div className="payment-body">
          <PaymentFilesList files={order.payment_files || []} order={order} />
          {payUploadErr && (
            <div className="error" data-testid="payment-upload-error">{payUploadErr}</div>
          )}
          <div className="payment-actions">
            {canUploadPayment && !order.cancelled && (
              <label className={"primary btn-file" + (payUploading ? " disabled" : "")} data-testid="upload-payment-label">
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/jpg,application/pdf"
                  disabled={payUploading}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = ""; // allow re-selecting same file
                    if (f) uploadPaymentProof(f);
                  }}
                  data-testid="upload-payment-input"
                  style={{display:"none"}}
                />
                <FileText size={16} /> {payUploading ? "Mengupload..." : (order.payment_files?.length ? "Ganti Bukti Pembayaran" : "Upload Bukti Pembayaran")}
              </label>
            )}
            {canApprovePayment && order.payment_files?.length > 0 && order.payment_status !== "Approved" && !order.payment_bypassed && (
              <button className="primary" onClick={approvePayment} disabled={busy} data-testid="approve-payment-inline-button">
                <FileCheck2 size={16} /> Approve Payment
              </button>
            )}
            {canApprovePayment && order.payment_status === "Approved" && order.current_stage < 6 && (
              <button className="ghost" onClick={rejectPayment} disabled={busy} data-testid="revoke-payment-inline-button">
                Batalkan approve payment
              </button>
            )}
            {!canUploadPayment && !canApprovePayment && !order.payment_bypassed && (
              <p className="muted small" data-testid="payment-readonly-note">Anda hanya dapat melihat bukti pembayaran (read-only).</p>
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
      </section>

      {/* Persiapan Helper (Batch 1 — per line item) */}
      <HelperPrepPanel order={order} canEdit={canHelperPrep} onUpdated={setOrder} />

      {/* Batch 3 — Production routing (Cetak/Non-Cetak) per line item */}
      <ProductionRoutePanel order={order} canEdit={canEditRoute} onUpdated={setOrder} />

      {/* Delivery deadline (Batch 1) */}
      <DeliveryDeadlinePanel order={order} canEdit={canEditDeliveryDeadline} onUpdated={setOrder} />

      {/* Info Screener (Req 10) */}
      <section className="panel" data-testid="screener-panel">
        <header className="panel-head"><h3>Info Screener</h3>
          <span className="muted small">Info operasional — tidak mengubah Qty PO</span>
        </header>
        <div className="prep-grid">
          <label>Jumlah Screen Dibuat
            <input type="number" min="0" defaultValue={order.screener_info?.screen_qty || 0} data-testid="screener-qty" disabled={!canScreenerUpdate}/>
          </label>
          <label className="span-2">Catatan Screener
            <input type="text" defaultValue={order.screener_info?.notes || ""} data-testid="screener-notes" disabled={!canScreenerUpdate}/>
          </label>
        </div>
        {canScreenerUpdate && (
          <div className="prep-actions">
            <button className="primary sm" onClick={saveScreener} disabled={busy} data-testid="screener-save-button"><Check size={13}/> Simpan Info Screener</button>
            {order.screener_info?.updated_at && <span className="muted small" data-testid="screener-updated">Update terakhir: {order.screener_info.updated_by_name || order.screener_info.updated_by} · {formatDate(order.screener_info.updated_at)}</span>}
          </div>
        )}
      </section>

      {/* Line items */}
      <section className="panel" data-testid="line-items-panel">
        <header className="panel-head">
          <h3>Line Item ({order.item_count ?? 0})</h3>
          <span className="pill" data-testid="scope-total-po">Total PO: {(order.total_quantity ?? 0).toLocaleString("id-ID")} pcs</span>
          <span className="pill sm" data-testid="scope-print-qty" style={{ background: "#e6ecf3", color: "#3a4b60" }}>
            Perlu Cetak: {Number(order.print_qty_total ?? 0).toLocaleString("id-ID")}
          </span>
          <span className="pill sm" data-testid="scope-nonprint-qty" style={{ background: "#efe6d3", color: "#7a6f52" }}>
            Tidak Cetak: {Math.max(Number(order.total_quantity ?? 0) - Number(order.print_qty_total ?? 0), 0).toLocaleString("id-ID")}
          </span>
        </header>
        <div className="line-items">
          {(order.line_items || []).map((li, i) => {
            const isPrint = (li.production_process || "print").toLowerCase() !== "non_print";
            return (
              <div key={i} className="line-item" data-testid={`line-item-${i}`}>
                <div>
                  <strong>{li.name}</strong>
                  <span className="mono">{li.item_code}</span>
                </div>
                <div className="li-meta">
                  <span><Package size={13} /> {(li.quantity ?? 0).toLocaleString("id-ID")} {li.unit}</span>
                  <span>Warna: {li.color}</span>
                  <span
                    className="pill sm"
                    data-testid={`line-item-process-${i}`}
                    style={{ background: isPrint ? "#e6ecf3" : "#efe6d3", color: isPrint ? "#3a4b60" : "#7a6f52" }}
                  >
                    {isPrint ? "Cetak" : "Non-Cetak"}
                  </span>
                </div>
                {li.notes && <p className="li-notes">Catatan sablon: {li.notes}</p>}
              </div>
            );
          })}
        </div>
      </section>

      {/* Operator splits */}
      <OperatorSplitPanel order={order} canEdit={canEditSchedule} onUpdated={setOrder} />

      {/* Task 12: Packing panel — Qty Packing input & Selesai Packing action */}
      {order.current_stage >= 8 && (
        <PackingPanel
          order={order}
          canManage={canMarkPacked}
          busy={busy}
          onSaveQty={async (qty, notes) => {
            await run(() => api.savePackingInfo(order.id, qty, notes), "Qty Packing disimpan");
          }}
          onComplete={packed}
        />
      )}

      {/* Task 13: Surat Jalan panel — source of truth SJ completion */}
      <SuratJalanPanel
        order={order}
        canManage={canManageSuratJalan}
        busy={busy}
        onCreate={async (number, notes) => {
          await run(() => api.createSuratJalan(order.id, number, notes), "Surat Jalan disimpan");
        }}
      />

      {/* Attachments */}
      <AttachmentsPanel
        order={order}
        user={user}
        onUpdated={setOrder}
        designSlot={(
          <div className="design-actions" data-testid="design-actions" style={{display:"flex",flexWrap:"wrap",gap:8,marginTop:10}}>
            <DesignStatusBadge order={order} />
            {canApproveDesign && (order.design_files?.length || 0) > 0 && order.design_status !== "Approved" && !order.design_bypassed && (
              <button className="primary" onClick={approveDesign} disabled={busy} data-testid="approve-design-inline-button">
                <FileCheck2 size={16} /> Approve Design
              </button>
            )}
            {canApproveDesign && order.design_status === "Approved" && order.current_stage < 4 && (
              <button className="ghost" onClick={rejectDesign} disabled={busy} data-testid="revoke-design-inline-button">
                Batalkan approve design
              </button>
            )}
            {canBypassDesign && !order.design_bypassed && order.design_status !== "Approved" && (
              <button className="ghost" onClick={doBypassDesign} disabled={busy} data-testid="bypass-design-inline-button" title="Butuh permission design.bypass_approval">
                <ShieldOff size={14}/> Bypass Design
              </button>
            )}
            {!canApproveDesign && !canBypassDesign && order.design_status !== "Approved" && !order.design_bypassed && (
              <p className="muted small" data-testid="design-readonly-note">Anda hanya dapat melihat file design (read-only).</p>
            )}
          </div>
        )}
        sampleSlot={(
          <div className="sample-actions" data-testid="sample-actions" style={{display:"flex",flexWrap:"wrap",gap:8,marginTop:10,alignItems:"center"}}>
            {order.sample_approved ? (
              <span className="pill sm" style={{background:"#e5f5e5",color:"#2b7a2b"}} data-testid="sample-status-badge">
                Sampel disetujui
                {order.sample_approved_by_name ? ` — ${order.sample_approved_by_name}` : ""}
              </span>
            ) : (order.sample_files?.length || 0) > 0 ? (
              <span className="pill sm" style={{background:"#fef4d6",color:"#a16d1f"}} data-testid="sample-status-badge">
                Menunggu approval sampel
              </span>
            ) : (
              <span className="pill sm" style={{background:"#f2eee7",color:"#7a6a4a"}} data-testid="sample-status-badge">
                Menunggu upload sampel
              </span>
            )}
            {canApproveSample && (order.sample_files?.length || 0) > 0 && !order.sample_approved && (
              <button className="primary" onClick={approveSample} disabled={busy} data-testid="approve-sample-button">
                <FileCheck2 size={16} /> Approve Sampel Cetak
              </button>
            )}
            {canApproveSample && order.sample_approved && order.current_stage < 10 && (
              <button className="ghost" onClick={rejectSample} disabled={busy} data-testid="revoke-sample-button">
                Batalkan approval sampel
              </button>
            )}
            {!canApproveSample && (order.sample_files?.length || 0) > 0 && !order.sample_approved && (
              <p className="muted small" data-testid="sample-readonly-note">Butuh permission <code>operator.sample_approve</code> untuk approve sampel cetak.</p>
            )}
          </div>
        )}
      />

      <CustomerApprovalPanel
        order={order}
        canView={canViewCustomerApproval}
        canManage={canManageCustomerApproval}
        onOrderUpdated={setOrder}
      />

      {/* Notes */}
      <section className="panel" data-testid="notes-panel">
        <header className="panel-head"><h3>Catatan Produksi</h3></header>
        {order.notes && <pre className="notes-view">{order.notes}</pre>}
        {!isCrm && (
          <div className="note-add">
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Tambahkan catatan..."
              rows={2}
              data-testid="note-input"
            />
            <button className="ghost sm" onClick={saveNote} disabled={busy || !note.trim()} data-testid="save-note-button">
              <MessageSquare size={14} /> Simpan catatan
            </button>
          </div>
        )}
      </section>

      {/* Delivery note & loading */}
      {order.current_stage >= 11 && (
        <section className="panel" data-testid="delivery-panel">
          <header className="panel-head">
            <h3>Surat Jalan &amp; Loading</h3>
            <Link to={`/po/${order.id}/surat-jalan`} className="ghost sm" data-testid="open-surat-jalan-button">
              <FileText size={14} /> Buka Surat Jalan
            </Link>
          </header>
          <div className="delivery-block">
            <div><span className="lbl"><FileText size={12} /> No. Surat Jalan</span><strong>{order.delivery_note_number || "-"}</strong></div>
            <div><span className="lbl">Status Packing</span><strong>{order.packing_status === "Sudah" ? "Sudah Dipacking" : "Belum Dipacking"}</strong></div>
            <div><span className="lbl">Status Kirim</span><strong data-testid="delivery-status">{order.delivery_status}</strong></div>
            {order.loading_checklist && (
              <div className="span-2">
                <span className="lbl">Checklist Loading</span>
                <p>Kendaraan: {order.loading_checklist.vehicle_plate || "-"} · Barang: {order.loading_checklist.items_loaded ? "OK" : "Belum"} · Seal: {order.loading_checklist.seal_ok ? "OK" : "Belum"}</p>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Delivery proof (from stage Surat Jalan onward) */}
      {order.current_stage >= 12 && (
        <DeliveryProofPanel order={order} onUpdated={setOrder} />
      )}

      <Timeline timeline={order.timeline} currentStage={order.current_stage} order={order} />

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
      {stageDrawer != null && (
        <StageDetailDrawer order={order} stageIdx={stageDrawer} onClose={() => setStageDrawer(null)} />
      )}
    </div>
  );
}

function PaymentStatusBadge({ order }) {
  if (order.payment_bypassed) return <span className="pill sm pill-bypass" data-testid="payment-status-badge">Payment Bypass</span>;
  const files = order.payment_files || [];
  if (order.payment_status === "Approved") return <span className="pill sm pill-ok" data-testid="payment-status-badge">Payment Approved</span>;
  if (files.length === 0) return <span className="pill sm pill-warn" data-testid="payment-status-badge">Belum Ada Bukti Payment</span>;
  return <span className="pill sm pill-warn" data-testid="payment-status-badge">Menunggu Approval Payment</span>;
}

function DesignStatusBadge({ order }) {
  if (order.design_bypassed) return <span className="pill sm pill-bypass" data-testid="design-status-badge">Design Bypass</span>;
  const files = order.design_files || [];
  if (order.design_status === "Approved") return <span className="pill sm pill-ok" data-testid="design-status-badge">Design Approved</span>;
  if (files.length === 0) return <span className="pill sm pill-warn" data-testid="design-status-badge">Belum Ada File Design</span>;
  return <span className="pill sm pill-warn" data-testid="design-status-badge">Menunggu Approval Design</span>;
}

function PaymentFilesList({ files, order }) {
  if (!files || !files.length) {
    return <p className="muted small" data-testid="payment-files-empty">Belum ada bukti pembayaran. Silakan upload gambar (JPG/PNG) atau PDF.</p>;
  }
  return (
    <>
      <ul className="payment-files" data-testid="payment-files-list">
        {files.map((f, i) => (
          <PaymentFileRow key={f.id || i} file={f} />
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

function PaymentFileRow({ file }) {
  const url = file.data_url || file.url;
  const isImage = url && (url.startsWith("data:image/") || /\.(jpe?g|png|gif|webp)$/i.test(file.name || ""));
  const isPdf = url && (url.startsWith("data:application/pdf") || /\.pdf$/i.test(file.name || ""));

  const handlePdfOpen = (e) => {
    if (!isPdf || !url?.startsWith("data:application/pdf")) return;

    e.preventDefault();

    try {
      const commaIndex = url.indexOf(",");
      if (commaIndex < 0) throw new Error("Invalid PDF data URL");

      const encoded = url.slice(commaIndex + 1);
      const binary = atob(encoded);
      const bytes = new Uint8Array(binary.length);

      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }

      const blob = new Blob([bytes], { type: "application/pdf" });
      const blobUrl = URL.createObjectURL(blob);

      window.open(blobUrl, "_blank");

      // Beri waktu tab baru selesai membaca Blob sebelum URL dilepas.
      setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
    } catch (err) {
      console.error("Gagal membuka PDF:", err);
      alert("PDF gagal dibuka. Silakan coba lagi.");
    }
  };

  return (
    <li className="payment-file" data-testid={`payment-file-${file.id || file.name}`}>
      <div className="payment-file-thumb">
        {isImage ? (
          <a href={url} target="_blank" rel="noreferrer">
            <img src={url} alt={file.name} />
          </a>
        ) : isPdf ? (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="pdf-thumb"
            onClick={handlePdfOpen}
          >
            <FileText size={22} /> PDF
          </a>
        ) : (
          <span className="muted small">—</span>
        )}
      </div>

      <div className="payment-file-meta">
        <strong className="mono small">{file.name}</strong>
        <span className="muted small">
          Diupload oleh {file.uploaded_by_name || file.uploaded_by}
        </span>
        {file.uploaded_at && (
          <span className="muted small">{formatDate(file.uploaded_at)}</span>
        )}
      </div>
    </li>
  );
}

