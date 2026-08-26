import fs from "fs";
import path from "path";

describe("Customer approval public wording", () => {
  const src = fs.readFileSync(path.join(__dirname, "PublicCustomerApproval.jsx"), "utf8");
  test("uses Revisi not Reject for customer-facing copy", () => {
    expect(src).toMatch(/Revisi/);
    expect(src).toMatch(/Revisi Diminta/);
    expect(src).not.toMatch(/Reject Design/);
    expect(src).not.toMatch(/Sudah Reject/);
    expect(src).not.toMatch(/\(Rejected\)/);
    expect(src).not.toMatch(/Ditolak/);
  });
});
