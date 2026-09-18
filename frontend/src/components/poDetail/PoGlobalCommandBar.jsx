import { Link } from "react-router-dom";
import { ArrowLeft, ChevronDown, FileText, List, MapPin, MoreVertical, Pencil, Printer, RefreshCw, RotateCcw, Zap } from "lucide-react";

/**
 * Global PO actions. Same buttons on every workflow — never duplicated in stage workspaces.
 */
export default function PoGlobalCommandBar({
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
  onPrint,
  moreOpen,
  onToggleMore,
  onRefresh,
  refreshing,
  onSummary,
  onBack,
}) {
  return (
    <nav className="po-command-bar" aria-label="Aksi utama PO" data-testid="po-command-bar">
      <div className="po-command-group">
        <span className="po-command-label">Informasi PO</span>
        <div className="po-command-buttons">
          {canEditPoInfo && !cancelled && (
            <button type="button" className="ghost sm" data-testid="po-edit-info" onClick={onEditInfo}>
              <Pencil size={14} /> Edit Informasi
            </button>
          )}
          {canSetLocation && (
            <button type="button" className="ghost sm" data-testid="po-atur-lokasi" onClick={onEditLocation}>
              <MapPin size={14} /> {hasOutlet ? "Edit Lokasi" : "Atur Lokasi"}
            </button>
          )}
        </div>
      </div>
      <div className="po-command-group">
        <span className="po-command-label">Perencanaan</span>
        <div className="po-command-buttons">
          {canRepeatOrder && !cancelled && (
            <Link to={`/penjualan/pesanan-penjualan?repeat_order_id=${encodeURIComponent(orderId)}`} className="ghost sm" data-testid="btn-repeat-order-detail" title="Buat pesanan baru dari PO ini">
              <RotateCcw size={14} /> Repeat Order
            </Link>
          )}
          {canManageUrgent && (
            <button type="button" className="ghost sm" onClick={onToggleUrgent} disabled={busy} data-testid="toggle-urgent-button">
              <Zap size={14} /> {isUrgent ? "Set Normal" : "Set Urgent"}
            </button>
          )}
          {canManageBufferStock && (
            <button type="button" className="ghost sm" onClick={onToggleBufferStock} disabled={busy} data-testid="toggle-buffer-stock-button">
              {isBufferStock ? "Unset Buffer Stock" : "Buffer Stock"}
            </button>
          )}
        </div>
      </div>
      <div className="po-command-group">
        <span className="po-command-label">Dokumen</span>
        <div className="po-command-buttons">
          <button type="button" className="ghost sm" data-testid="btn-print-po" onClick={onPrint}>
            <Printer size={14} /> Cetak PO
          </button>
          <Link to={`/penjualan/faktur-penjualan?po_id=${orderId}&action=create`} className="ghost sm" data-testid="btn-buat-faktur-penjualan">
            <FileText size={14} /> Buat Faktur
          </Link>
        </div>
      </div>
      <div className="po-command-group po-command-more">
        <span className="po-command-label">Lainnya</span>
        <button type="button" className="ghost sm" aria-expanded={moreOpen} data-testid="po-more-actions" onClick={onToggleMore}>
          <MoreVertical size={15} /> <ChevronDown size={13} />
        </button>
        {moreOpen && (
          <div className="po-command-menu" role="menu" data-testid="po-more-actions-menu">
            <button type="button" role="menuitem" data-testid="po-refresh-button" onClick={onRefresh} disabled={refreshing}>
              <RefreshCw size={14} /> Refresh data
            </button>
            <button type="button" role="menuitem" data-testid="btn-po-summary" onClick={onSummary}>
              <List size={14} /> Ringkasan PO
            </button>
            <button type="button" role="menuitem" data-testid="back-button" onClick={onBack}>
              <ArrowLeft size={14} /> Kembali ke Daftar PO
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
