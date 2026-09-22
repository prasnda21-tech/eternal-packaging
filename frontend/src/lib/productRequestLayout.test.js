import { checkFormLayout, checkStepperNoClip, rectContained, rectsOverlap } from "./productRequestLayout";

describe("product request layout helpers", () => {
  test("rectsOverlap uses getBoundingClientRect-style boxes", () => {
    const progress = { top: 0, bottom: 26, left: 0, right: 800 };
    const customerTitle = { top: 31, bottom: 46, left: 0, right: 200 };
    const overlapping = { top: 20, bottom: 40, left: 0, right: 200 };
    expect(rectsOverlap(progress, customerTitle)).toBe(false);
    expect(rectsOverlap(progress, overlapping)).toBe(true);
  });

  test("rectContained keeps material and model inside the product section", () => {
    const product = { top: 100, bottom: 280, left: 0, right: 700 };
    const material = { top: 220, bottom: 251, left: 8, right: 240 };
    const clipped = { top: 260, bottom: 300, left: 8, right: 240 };
    expect(rectContained(material, product)).toBe(true);
    expect(rectContained(clipped, product)).toBe(false);
  });

  test("checkFormLayout reports overlapping and overflowing pairs", () => {
    const rects = {
      "[data-testid='prq-progress']": { top: 0, bottom: 26, left: 0, right: 800 },
      "[data-testid='prq-customer-title']": { top: 32, bottom: 46, left: 8, right: 200 },
      "[data-testid='prq-customer-card']": { top: 32, bottom: 170, left: 0, right: 700 },
      "[data-testid='prq-product-card']": { top: 176, bottom: 360, left: 0, right: 700 },
      "[data-testid='prq-potential-card']": { top: 366, bottom: 500, left: 0, right: 700 },
      "[data-testid='prq-actions']": { top: 506, bottom: 542, left: 0, right: 1000 },
      "[data-testid='prq-main']": { top: 32, bottom: 500, left: 0, right: 700 },
      "[data-testid='prq-summary']": { top: 32, bottom: 420, left: 708, right: 1008 },
      "[data-testid='prq-new-product-material']": { top: 300, bottom: 331, left: 8, right: 240 },
      "[data-testid='prq-new-product-model']": { top: 300, bottom: 331, left: 248, right: 690 },
      "[data-testid='prq-material-label']": { top: 286, bottom: 298, left: 8, right: 80 },
      "[data-testid='prq-model-label']": { top: 286, bottom: 298, left: 248, right: 400 },
      "[data-testid='prq-qty']": { top: 420, bottom: 451, left: 8, right: 160 },
      "[data-testid='prq-needed-date']": { top: 420, bottom: 451, left: 520, right: 690 },
    };
    expect(checkFormLayout((selector) => rects[selector])).toEqual([]);

    const broken = {
      ...rects,
      "[data-testid='prq-progress']": { top: 20, bottom: 50, left: 0, right: 800 },
      "[data-testid='prq-new-product-material']": { top: 350, bottom: 390, left: 8, right: 240 },
    };
    const violations = checkFormLayout((selector) => broken[selector]);
    expect(violations).toEqual(expect.arrayContaining([
      "[data-testid='prq-progress'] overlaps [data-testid='prq-customer-title']",
      "[data-testid='prq-new-product-material'] not inside [data-testid='prq-product-card']",
    ]));
  });

  test("checkStepperNoClip requires circles and labels inside the stepper", () => {
    const ok = {
      "[data-testid='workspace-tabs']": { top: 48, bottom: 82, left: 0, right: 1366 },
      "[data-testid='prq-progress']": { top: 120, bottom: 152, left: 74, right: 1356 },
      "[data-testid='prq-customer-card']": { top: 160, bottom: 330, left: 74, right: 1048 },
      "[data-testid='prq-progress-customer']": { top: 125, bottom: 147, left: 74, right: 380 },
      "[data-testid='prq-progress-circle-customer']": { top: 126, bottom: 146, left: 74, right: 94 },
      "[data-testid='prq-progress-label-customer']": { top: 128, bottom: 141, left: 98, right: 160 },
      "[data-testid='prq-progress-product']": { top: 125, bottom: 147, left: 390, right: 700 },
      "[data-testid='prq-progress-circle-product']": { top: 126, bottom: 146, left: 390, right: 410 },
      "[data-testid='prq-progress-label-product']": { top: 128, bottom: 141, left: 414, right: 470 },
      "[data-testid='prq-progress-potential']": { top: 125, bottom: 147, left: 710, right: 1020 },
      "[data-testid='prq-progress-circle-potential']": { top: 126, bottom: 146, left: 710, right: 730 },
      "[data-testid='prq-progress-label-potential']": { top: 128, bottom: 141, left: 734, right: 790 },
      "[data-testid='prq-progress-submit']": { top: 125, bottom: 147, left: 1030, right: 1356 },
      "[data-testid='prq-progress-circle-submit']": { top: 126, bottom: 146, left: 1030, right: 1050 },
      "[data-testid='prq-progress-label-submit']": { top: 128, bottom: 141, left: 1054, right: 1100 },
    };
    expect(checkStepperNoClip((selector) => ok[selector])).toEqual([]);

    const clipped = {
      ...ok,
      "[data-testid='prq-progress-circle-customer']": { top: 118, bottom: 138, left: 74, right: 94 },
    };
    expect(checkStepperNoClip((selector) => clipped[selector])).toContain("step customer clipped by stepper");
  });
});
