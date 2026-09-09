/** Accurate Import PO preview: keep nested `preview` and hoist line fields. */

export function accuratePreviewInner(stateOrResponse) {
  if (!stateOrResponse || typeof stateOrResponse !== "object") return {};
  const nested = stateOrResponse.preview;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) return nested;
  return stateOrResponse;
}

export function accuratePreviewLineItems(stateOrResponse) {
  const inner = accuratePreviewInner(stateOrResponse);
  if (Array.isArray(inner.line_items)) return inner.line_items;
  if (Array.isArray(stateOrResponse?.line_items)) return stateOrResponse.line_items;
  return [];
}

/**
 * Wrap POST /accurate/import/preview JSON for ImportPO state.
 *
 * API shape: `{ preview: { item_count, line_items, ... }, duplicate, ... }`.
 * React reads `state.preview.line_items`. Visual-edits metadata binds
 * `x-source-var=preview` + `x-source-path=preview.item_count` /
 * `x-array-var=preview` as if `line_items` lived on the wrapper. Hoist
 * inner fields so both paths see the same rows.
 */
export function wrapAccurateImportPreviewResponse(d, source = "accurate") {
  const inner = accuratePreviewInner(d);
  const line_items = accuratePreviewLineItems(d);
  const item_count = line_items.length;
  const total_quantity = inner.total_quantity != null
    ? inner.total_quantity
    : line_items.reduce((s, li) => s + Number(li.quantity || 0), 0);
  const preview = { ...inner, line_items, item_count, total_quantity };
  return {
    ...d,
    ...preview,
    preview,
    line_items,
    item_count,
    total_quantity,
    source,
  };
}
