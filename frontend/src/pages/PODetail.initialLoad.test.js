/**
 * Detail PO initial-load sequencing: sales receipts must start from route id
 * in parallel with getOrder (not gated on order?.id).
 */
import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");

describe("PODetail initial load sequencing (source)", () => {
  test("getOrder effect depends on route id", () => {
    expect(src).toMatch(/api\.getOrder\(id\)/);
    expect(src).toMatch(/if \(!cancelled\) setLoading\(false\)/);
  });

  test("listSalesReceipts starts with route id (parallel to getOrder)", () => {
    expect(src).toMatch(/api\.listSalesReceipts\(\{\s*po_id:\s*id\s*\}\)/);
    // Must not wait for hydrated order.id before first receipts request
    expect(src).not.toMatch(/api\.listSalesReceipts\(\{\s*po_id:\s*order\.id\s*\}\)/);
  });

  test("page loading gate is only the primary getOrder loading flag", () => {
    expect(src).toMatch(/if \(loading\) return <div className="loading" data-testid="po-loading"/);
    // Secondary receipts have their own subsection spinner, not the page gate
    expect(src).toMatch(/data-testid="sales-receipts-loading"/);
    expect(src).toMatch(/salesReceiptsLoading/);
  });
});
