import { useState } from "react";
import { Check, RefreshCw, X } from "lucide-react";
import api from "../lib/api";
import MasterItemSearchSelect, { formatMasterItemLabel } from "./MasterItemSearchSelect";

/**
 * Compact line actions: Ubah Item and Ubah Qty are separate capabilities.
 * Product replacement stays on the existing lock. Quantity correction uses
 * the committed floor returned by the order, then the backend rechecks on save.
 */
export function replaceEligibilityForLine(order, lineIdx) {
  const rows = order?.line_product_replace;
  if (Array.isArray(rows)) {
    const hit = rows.find((r) => Number(r?.line_idx) === Number(lineIdx));
    if (hit) return hit;
  }
  return null;
}

export function isLineProductReplaceAllowed(order, lineIdx) {
  const el = replaceEligibilityForLine(order, lineIdx);
  if (el && typeof el.allowed === "boolean") return el.allowed;
  const li = (order?.line_items || [])[lineIdx];
  if (!li) return false;
  const isPrint = String(li.production_process || "print").toLowerCase() !== "non_print";
  if (isPrint && (order?.sample_files || []).length > 0) return false;
  const idx = Number(lineIdx);
  for (const ship of order?.shipments || []) {
    if ((ship?.lines || []).some((ln) => Number(ln?.line_idx) === idx)) return false;
  }
  for (const lc of order?.loading_confirmations || []) {
    if ((lc?.lines || []).some((ln) => Number(ln?.line_idx) === idx)) return false;
  }
  for (const ev of order?.delivery_confirmations || []) {
    const items = ev?.items || ev?.lines || [];
    if (items.some((ln) => Number(ln?.line_idx) === idx)) return false;
  }
  return true;
}

export function isLineQtyEditAllowed(order, lineIdx) {
  if (order?.cancelled) return false;
  const el = replaceEligibilityForLine(order, lineIdx);
  if (el && typeof el.qty_edit_allowed === "boolean") return el.qty_edit_allowed;
  return true;
}

export const QTY_DELIVERED_MESSAGE = "Qty tidak dapat ditambah karena pesanan sudah terkirim lengkap.";

export const QTY_INCREASE_WARNINGS = [
  "Qty tambahan belum masuk Penjadwalan Operator.",
  "Stok tambahan akan diperiksa saat proses produksi.",
  "Faktur yang sudah ada tidak berubah; tambahan qty menjadi sisa pesanan.",
  "Harga tambahan mengikuti harga pada Pesanan Penjualan ini.",
];

export function qtyIncreaseAllowed(order, lineIdx) {
  const el = replaceEligibilityForLine(order, lineIdx);
  if (el && typeof el.qty_increase_allowed === "boolean") return el.qty_increase_allowed;
  return true;
}

export function qtyEditBounds(order, lineIdx) {
  const el = replaceEligibilityForLine(order, lineIdx) || {};
  const li = (order?.line_items || [])[lineIdx] || {};
  const current = Number.isInteger(el.current_qty) ? el.current_qty : Number(li.quantity || 0);
  const committed = Number.isInteger(el.committed_qty) ? el.committed_qty : 0;
  const increaseAllowed = qtyIncreaseAllowed(order, lineIdx);
  const max = increaseAllowed ? null : (Number.isInteger(el.max_qty) ? el.max_qty : current);
  const min = Number.isInteger(el.min_qty) ? el.min_qty : committed;
  const remaining = Number.isInteger(el.editable_remaining)
    ? el.editable_remaining
    : Math.max(0, current - committed);
  const increaseReason = el.qty_increase_reason || (increaseAllowed ? "" : QTY_DELIVERED_MESSAGE);
  return { current, committed, remaining, min, max, increaseAllowed, increaseReason };
}

export function formatQtyId(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("id-ID");
}

export function lockReasonForLine(order, lineIdx) {
  const el = replaceEligibilityForLine(order, lineIdx);
  if (el?.reason_message) return el.reason_message;
  if (el?.reason_code === "ITEM_SUDAH_MASUK_SAMPEL_CETAK") {
    return "Tidak dapat diganti — sudah masuk Sampel Cetak";
  }
  if (el?.reason_code === "ITEM_SUDAH_DIPAKAI_DI_PROSES_LANJUTAN") {
    return "Tidak dapat diganti — sudah dipakai pada proses berikutnya";
  }
  const li = (order?.line_items || [])[lineIdx];
  const isPrint = li && String(li.production_process || "print").toLowerCase() !== "non_print";
  if (isPrint && (order?.sample_files || []).length > 0) {
    return "Tidak dapat diganti — sudah masuk Sampel Cetak";
  }
  return "Tidak dapat diganti — sudah dipakai pada proses berikutnya";
}

export function formatReplaceApiError(err) {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object") return d.message || d.code || JSON.stringify(d);
  return err?.message || "Gagal mengubah item PO";
}

export function parsePositiveQty(value) {
  const raw = String(value ?? "").trim();
  if (!/^\d+$/.test(raw)) return null;
  const qty = Number(raw);
  return Number.isSafeInteger(qty) && qty > 0 ? qty : null;
}

export function isSameCanonicalProduct(line, target) {
  if (!target) return true;
  const lineMasterId = line?.master_item_id || line?.product_id || line?.item_id || null;
  const targetMasterId = target?.id || target?.master_item_id || null;
  const lineAccurateId = line?.accurate_item_id || null;
  const targetAccurateId = target?.accurate_item_id || null;
  if (lineMasterId && targetMasterId) return String(lineMasterId) === String(targetMasterId);
  if (lineAccurateId && targetAccurateId) return String(lineAccurateId) === String(targetAccurateId);
  return false;
}

export function hasLineItemChanges(line, target, qtyValue) {
  const qty = parsePositiveQty(qtyValue);
  const currentQty = Number(line?.quantity || 0);
  return !isSameCanonicalProduct(line, target) || (qty !== null && qty !== currentQty);
}

export default function LineProductReplaceControl({
  order,
  lineIdx,
  canEdit,
  onUpdated,
}) {
  const [productOpen, setProductOpen] = useState(false);
  const [qtyOpen, setQtyOpen] = useState(false);
  const [target, setTarget] = useState(null);
  const [quantity, setQuantity] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [qtyErr, setQtyErr] = useState("");
  const [confirmIncrease, setConfirmIncrease] = useState(false);

  const li = (order?.line_items || [])[lineIdx];
  if (!li) return null;

  const productAllowed = Boolean(canEdit) && isLineProductReplaceAllowed(order, lineIdx);
  const qtyAllowed = Boolean(canEdit) && isLineQtyEditAllowed(order, lineIdx);
  const productLocked = !isLineProductReplaceAllowed(order, lineIdx);
  const reason = productLocked ? lockReasonForLine(order, lineIdx) : "";
  const bounds = qtyEditBounds(order, lineIdx);

  const openProduct = () => {
    setErr("");
    setTarget(null);
    setProductOpen(true);
  };

  const closeProduct = () => {
    if (saving) return;
    setProductOpen(false);
    setTarget(null);
    setErr("");
  };

  const openQty = () => {
    setQtyErr("");
    setConfirmIncrease(false);
    setQuantity(String(bounds.current || li.quantity || ""));
    setQtyOpen(true);
  };

  const closeQty = () => {
    if (saving) return;
    setQtyOpen(false);
    setQuantity("");
    setQtyErr("");
    setConfirmIncrease(false);
  };

  const saveProduct = async () => {
    if (saving || !target) return;
    const mid = target?.id || target?.master_item_id || null;
    const aid = target?.accurate_item_id || null;
    if (!mid && !aid) {
      setErr("Pilih produk dengan identitas kanonis.");
      return;
    }
    setSaving(true);
    setErr("");
    try {
      const res = await api.replaceSalesOrderLineProduct(order.id, lineIdx, {
        master_item_id: mid,
        accurate_item_id: aid,
      });
      if (res?.order && onUpdated) onUpdated(res.order);
      setProductOpen(false);
      setTarget(null);
    } catch (e) {
      setErr(formatReplaceApiError(e));
    } finally {
      setSaving(false);
    }
  };

  const parsedQty = parsePositiveQty(quantity);
  const increasing = parsedQty !== null && parsedQty > bounds.current;
  const increaseBlocked = increasing && !bounds.increaseAllowed;
  const addedQty = increasing ? parsedQty - bounds.current : 0;

  const saveQty = async () => {
    if (saving) return;
    const qty = parsePositiveQty(quantity);
    if (qty === null) {
      setQtyErr("Qty baru wajib berupa angka bulat lebih dari 0.");
      return;
    }
    if (qty > bounds.current && !bounds.increaseAllowed) {
      setQtyErr(bounds.increaseReason || QTY_DELIVERED_MESSAGE);
      return;
    }
    if (qty > bounds.current && !confirmIncrease) {
      setConfirmIncrease(true);
      return;
    }
    setSaving(true);
    setQtyErr("");
    try {
      const res = await api.replaceSalesOrderLineProduct(order.id, lineIdx, {
        quantity: qty,
      });
      if (res?.order && onUpdated) onUpdated(res.order);
      setQtyOpen(false);
      setQuantity("");
    } catch (e) {
      setQtyErr(formatReplaceApiError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="line-product-replace" data-testid={`line-product-replace-${lineIdx}`}>
      {canEdit ? (
        <div className="line-product-replace-actions">
          {productAllowed ? (
            <button
              type="button"
              className="ghost sm"
              onClick={openProduct}
              data-testid={`ganti-barang-btn-${lineIdx}`}
            >
              <RefreshCw size={13} /> Ubah Item
            </button>
          ) : null}
          {qtyAllowed ? (
            <button
              type="button"
              className="ghost sm"
              onClick={openQty}
              data-testid={`ubah-qty-btn-${lineIdx}`}
            >
              Ubah Qty
            </button>
          ) : replaceEligibilityForLine(order, lineIdx)?.qty_edit_reason ? (
            <span className="muted small" data-testid={`ubah-qty-locked-${lineIdx}`}>
              {replaceEligibilityForLine(order, lineIdx).qty_edit_reason}
            </span>
          ) : null}
        </div>
      ) : null}
      {productLocked ? (
        <span
          className="muted small line-product-replace-lock"
          data-testid={`ganti-barang-locked-${lineIdx}`}
          title={reason}
        >
          {reason}
        </span>
      ) : null}

      {productOpen && (
        <div className="modal" data-testid={`ganti-barang-modal-${lineIdx}`} role="dialog">
          <div className="modal-inner line-item-edit-modal">
            <header className="modal-head">
              <div>
                <h3>Ubah Item PO</h3>
                <p className="muted small">Ganti barang item ini. Qty diubah dari tombol terpisah.</p>
              </div>
              <button type="button" className="ghost sm" onClick={closeProduct} disabled={saving} aria-label="Tutup">
                <X size={16} />
              </button>
            </header>
            <div className="line-item-edit-body">
              <div className="line-item-edit-current" data-testid={`ganti-barang-current-${lineIdx}`}>
                <div className="line-item-edit-section-label">Saat Ini</div>
                <div>
                  <span className="muted small">Barang</span>
                  <strong>{li.name || "—"}</strong>
                  {li.item_code ? <span className="mono small">{li.item_code}</span> : null}
                </div>
              </div>

              <div data-testid={`ganti-barang-search-${lineIdx}`}>
                <label className="line-item-edit-label">Barang Pengganti</label>
                <MasterItemSearchSelect
                  value={target}
                  onChange={setTarget}
                  disabled={saving}
                  status="active"
                  placeholder="Cari nama atau kode barang…"
                  emptyLabel="Produk tidak ditemukan"
                  testId={`ganti-barang-picker-${lineIdx}`}
                  fetchItems={(params) => api.salesCatalogProducts(params)}
                />
              </div>

              <div className="line-item-edit-preview" data-testid={`ganti-barang-target-${lineIdx}`}>
                <div className="line-item-edit-section-label">Setelah Disimpan</div>
                <div className="line-item-edit-preview-row">
                  <div>
                    <strong>{target ? formatMasterItemLabel(target) : (li.name || "—")}</strong>
                  </div>
                  <Check size={18} aria-hidden="true" />
                </div>
              </div>

              {err && (
                <p className="error small" data-testid={`ganti-barang-error-${lineIdx}`}>{err}</p>
              )}
            </div>
            <footer className="line-item-edit-footer">
              <button type="button" className="ghost" onClick={closeProduct} disabled={saving}>
                Batal
              </button>
              <button
                type="button"
                className="primary"
                onClick={saveProduct}
                disabled={saving || !target}
                data-testid={`ganti-barang-save-${lineIdx}`}
              >
                {saving ? "Menyimpan…" : "Simpan Item"}
              </button>
            </footer>
          </div>
        </div>
      )}

      {qtyOpen && (
        <div className="modal" data-testid={`ubah-qty-modal-${lineIdx}`} role="dialog">
          <div className="modal-inner line-item-edit-modal line-qty-edit-modal">
            <header className="modal-head">
              <div>
                <h3>Ubah Qty</h3>
                <p className="muted small">{li.name || "Item pesanan"}</p>
              </div>
              <button type="button" className="ghost sm" onClick={closeQty} disabled={saving} aria-label="Tutup">
                <X size={16} />
              </button>
            </header>
            <div className="line-item-edit-body">
              <dl className="line-qty-edit-facts">
                <dt>Qty Pesanan</dt>
                <dd data-testid={`ubah-qty-current-${lineIdx}`}>{formatQtyId(bounds.current)}</dd>
                <dt>Sudah Terikat</dt>
                <dd data-testid={`ubah-qty-committed-${lineIdx}`}>{formatQtyId(bounds.committed)}</dd>
                <dt>Sisa Bisa Dikoreksi</dt>
                <dd data-testid={`ubah-qty-remaining-${lineIdx}`}>{formatQtyId(bounds.remaining)}</dd>
              </dl>
              <label className="line-item-edit-field">
                <span className="line-item-edit-label">Qty Baru</span>
                <div className="line-item-edit-qty-wrap">
                  <input
                    type="number"
                    min={bounds.min}
                    step="1"
                    inputMode="numeric"
                    value={quantity}
                    onChange={(event) => {
                      setQuantity(event.target.value);
                      setQtyErr("");
                      setConfirmIncrease(false);
                    }}
                    disabled={saving}
                    data-testid={`ubah-qty-input-${lineIdx}`}
                  />
                  <span>{li.unit || "pcs"}</span>
                </div>
              </label>
              <p className="muted small" data-testid={`ubah-qty-min-${lineIdx}`}>Minimum: {formatQtyId(bounds.min)}</p>
              {increasing && bounds.increaseAllowed && (
                <ul className="line-qty-increase-notes" data-testid={`ubah-qty-increase-notes-${lineIdx}`}>
                  {QTY_INCREASE_WARNINGS.map((note) => <li key={note}>{note}</li>)}
                </ul>
              )}
              {increaseBlocked && (
                <p className="error small" data-testid={`ubah-qty-delivered-${lineIdx}`}>
                  {bounds.increaseReason || QTY_DELIVERED_MESSAGE}
                </p>
              )}
              {confirmIncrease && (
                <div className="line-qty-confirm" data-testid={`ubah-qty-confirm-${lineIdx}`}>
                  <p>Tambah qty dari {formatQtyId(bounds.current)} menjadi {formatQtyId(parsedQty)}?</p>
                  <p className="muted small">
                    Tambahan {formatQtyId(addedQty)} {li.unit || "pcs"} perlu masuk Penjadwalan Operator sebelum dapat dikerjakan.
                  </p>
                </div>
              )}
              {qtyErr && (
                <p className="error small" data-testid={`ubah-qty-error-${lineIdx}`}>{qtyErr}</p>
              )}
            </div>
            <footer className="line-item-edit-footer">
              <button type="button" className="ghost" onClick={closeQty} disabled={saving}>
                Batal
              </button>
              <button
                type="button"
                className="primary"
                onClick={saveQty}
                disabled={saving || parsePositiveQty(quantity) === null || increaseBlocked}
                data-testid={`ubah-qty-save-${lineIdx}`}
              >
                {saving ? "Menyimpan…" : (confirmIncrease ? "Ya, tambah qty" : "Simpan Qty")}
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
