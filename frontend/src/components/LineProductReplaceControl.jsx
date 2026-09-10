import { useState } from "react";
import { RefreshCw, X } from "lucide-react";
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
  return err?.message || "Gagal mengganti barang";
}

export default function LineProductReplaceControl({
  order,
  lineIdx,
  canEdit,
  onUpdated,
}) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const li = (order?.line_items || [])[lineIdx];
  if (!li) return null;

  const allowed = canEdit && isLineProductReplaceAllowed(order, lineIdx);
  const reason = !allowed ? lockReasonForLine(order, lineIdx) : "";

  const openEditor = () => {
    setErr("");
    setTarget(null);
    setOpen(true);
  };

  const closeEditor = () => {
    if (saving) return;
    setOpen(false);
    setTarget(null);
    setErr("");
  };

  const save = async () => {
    if (saving || !target) return;
    const mid = target.id || target.master_item_id || null;
    const aid = target.accurate_item_id || null;
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
      setOpen(false);
      setTarget(null);
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
          <RefreshCw size={13} /> Ganti Barang
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
          <div className="modal-inner" style={{ maxWidth: 480 }}>
            <header className="modal-head">
              <h3>Ganti Barang</h3>
              <button type="button" className="ghost sm" onClick={closeEditor} disabled={saving} aria-label="Tutup">
                <X size={16} />
              </button>
            </header>
            <div style={{ padding: "16px 22px", display: "flex", flexDirection: "column", gap: 14 }}>
              <div data-testid={`ganti-barang-current-${lineIdx}`}>
                <div className="muted small">Barang Saat Ini</div>
                <strong>{li.name || "—"}</strong>
                {li.item_code ? <div className="mono small">{li.item_code}</div> : null}
              </div>

              <div data-testid={`ganti-barang-search-${lineIdx}`}>
                <div className="muted small" style={{ marginBottom: 6 }}>Cari Barang Pengganti</div>
                <MasterItemSearchSelect
                  value={target}
                  onChange={setTarget}
                  disabled={saving}
                  status="active"
                  placeholder="Cari barang…"
                  emptyLabel="Produk tidak ditemukan"
                  testId={`ganti-barang-picker-${lineIdx}`}
                />
              </div>

              {target && (
                <div data-testid={`ganti-barang-target-${lineIdx}`}>
                  <div className="muted small">Ganti Menjadi</div>
                  <strong>{formatMasterItemLabel(target)}</strong>
                </div>
              )}

              {err && (
                <p className="error small" data-testid={`ganti-barang-error-${lineIdx}`}>{err}</p>
              )}
            </div>
            <footer style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "12px 22px", borderTop: "1px solid var(--line)" }}>
              <button type="button" className="ghost" onClick={closeEditor} disabled={saving}>
                Batal
              </button>
              <button
                type="button"
                className="primary"
                onClick={save}
                disabled={saving || !target}
                data-testid={`ganti-barang-save-${lineIdx}`}
              >
                {saving ? "Menyimpan…" : "Simpan Perubahan"}
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
