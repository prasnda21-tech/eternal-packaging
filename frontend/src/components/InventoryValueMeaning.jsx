import { Link } from "react-router-dom";
import { formatRupiah } from "../lib/purchaseUi";
import {
  AVAILABLE_FOR_ORDER_NOTE,
  BOOK_VALUE_NOTE,
  inventoryEstimateText,
  inventoryValueReconPath,
} from "../lib/inventoryValueLink";

export default function InventoryValueMeaning({
  physicalText,
  item = null,
  covered = false,
  loading = false,
  reason = "",
  testIdPrefix = "inventory-meaning",
  reconTo,
}) {
  const estimateText = loading
    ? "Memuat…"
    : (covered ? inventoryEstimateText(item, formatRupiah) : "Belum ada dasar");
  return (
    <div data-testid={`${testIdPrefix}-meanings`}>
      <p>
        <span>Tersedia untuk pesanan: </span>
        <span data-testid={`${testIdPrefix}-available`}>{AVAILABLE_FOR_ORDER_NOTE}</span>
      </p>
      <p>
        <span>Stok fisik: </span>
        <strong data-testid={`${testIdPrefix}-physical`}>{physicalText}</strong>
      </p>
      <p>
        <span>Nilai estimasi: </span>
        <strong data-testid={`${testIdPrefix}-estimate`}>{estimateText}</strong>
      </p>
      <p data-testid={`${testIdPrefix}-book`}>
        <span>Nilai buku dari jurnal: </span>
        {BOOK_VALUE_NOTE}{" "}
        <Link to={reconTo || inventoryValueReconPath()}>Buka rekonsiliasi agregat</Link>
      </p>
      {reason ? <p data-testid={`${testIdPrefix}-reason`}>{reason}</p> : null}
    </div>
  );
}
