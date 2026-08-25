export function computeStockCurrent(poQty, manualCurrent, purchaseQty, operatorPrintQty) {
  const n = (v) => {
    const x = Number(v);
    return Number.isFinite(x) ? x : 0;
  };
  return n(poQty) - (n(manualCurrent) + n(purchaseQty)) - n(operatorPrintQty);
}
