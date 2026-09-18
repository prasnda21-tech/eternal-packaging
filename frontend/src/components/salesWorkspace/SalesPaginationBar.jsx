import { SALES_PAGE_SIZES } from "../../lib/salesWorkspaceList";

export default function SalesPaginationBar({
  page,
  pageSize,
  pageCount,
  total,
  start,
  end,
  onPageChange,
  onPageSizeChange,
  selectAllLabel = "Pilih semua memilih seluruh hasil, bukan hanya halaman ini.",
}) {
  return (
    <div className="sw-pager" data-testid="sales-pagination">
      <div className="sw-pager-meta">
        <span data-testid="sales-pagination-range">
          {total === 0 ? "0 hasil" : `${start}–${end} dari ${total}`}
        </span>
        <p className="sr-only">{selectAllLabel}</p>
      </div>
      <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <label>
          <span className="sr-only">Baris per halaman</span>
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            data-testid="sales-page-size"
            aria-label="Baris per halaman"
          >
            {SALES_PAGE_SIZES.map((size) => (
              <option key={size} value={size}>{`${size} / halaman`}</option>
            ))}
          </select>
        </label>
        <button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)} aria-label="Halaman sebelumnya" data-testid="sales-page-prev">
          ‹
        </button>
        <span data-testid="sales-page-indicator">{page} / {pageCount}</span>
        <button type="button" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)} aria-label="Halaman berikutnya" data-testid="sales-page-next">
          ›
        </button>
      </div>
    </div>
  );
}
