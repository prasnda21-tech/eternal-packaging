/** Screen Besar / Screen Kecil — Production Schedule + Pembagian Jadwal.

Machine enums match backend `services/screen_size.py`. Display-only planning
metadata. Not Qty Screen and not the Screener workflow stage.
*/

export const SCREEN_SIZE_LARGE = "large";
export const SCREEN_SIZE_SMALL = "small";

export const SCREEN_SIZE_OPTIONS = [
  { value: SCREEN_SIZE_LARGE, label: "Screen Besar" },
  { value: SCREEN_SIZE_SMALL, label: "Screen Kecil" },
];

export function normalizeScreenSize(value) {
  if (value == null) return null;
  const text = String(value).trim().toLowerCase();
  if (!text) return null;
  if (text === SCREEN_SIZE_LARGE || text === SCREEN_SIZE_SMALL) return text;
  return null;
}

export function screenSizeLabel(value) {
  const v = normalizeScreenSize(value);
  if (v === SCREEN_SIZE_LARGE) return "Screen Besar";
  if (v === SCREEN_SIZE_SMALL) return "Screen Kecil";
  return "—";
}

export function lineItemScreenSize(order, lineIdx) {
  if (lineIdx === "" || lineIdx == null) return null;
  const idx = Number(lineIdx);
  const items = order?.line_items || [];
  if (!Number.isInteger(idx) || idx < 0 || idx >= items.length) return null;
  return normalizeScreenSize(items[idx]?.screen_size);
}
