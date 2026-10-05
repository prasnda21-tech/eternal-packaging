import { canAppendProductionNote, productionNotesVisible } from "./productionNoteAccess";

describe("production note access", () => {
  test("Order Masuk legacy viewer sees the panel without production_note.view", () => {
    expect(productionNotesVisible(true, false)).toBe(true);
  });

  test("another workflow stage stays hidden without production_note.view", () => {
    expect(productionNotesVisible(false, false)).toBe(false);
  });

  test("production_note.view shows the panel on a non-Order-Masuk stage", () => {
    expect(productionNotesVisible(false, true)).toBe(true);
    expect(productionNotesVisible(true, true)).toBe(true);
  });

  test("non-CRM append stays allowed without production_note.create", () => {
    expect(canAppendProductionNote("operator", false)).toBe(true);
    expect(canAppendProductionNote("admin", false)).toBe(true);
    expect(canAppendProductionNote("head_production", false)).toBe(true);
  });

  test("CRM append requires production_note.create", () => {
    expect(canAppendProductionNote("crm", false)).toBe(false);
    expect(canAppendProductionNote("crm", true)).toBe(true);
  });
});
