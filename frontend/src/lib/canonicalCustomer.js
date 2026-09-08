/** Canonical PO customer display for PWA. Never outlet/region/location. */

export const CANONICAL_CUSTOMER_PLACEHOLDER = "—";

function asDisplayName(value) {
  if (typeof value === "string") return value.trim();
  return "";
}

export function getCanonicalCustomerName(record, placeholder = CANONICAL_CUSTOMER_PLACEHOLDER) {
  if (!record || typeof record !== "object") return placeholder;
  const primary = asDisplayName(record.customer);
  if (primary) return primary;
  const secondary = asDisplayName(record.customer_name);
  if (secondary) return secondary;
  return placeholder;
}
