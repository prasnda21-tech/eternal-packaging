export function computeStockCurrent(poQty, manualCurrent, purchaseQty, operatorPrintQty) {
  const n = (v) => {
    const x = Number(v);
    return Number.isFinite(x) ? x : 0;
  };
  return (n(manualCurrent) + n(purchaseQty)) - n(poQty) - n(operatorPrintQty);
}

export function purchaseSourceLabel(source) {
  return String(source || "").toUpperCase() === "ACCURATE" ? "Accurate" : "Manual";
}

export function isAccuratePurchaseSource(source) {
  return String(source || "").toUpperCase() === "ACCURATE";
}
