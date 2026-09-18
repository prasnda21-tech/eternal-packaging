import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { Copy, FileText, MapPin, Package, Pencil, Printer, RotateCcw, Trash2, Truck, XCircle, Zap } from "lucide-react";

const MENU_WIDTH = 420;
const MENU_GAP = 8;

function menuBoxFromTrigger(el, height = 420) {
  if (!el || typeof window === "undefined") return null;
  const rect = el.getBoundingClientRect();
  const width = Math.min(MENU_WIDTH, Math.max(380, Math.min(440, window.innerWidth - 16)));
  const left = Math.max(8, Math.min(rect.left - width - MENU_GAP, window.innerWidth - width - 8));
  const top = Math.min(
    Math.max(8, rect.top),
    Math.max(8, window.innerHeight - height - 8),
  );
  return { top: Math.round(top), left: Math.round(left), width };
}

function menuItemsOf(root) {
  return root ? Array.from(root.querySelectorAll('[role="menuitem"]')) : [];
}

/**
 * Compact planning popover. Trigger lives on the right rail — never a command bar or page.
 */
export default function PoGlobalCommandBar({
  triggerRef,
  canEditPoInfo,
  cancelled,
  onEditInfo,
  canSetLocation,
  hasOutlet,
  onEditLocation,
  canRepeatOrder,
  orderId,
  canManageUrgent,
  isUrgent,
  onToggleUrgent,
  busy,
  canManageBufferStock,
  isBufferStock,
  onToggleBufferStock,
  canCopyTrackingLink,
  onCopyTracking,
  trackingBusy,
  trackingCopyMsg,
  canProcessSalesDelivery,
  salesDeliveryAllQtyDone,
  onPrint,
  planOpen,
  onClosePlan,
  canCancel,
  onCancelPo,
  canDelete,
  onDeletePo,
  deleting,
  canRollback,
  currentStage,
  onRollback,
}) {
  const menuRef = useRef(null);
  const closeRef = useRef(() => {});
  const [menuBox, setMenuBox] = useState(null);
  closeRef.current = () => {
    onClosePlan?.();
    triggerRef?.current?.focus?.();
  };

  useLayoutEffect(() => {
    if (!planOpen) {
      setMenuBox(null);
      return undefined;
    }
    const sync = () => {
      const measured = menuRef.current?.offsetHeight || 420;
      setMenuBox(menuBoxFromTrigger(triggerRef?.current, measured));
    };
    sync();
    window.addEventListener("resize", sync);
    window.addEventListener("scroll", sync, true);
    return () => {
      window.removeEventListener("resize", sync);
      window.removeEventListener("scroll", sync, true);
    };
  }, [planOpen, triggerRef]);

  useEffect(() => {
    if (!planOpen) return undefined;
    const onDoc = (event) => {
      const target = event.target;
      if (triggerRef?.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      closeRef.current();
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      }
    };
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("keydown", onKey);
    };
  }, [planOpen, triggerRef]);

  useEffect(() => {
    if (!planOpen || !menuRef.current) return undefined;
    const first = menuItemsOf(menuRef.current)[0];
    first?.focus();
    return undefined;
  }, [planOpen, menuBox]);

  if (!planOpen || !menuBox || typeof document === "undefined") return null;

  const closeThen = (fn) => () => {
    closeRef.current();
    fn?.();
  };

  const onMenuKeyDown = (event) => {
    const items = menuItemsOf(menuRef.current);
    if (!items.length) return;
    const index = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      event.preventDefault();
      items[(index + 1 + items.length) % items.length].focus();
    } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      event.preventDefault();
      items[(index - 1 + items.length) % items.length].focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      items[0].focus();
    } else if (event.key === "End") {
      event.preventDefault();
      items[items.length - 1].focus();
    }
  };

  return createPortal(
    <div
      ref={menuRef}
      className="po-plan-menu"
      role="menu"
      data-testid="po-plan-actions-menu"
      style={{ top: menuBox.top, left: menuBox.left, width: menuBox.width }}
      onKeyDown={onMenuKeyDown}
    >
      <div className="po-plan-menu-grid">
        <section className="po-plan-group" data-testid="po-plan-group-info">
          <span className="po-plan-heading">Informasi</span>
          {canEditPoInfo && !cancelled && (
            <button type="button" role="menuitem" data-testid="po-edit-info" onClick={closeThen(onEditInfo)}>
              <Pencil size={14} /> Edit Informasi
            </button>
          )}
          {canSetLocation && (
            <button type="button" role="menuitem" data-testid="po-atur-lokasi" onClick={closeThen(onEditLocation)}>
              <MapPin size={14} /> {hasOutlet ? "Edit Lokasi" : "Atur Lokasi"}
            </button>
          )}
        </section>
        <section className="po-plan-group" data-testid="po-plan-group-plan">
          <span className="po-plan-heading">Perencanaan</span>
          {canRepeatOrder && !cancelled && (
            <Link
              to={`/penjualan/pesanan-penjualan?repeat_order_id=${encodeURIComponent(orderId)}`}
              role="menuitem"
              data-testid="btn-repeat-order-detail"
              title="Buat pesanan baru dari PO ini"
              onClick={onClosePlan}
            >
              <RotateCcw size={14} /> Repeat Order
            </Link>
          )}
          {canManageUrgent && (
            <button type="button" role="menuitem" onClick={closeThen(onToggleUrgent)} disabled={busy} data-testid="toggle-urgent-button">
              <Zap size={14} /> {isUrgent ? "Set Normal" : "Set Urgent"}
            </button>
          )}
          {canManageBufferStock && (
            <button type="button" role="menuitem" onClick={closeThen(onToggleBufferStock)} disabled={busy} data-testid="toggle-buffer-stock-button">
              <Package size={14} /> {isBufferStock ? "Unset Buffer Stock" : "Buffer Stock"}
            </button>
          )}
          {canCopyTrackingLink && (
            <button
              type="button"
              role="menuitem"
              onClick={closeThen(onCopyTracking)}
              disabled={trackingBusy || cancelled}
              data-testid="copy-tracking-link"
            >
              <Copy size={14} /> Live Tracking
              {trackingCopyMsg ? <span className="muted small" data-testid="tracking-copy-msg">{trackingCopyMsg}</span> : null}
            </button>
          )}
          {canProcessSalesDelivery ? (
            <Link
              to={`/penjualan/pengiriman-pesanan?po_id=${orderId}&action=create`}
              role="menuitem"
              data-testid="btn-proses-pengiriman-pesanan"
              onClick={onClosePlan}
            >
              <Truck size={14} /> Buat Pengiriman Pesanan
            </Link>
          ) : salesDeliveryAllQtyDone ? (
            <span className="po-plan-menu-done" data-testid="badge-pengiriman-selesai">
              Seluruh Qty Selesai Dibuatkan SJ
            </span>
          ) : null}
        </section>
        <section className="po-plan-group po-plan-group-span" data-testid="po-plan-group-docs">
          <span className="po-plan-heading">Dokumen</span>
          <div className="po-plan-two-col">
            <button type="button" role="menuitem" data-testid="btn-print-po" onClick={closeThen(onPrint)}>
              <Printer size={14} /> Cetak PO
            </button>
            <Link
              to={`/penjualan/faktur-penjualan?po_id=${orderId}&action=create`}
              role="menuitem"
              data-testid="btn-buat-faktur-penjualan"
              onClick={onClosePlan}
            >
              <FileText size={14} /> Buat Faktur
            </Link>
          </div>
        </section>
      </div>
      {(canRollback || canCancel || canDelete) && (
        <section className="po-plan-danger" data-testid="po-actions-danger-body">
          <span className="po-plan-heading">Aksi Berbahaya / Pengecualian</span>
          {canRollback && currentStage > 0 && (
            <button type="button" role="menuitem" onClick={closeThen(onRollback)} disabled={busy} data-testid="rollback-button" title="Butuh permission workflow.rollback">
              <RotateCcw size={14} /> Rollback
            </button>
          )}
          {canCancel && !cancelled && (
            <button
              type="button"
              role="menuitem"
              className="is-warning"
              onClick={closeThen(onCancelPo)}
              disabled={busy}
              data-testid="cancel-po-button"
              title="Menyimpan histori dan audit. Butuh permission po.cancel"
            >
              <XCircle size={14} /> Batalkan PO
              <span className="po-plan-hint">Menyimpan histori dan audit.</span>
            </button>
          )}
          {canDelete && (
            <button
              type="button"
              role="menuitem"
              className="is-danger"
              onClick={closeThen(onDeletePo)}
              disabled={busy || deleting}
              data-testid="delete-po-button"
              title="Khusus PO test/duplicate. Butuh permission po.delete"
            >
              <Trash2 size={14} /> Hapus Permanen PO
              <span className="po-plan-hint">Khusus PO test/duplicate.</span>
            </button>
          )}
        </section>
      )}
    </div>,
    document.body,
  );
}
