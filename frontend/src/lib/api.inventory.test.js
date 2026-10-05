import fs from "fs";
import path from "path";
import api from "./api";

const INVENTORY_METHODS = [
  "branches",
  "warehouses",
  "physicalStockCatalog",
  "physicalStock",
  "physicalMutations",
  "physicalOpeningBalance",
  "createPhysicalOpeningBalance",
  "createPhysicalAdjustment",
];

describe("inventory api contract", () => {
  test.each(INVENTORY_METHODS)("%s is callable", (name) => {
    expect(typeof api[name]).toBe("function");
  });

  test("confirmDelivered still accepts a request config", () => {
    const source = fs.readFileSync(path.join(__dirname, "api.js"), "utf8");
    expect(typeof api.confirmDelivered).toBe("function");
    expect(source).toMatch(/confirmDelivered:\s*\(id,\s*payload\s*=\s*\{\},\s*config\)/);
  });
});
