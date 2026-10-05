import fs from "fs";
import path from "path";

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

describe("PODetail Catatan Produksi wiring", () => {
  test("one panel, legacy Order Masuk plus production_note.view on every stage", () => {
    expect(detail).toMatch(/productionNotesVisible\(onOrderMasuk, hasPerm\("production_note\.view"\)\)/);
    expect(detail).toMatch(/hidden=\{!showProductionNotes\}/);
    expect(detail).toMatch(/hidden=\{!onOrderMasuk\}/);
    expect(detail).toMatch(/<ProductionNotesPanel/);
    expect((detail.match(/<ProductionNotesPanel/g) || []).length).toBe(1);
    expect(detail).not.toMatch(/production_note\.delete/);
  });

  test("append stays legacy non-CRM or production_note.create; edit is its own permission", () => {
    expect(detail).toMatch(/canAppendProductionNote\(role, hasPerm\("production_note\.create"\)\)/);
    expect(detail).toMatch(/hasPerm\("production_note\.edit"\)/);
    expect(detail).toMatch(/api\.addNote\(order\.id, text\)/);
    expect(detail).toMatch(/api\.updateNote\(order\.id, text\)/);
    expect(detail).not.toMatch(/!isCrm && \([\s\S]*Tambah Catatan/);
  });

  test("API client appends with POST and replaces with PATCH on the same note route", () => {
    expect(api).toMatch(/addNote:\s*\(id,\s*note\)\s*=>\s*client\.post\(`\/orders\/\$\{id\}\/note`/);
    expect(api).toMatch(/updateNote:\s*\(id,\s*note\)\s*=>\s*client\.patch\(`\/orders\/\$\{id\}\/note`/);
    expect(api).not.toMatch(/client\.delete\(`\/orders\/\$\{id\}\/note`/);
  });
});
