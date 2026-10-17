import { productionSchedulePoHref, schedulePoHref } from "./workPanel";

describe("Jadwal Produksi PO navigation", () => {
  test("bare href stays canonical Detail PO without stage (bell / fallback)", () => {
    expect(productionSchedulePoHref("abc")).toBe("/po/abc");
    expect(productionSchedulePoHref("abc")).not.toMatch(/status-pengiriman/);
    expect(productionSchedulePoHref("abc")).not.toMatch(/pengiriman/);
    expect(productionSchedulePoHref("abc")).not.toMatch(/kerja/);
  });

  test("schedule list context deep-links to Penjadwalan stage view", () => {
    expect(productionSchedulePoHref("abc", { stage: "penjadwalan" })).toBe("/po/abc?stage=penjadwalan");
    expect(productionSchedulePoHref("abc", { stage: "penjadwalan" })).not.toMatch(/kerja/);
  });

  test("CRM work-panel helper would otherwise open Surat Jalan, not used for Jadwal Produksi", () => {
    const crmHas = (p) => p === "delivery_note.manage" || p === "po.view";
    expect(schedulePoHref("abc", crmHas)).toBe("/po/abc/kerja?divisi=surat_jalan");
    expect(productionSchedulePoHref("abc")).toBe("/po/abc");
  });
});
