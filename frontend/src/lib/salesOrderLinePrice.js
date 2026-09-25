/** Selling-price helpers for a sales-order line. Blank is not zero. */

export function linePriceNumber(value) {
  if (value === null || value === undefined) return null;
  const text = String(value).trim().replace(",", ".");
  if (!text) return null;
  const amount = Number(text);
  if (!Number.isFinite(amount)) return null;
  return Math.round(amount * 100) / 100;
}

export function pricesEqual(left, right) {
  const a = linePriceNumber(left);
  const b = linePriceNumber(right);
  if (a === null || b === null) return false;
  return a === b;
}

export function lineSellingSubtotal(quantity, unitPrice) {
  const price = linePriceNumber(unitPrice);
  const qty = Number(quantity);
  if (price === null || !Number.isFinite(qty)) return null;
  return Math.round(qty * price * 100) / 100;
}

export function orderSellingTotal(lines) {
  let seen = false;
  let sum = 0;
  (lines || []).forEach((line) => {
    const subtotal = lineSellingSubtotal(line?.quantity, line?.unit_price);
    if (subtotal === null) return;
    seen = true;
    sum += subtotal;
  });
  return seen ? Math.round(sum * 100) / 100 : null;
}

export function quoteFailureMessage(quote, failed) {
  if (failed) return "Harga jual tidak dapat diambil. Isi manual.";
  if (quote?.status === "unit_mismatch") {
    const expected = String(quote.expected_unit || "").trim();
    return expected
      ? `Satuan tidak cocok dengan Pengaturan Harga (${expected}). Isi manual.`
      : "Satuan tidak cocok dengan Pengaturan Harga. Isi manual.";
  }
  if (quote?.status === "ambiguous") return "Harga jual ambigu di Pengaturan Harga. Isi manual.";
  return "Harga jual tidak ditemukan di Pengaturan Harga. Isi manual.";
}

export function priceSourceLabel(line) {
  const price = linePriceNumber(line?.unit_price);
  if (price === null && line?.price_message) return line.price_message;
  if (line?.price_review) return "Perlu pemeriksaan";
  if (price === null) return "Isi harga jual";
  if (line?.price_source === "pengaturan_harga") return "Harga dari Pengaturan Harga";
  return "Input manual";
}

export function formatOrderRupiah(value) {
  const amount = linePriceNumber(value);
  if (amount === null) return "—";
  const hasFraction = Math.round(amount * 100) % 100 !== 0;
  return `Rp${amount.toLocaleString("id-ID", {
    minimumFractionDigits: hasFraction ? 2 : 0,
    maximumFractionDigits: 2,
  })}`;
}
