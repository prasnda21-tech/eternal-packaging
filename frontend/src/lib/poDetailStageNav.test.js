import { STAGES } from "./constants";
import {
  PO_DETAIL_STAGE_SECTION_IDS,
  poDetailStageSectionId,
  scrollToPoDetailStage,
  scrollToPoDetailStageIndex,
} from "./poDetailStageNav";

describe("PO Detail stage navigation", () => {
  test("maps all 15 stage names to semantic section ids", () => {
    expect(STAGES).toHaveLength(15);
    STAGES.forEach((name) => {
      expect(poDetailStageSectionId(name)).toMatch(/^po-stage-/);
    });
  });

  test("Payment and Approval Payment share the combined payment card", () => {
    expect(PO_DETAIL_STAGE_SECTION_IDS.Payment).toBe("po-stage-payment");
    expect(PO_DETAIL_STAGE_SECTION_IDS["Approval Payment"]).toBe("po-stage-payment");
  });

  test("Packing Loading and Pengiriman have distinct anchors", () => {
    expect(poDetailStageSectionId("Packing")).toBe("po-stage-packing");
    expect(poDetailStageSectionId("Surat Jalan")).toBe("po-stage-surat-jalan");
    expect(poDetailStageSectionId("Loading")).toBe("po-stage-loading");
    expect(poDetailStageSectionId("Pengiriman")).toBe("po-stage-pengiriman");
  });

  test("scroll is DOM-only and does not mutate workflow", () => {
    const el = { scrollIntoView: jest.fn() };
    const getElementById = jest.spyOn(document, "getElementById").mockReturnValue(el);
    expect(scrollToPoDetailStage("Payment")).toBe(true);
    expect(el.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(scrollToPoDetailStageIndex(11)).toBe(true);
    expect(getElementById).toHaveBeenCalledWith("po-stage-packing");
    getElementById.mockRestore();
  });
});
