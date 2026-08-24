/** Visibility vs submit for Packing card — used by PODetail + PackingPanel. */

export function shouldShowPackingCard(hasPackingUpdatePerm) {
  return !!hasPackingUpdatePerm;
}

export function packingSubmitEnabled(hasPackingUpdatePerm, availableForPacking) {
  return !!hasPackingUpdatePerm && Number(availableForPacking) > 0;
}
