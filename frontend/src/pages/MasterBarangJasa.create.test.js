import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "MasterBarangJasa.jsx"), "utf8");
const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

test("master page exposes local create gated by manage permission", () => {
  expect(src).toMatch(/hasPerm\("master_items\.manage"\)/);
  expect(src).toMatch(/master-items-create-button/);
  expect(src).toMatch(/masterItemsCreate/);
  expect(src).toMatch(/item_code/);
  expect(src).toMatch(/item_type/);
  expect(src).not.toMatch(/accurate_item_id:\s*['"`]/);
  expect(api).toMatch(/masterItemsCreate:/);
});

test("master table distinguishes Lokal origin", () => {
  expect(src).toMatch(/originLabel/);
  expect(src).toMatch(/Lokal/);
});
