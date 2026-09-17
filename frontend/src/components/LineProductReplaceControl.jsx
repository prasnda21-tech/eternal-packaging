import { useState } from "react";
import { Check, RefreshCw, X } from "lucide-react";
import api from "../lib/api";
import MasterItemSearchSelect, { formatMasterItemLabel } from "./MasterItemSearchSelect";

/**
 * Compact Ganti Barang editor for an exact order line_idx.
 * Writes only on explicit Simpan Perubahan.
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
  // Conservative fallback without eligibility payload: no sample for print; no embedded downstream.
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
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState(null);
  const [quantity, setQuantity] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const li = (order?.line_items || [])[lineIdx];
  if (!li) return null;

  const allowed = canEdit && isLineProductReplaceAllowed(order, lineIdx);
  const reason = !allowed ? lockReasonForLine(order, lineIdx) : "";

  const openEditor = () => {
    setErr("");
    setTarget(null);
    setQuantity(String(li.quantity || ""));
    setOpen(true);
  };

  const closeEditor = () => {
    if (saving) return;
    setOpen(false);
    setTarget(null);
    setQuantity("");
    setErr("");
  };

  const save = async () => {
    if (saving) return;
    const qty = parsePositiveQty(quantity);
    if (qty === null) {
      setErr("Qty baru wajib berupa angka bulat lebih dari 0.");
      return;
    }
    if (!hasLineItemChanges(li, target, quantity)) {
      setErr("Belum ada perubahan barang atau qty.");
      return;
    }
    const mid = target?.id || target?.master_item_id || null;
    const aid = target?.accurate_item_id || null;
    if (target && !mid && !aid) {
      setErr("Pilih produk dengan identitas kanonis.");
      return;
    }
    setSaving(true);
    setErr("");
    try {
      const res = await api.replaceSalesOrderLineProduct(order.id, lineIdx, {
        ...(target ? { master_item_id: mid, accurate_item_id: aid } : {}),
        quantity: qty,
      });
      if (res?.order && onUpdated) onUpdated(res.order);
      setOpen(false);
      setTarget(null);
      setQuantity("");
    } catch (e) {
      setErr(formatReplaceApiError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="line-product-replace" data-testid={`line-product-replace-${lineIdx}`}>
      {allowed ? (
        <button
          type="button"
          className="ghost sm"
          onClick={openEditor}
          data-testid={`ganti-barang-btn-${lineIdx}`}
        >
          <RefreshCw size={13} /> Ubah Item
        </button>
      ) : (
        <span
          className="muted small"
          data-testid={`ganti-barang-locked-${lineIdx}`}
          title={reason}
        >
          {reason}
        </span>
      )}

      {open && (
        <div className="modal" data-testid={`ganti-barang-modal-${lineIdx}`} role="dialog">
          <div className="modal-inner line-item-edit-modal">
            <header className="modal-head">
              <div>
                <h3>Ubah Item PO</h3>
                <p className="muted small">Ganti barang dan qty dalam satu perubahan.</p>
              </div>
              <button type="button" className="ghost sm" onClick={closeEditor} disabled={saving} aria-label="Tutup">
                <X size={16} />
              </button>
            </header>
            <div className="line-item-edit-body">
              <div className="line-item-edit-current" data-testid={`ganti-barang-current-${lineIdx}`}>
                <div className="line-item-edit-section-label">Saat Ini</div>
                <div className="line-item-edit-current-grid">
                  <div>
                    <span className="muted small">Barang</span>
                    <strong>{li.name || "—"}</strong>
                    {li.item_code ? <span className="mono small">{li.item_code}</span> : null}
                  </div>
                  <div>
                    <span className="muted small">Qty</span>
                    <strong>{Number(li.quantity || 0).toLocaleString("id-ID")} {li.unit || "pcs"}</strong>
                  </div>
                </div>
              </div>

              <div data-testid={`ganti-barang-search-${lineIdx}`}>
                <label className="line-item-edit-label">Barang Pengganti <span>(opsional)</span></label>
                <MasterItemSearchSelect
                  value={target}
                  onChange={setTarget}
                  disabled={saving}
                  status="active"
                  placeholder="Cari nama atau kode barang…"
                  emptyLabel="Produk tidak ditemukan"
                  testId={`ganti-barang-picker-${lineIdx}`}
                />
              </div>

              <label className="line-item-edit-field">
                <span className="line-item-edit-label">Qty Baru</span>
                <div className="line-item-edit-qty-wrap">
                  <input
                    type="number"
                    min="1"
                    step="1"
                    inputMode="numeric"
                    value={quantity}
                    onChange={(event) => { setQuantity(event.target.value); setErr(""); }}
                    disabled={saving}
                    data-testid={`ganti-barang-qty-${lineIdx}`}
                  />
                  <span>{target?.unit || li.unit || "pcs"}</span>
                </div>
              </label>

              <div className="line-item-edit-preview" data-testid={`ganti-barang-target-${lineIdx}`}>
                <div className="line-item-edit-section-label">Setelah Disimpan</div>
                <div className="line-item-edit-preview-row">
                  <div>
                    <strong>{target ? formatMasterItemLabel(target) : (li.name || "—")}</strong>
                    <span>{parsePositiveQty(quantity)?.toLocaleString("id-ID") || "—"} {target?.unit || li.unit || "pcs"}</span>
                  </div>
                  <Check size={18} aria-hidden="true" />
                </div>
              </div>

              <p className="line-item-edit-note">Perubahan hanya berlaku pada item PO ini. Tahap produksi tetap sama.</p>

              {err && (
                <p className="error small" data-testid={`ganti-barang-error-${lineIdx}`}>{err}</p>
              )}
            </div>
            <footer className="line-item-edit-footer">
              <button type="button" className="ghost" onClick={closeEditor} disabled={saving}>
                Batal
              </button>
              <button
                type="button"
                className="primary"
                onClick={save}
                disabled={saving || parsePositiveQty(quantity) === null || !hasLineItemChanges(li, target, quantity)}
                data-testid={`ganti-barang-save-${lineIdx}`}
              >
                {saving ? "Menyimpan…" : "Simpan Item & Qty"}
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
