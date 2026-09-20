export function rectsOverlap(a, b) {
  if (!a || !b) return false;
  return !(a.bottom <= b.top || b.bottom <= a.top || a.right <= b.left || b.right <= a.left);
}

export function rectContained(inner, outer, pad = 0.5) {
  if (!inner || !outer) return false;
  return (
    inner.top >= outer.top - pad
    && inner.bottom <= outer.bottom + pad
    && inner.left >= outer.left - pad
    && inner.right <= outer.right + pad
  );
}

export const FORM_LAYOUT_PAIRS = [
  ["[data-testid='prq-progress']", "[data-testid='prq-customer-title']"],
  ["[data-testid='prq-customer-card']", "[data-testid='prq-product-card']"],
  ["[data-testid='prq-product-card']", "[data-testid='prq-potential-card']"],
  ["[data-testid='prq-potential-card']", "[data-testid='prq-actions']"],
  ["[data-testid='prq-main']", "[data-testid='prq-summary']"],
];

export const FORM_CONTAINED_PAIRS = [
  ["[data-testid='prq-new-product-material']", "[data-testid='prq-product-card']"],
  ["[data-testid='prq-new-product-model']", "[data-testid='prq-product-card']"],
  ["[data-testid='prq-material-label']", "[data-testid='prq-product-card']"],
  ["[data-testid='prq-model-label']", "[data-testid='prq-product-card']"],
  ["[data-testid='prq-qty']", "[data-testid='prq-potential-card']"],
  ["[data-testid='prq-needed-date']", "[data-testid='prq-potential-card']"],
];

export const STEPPER_KEYS = ["customer", "product", "potential", "submit"];

export function checkStepperNoClip(getRect) {
  const violations = [];
  const container = getRect("[data-testid='prq-progress']");
  const customer = getRect("[data-testid='prq-customer-card']");
  const tab = getRect("[data-workspace-tab='1']") || getRect(".kb-workspace-tab") || getRect("[data-testid='workspace-tabs']");
  if (!container) return ["stepper missing"];
  if (customer && container.bottom > customer.top) {
    violations.push("stepper overlaps Data Customer");
  }
  if (tab && tab.bottom > container.top) {
    violations.push("tab header overlaps stepper");
  }
  for (const key of STEPPER_KEYS) {
    const item = getRect(`[data-testid='prq-progress-${key}']`);
    const circle = getRect(`[data-testid='prq-progress-circle-${key}']`);
    const label = getRect(`[data-testid='prq-progress-label-${key}']`);
    if (!item || !circle || !label) {
      violations.push(`step ${key} missing`);
      continue;
    }
    if (!rectContained(item, container) || !rectContained(circle, container) || !rectContained(label, container)) {
      violations.push(`step ${key} clipped by stepper`);
    }
  }
  return violations;
}

export function checkFormLayout(getRect) {
  const violations = [];
  for (const [a, b] of FORM_LAYOUT_PAIRS) {
    const ra = getRect(a);
    const rb = getRect(b);
    if (!ra || !rb) {
      violations.push(`${a} or ${b} missing`);
      continue;
    }
    if (rectsOverlap(ra, rb)) violations.push(`${a} overlaps ${b}`);
  }
  for (const [inner, outer] of FORM_CONTAINED_PAIRS) {
    const ri = getRect(inner);
    const ro = getRect(outer);
    if (!ri || !ro) {
      violations.push(`${inner} or ${outer} missing`);
      continue;
    }
    if (!rectContained(ri, ro)) violations.push(`${inner} not inside ${outer}`);
  }
  return violations;
}
