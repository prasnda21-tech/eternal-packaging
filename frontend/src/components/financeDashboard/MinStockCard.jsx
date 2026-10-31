import React from "react";

function fmtQty(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("id-ID");
}

export default function MinStockCard({ model, compact }) {
  const status = model?.status || "loading";
  const violations = Number(model?.violation_count);
  const unevaluable = Number(model?.unevaluable_count) || 0;
  const items = Array.isArray(model?.items) ? model.items : [];
  const preview = compact ? items.slice(0, 3) : items.slice(0, 6);

  if (status === "loading") {
    return <div className="kb-skel" aria-busy="true" aria-label="Memuat"><span /><span /><span /></div>;
  }
  if (status === "forbidden") {
    return (
      <div className="kb-empty is-forbidden">
        <strong>Tidak ada izin</strong>
        <p>Kartu ini membutuhkan izin Lihat Stock Saat Ini.</p>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="kb-empty is-error">
        <strong>Gagal memuat</strong>
        <p>{model?.detail || "Muat ulang untuk mencoba lagi."}</p>
      </div>
    );
  }

  const showCount = Number.isFinite(violations) ? violations : null;
  return (
    <div className="kb-min-stock" data-testid="min-stock-card">
      <div className="kb-trio">
        <div>
          <span>Di/bawah minimum</span>
          <strong data-testid="min-stock-violation-count">
            {showCount == null ? "—" : showCount.toLocaleString("id-ID")}
          </strong>
          <small>kombinasi barang–gudang</small>
        </div>
        <div>
          <span>Belum bisa dinilai</span>
          <strong data-testid="min-stock-unevaluable-count">
            {unevaluable.toLocaleString("id-ID")}
          </strong>
        </div>
      </div>
      {status === "incomplete" ? (
        <p className="kb-kicker" data-testid="min-stock-incomplete-note">
          {model?.detail || "Sebagian data belum bisa dinilai; angka pelanggaran hanya hasil terverifikasi."}
        </p>
      ) : null}
      {status === "empty" && unevaluable === 0 ? (
        <p className="kb-kicker">Tidak ada kombinasi barang–gudang di/bawah minimum.</p>
      ) : null}
      {preview.length ? (
        <div className="kb-lines" data-testid="min-stock-preview">
          {preview.map((row) => (
            <div key={`${row.master_item_id}-${row.warehouse_id}`}>
              <span title={row.item_name}>
                {row.item_name || row.item_code || "Produk"}
                {row.warehouse_code || row.warehouse_name
                  ? ` · ${row.warehouse_code || row.warehouse_name}`
                  : ""}
              </span>
              <strong>
                {fmtQty(row.stock_dapat_dijual)} / {row.minimum_configured ? fmtQty(row.minimum_qty) : "—"}
              </strong>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
