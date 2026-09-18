import { STAGES } from "./constants";
import {
  PO_DETAIL_STAGE_SECTION_IDS,
  PO_DETAIL_STEPPER_OMIT,
  PO_DETAIL_UI_STAGE_COUNT,
  collapsePoDetailUiStage,
  deskPanelOwnsStage,
  parsePoDetailStageParam,
  poDetailStagePanelTitle,
  poDetailStageSectionId,
  poDetailStageSlug,
  poDetailUiStageName,
  poDetailUiStageNumber,
  scrollToPoDetailStage,
  scrollToPoDetailStageIndex,
  syncPoDetailStageQuery,
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

  test("Design and Approval Design share the combined design card", () => {
    expect(PO_DETAIL_STAGE_SECTION_IDS.Design).toBe("po-stage-design");
    expect(PO_DETAIL_STAGE_SECTION_IDS["Approval Design"]).toBe("po-stage-design");
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

  test("parses ?stage= slugs, focus aliases, and numeric indexes", () => {
    expect(parsePoDetailStageParam("?stage=order-masuk")).toBe(0);
    expect(parsePoDetailStageParam("?stage=approval-design")).toBe(2);
    expect(collapsePoDetailUiStage(2)).toBe(1);
    expect(collapsePoDetailUiStage(4)).toBe(3);
    expect(poDetailUiStageName(2)).toBe("Design");
    expect(poDetailUiStageName(4)).toBe("Payment");
    expect(poDetailUiStageNumber(1)).toBe(2);
    expect(poDetailUiStageNumber(2)).toBe(2);
    expect(poDetailUiStageNumber(14)).toBe(PO_DETAIL_UI_STAGE_COUNT);
    expect(PO_DETAIL_STEPPER_OMIT).toEqual([2, 4]);
    expect(poDetailStagePanelTitle(2)).toBe("Workspace Design");
    expect(poDetailStagePanelTitle(4)).toBe("Workspace Payment");
    expect(parsePoDetailStageParam("?stage=packing")).toBe(11);
    expect(parsePoDetailStageParam("?stage=surat-jalan")).toBe(12);
    expect(parsePoDetailStageParam("?stage=loading")).toBe(13);
    expect(parsePoDetailStageParam("?stage=pengiriman")).toBe(14);
    expect(parsePoDetailStageParam("?focus=5")).toBe(5);
    expect(parsePoDetailStageParam("?stage=nope")).toBe(null);
    expect(poDetailStageSlug(11)).toBe("packing");
  });

  test("combined panels own adjacent canonical stages without sharing others", () => {
    expect(deskPanelOwnsStage("design", 1)).toBe(true);
    expect(deskPanelOwnsStage("design", 2)).toBe(true);
    expect(deskPanelOwnsStage("design", 3)).toBe(false);
    expect(deskPanelOwnsStage("1", 2)).toBe(true);
    expect(deskPanelOwnsStage("2", 1)).toBe(true);
    expect(deskPanelOwnsStage("payment", 3)).toBe(true);
    expect(deskPanelOwnsStage("payment", 4)).toBe(true);
    expect(deskPanelOwnsStage("payment", 5)).toBe(false);
    expect(deskPanelOwnsStage("sample", 8)).toBe(true);
    expect(deskPanelOwnsStage("sample", 9)).toBe(true);
    expect(deskPanelOwnsStage("11", 11)).toBe(true);
    expect(deskPanelOwnsStage("11", 12)).toBe(false);
  });

  test("syncPoDetailStageQuery writes stage without API calls", () => {
    const push = jest.spyOn(window.history, "pushState");
    const replace = jest.spyOn(window.history, "replaceState");
    syncPoDetailStageQuery(11, { push: true });
    expect(push).toHaveBeenCalled();
    expect(String(push.mock.calls[0][2])).toMatch(/stage=packing/);
    syncPoDetailStageQuery(0, { push: false });
    expect(replace).toHaveBeenCalled();
    expect(String(replace.mock.calls[0][2])).toMatch(/stage=order-masuk/);
    push.mockRestore();
    replace.mockRestore();
  });
});
