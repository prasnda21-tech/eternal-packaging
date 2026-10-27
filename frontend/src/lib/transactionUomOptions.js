/**
 * Build selectable transaction UOMs for PO/GR lines (PCS base + alternates).
 * Conversion to PCS happens once on the backend from the chosen unit.
 */

export function normalizeUom(value) {
  const raw = String(value || "").trim().toUpperCase();
  return raw || "PCS";
}

/**
 * @param {object|null|undefined} unitSetting from masterItemDetail.unit_setting
 * @param {object|null|undefined} master fallback master row (unit)
 * @returns {{ uom: string, factor_to_base: number, label: string }[]}
 */
export function buildTransactionUomOptions(unitSetting, master) {
  const setting = unitSetting && typeof unitSetting === "object" ? unitSetting : {};
  const base = normalizeUom(setting.base_uom || master?.unit || master?.base_uom || "PCS");
  const options = [{ uom: base, factor_to_base: 1, label: base === "PCS" ? "PCS (dasar)" : `${base} (dasar)` }];
  const seen = new Set([base]);

  const alts = Array.isArray(setting.alternate_uoms) ? setting.alternate_uoms : [];
  for (const row of alts) {
    if (!row) continue;
    const uom = normalizeUom(row.uom);
    if (!uom || seen.has(uom)) continue;
    const factor = Number(row.factor_to_base ?? row.conversion_factor);
    if (!Number.isFinite(factor) || factor <= 0) continue;
    seen.add(uom);
    options.push({
      uom,
      factor_to_base: factor,
      label: `${uom} (= ${factor} ${base})`,
    });
  }

  // Legacy single-pair setting without alternate_uoms[]
  const legacyTx = normalizeUom(setting.transaction_uom);
  const legacyFactor = Number(setting.conversion_factor);
  if (legacyTx && !seen.has(legacyTx) && Number.isFinite(legacyFactor) && legacyFactor > 0) {
    options.push({
      uom: legacyTx,
      factor_to_base: legacyFactor,
      label: `${legacyTx} (= ${legacyFactor} ${base})`,
    });
  }

  return options;
}

export function defaultTransactionUom(options, preferred) {
  const list = Array.isArray(options) ? options : [];
  const want = normalizeUom(preferred);
  if (list.some((row) => row.uom === want)) return want;
  return list[0]?.uom || "PCS";
}
