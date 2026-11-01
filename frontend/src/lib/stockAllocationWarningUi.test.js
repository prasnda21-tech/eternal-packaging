/**
 * Prove allocation warnings are user-visible soft signals — never submit blockers.
 */
import fs from "fs";
import path from "path";
import {
  allocationWarningMessage,
  summarizeFormAllocationWarnings,
} from "./stockAllocationForm";
import { deliveryAllocationSoftWarnings } from "../components/DeliveryProofPanel";

const soSrc = fs.readFileSync(path.join(__dirname, "../pages/PesananPenjualan.jsx"), "utf8");
const praSrc = fs.readFileSync(path.join(__dirname, "../pages/PraPo.jsx"), "utf8");
const deliverySrc = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");

describe("allocation warning UI contracts", () => {
  test("SO create surfaces warning testid and does not disable save for imbalance", () => {
    expect(soSrc).toContain('data-testid="so-allocation-warning"');
    expect(soSrc).toContain("summarizeFormAllocationWarnings");
    // Save path still runs createSalesOrder after soft notice — no throw/return on softAllocWarnings alone.
    const submit = soSrc.slice(soSrc.indexOf("handleFormSubmit"), soSrc.indexOf("handleFormSubmit") + 1800);
    expect(submit).toContain("setAllocationNotice");
    expect(submit).toContain("api.createSalesOrder");
    expect(submit).not.toMatch(/if \(softAllocWarnings\.length\) \{\s*setFormErr/);
    expect(submit).not.toMatch(/if \(softAllocWarnings\.length\) \{\s*return/);
  });

  test("Pra-PO verify keeps Setujui enabled with allocation panel warning", () => {
    expect(praSrc).toContain('data-testid="pra-po-verify-allocations"');
    expect(praSrc).toContain('data-testid="pra-po-allocation-warning"');
    expect(praSrc).toContain('data-testid="pra-po-verify"');
    const verifyBtn = praSrc.match(/disabled=\{([^}]+)\}[^\n]*data-testid="pra-po-verify"/);
    expect(verifyBtn).toBeTruthy();
    expect(verifyBtn[1]).toContain("busy");
    expect(verifyBtn[1]).not.toMatch(/allocation/i);
  });

  test("Delivery shows allocation warning without disabling confirm", () => {
    expect(deliverySrc).toContain('data-testid="delivery-allocation-warning"');
    expect(deliverySrc).toContain("tidak memblokir pengiriman");
    const confirm = deliverySrc.match(/disabled=\{([^}]+)\}[\s\S]{0,80}data-testid="confirm-delivered-button"/);
    expect(confirm).toBeTruthy();
    expect(confirm[1]).not.toMatch(/allocation/i);
  });

  test("soft warning messages cover empty/under/over and warehouse mismatch", () => {
    expect(allocationWarningMessage({
      master_item_id: "m", quantity: 10, stock_allocations: [],
    })).toMatch(/belum lengkap/i);
    expect(summarizeFormAllocationWarnings([
      { master_item_id: "m", quantity: 10, stock_allocations: [{ warehouse_id: "w", qty: 1 }] },
    ])[0].message).toMatch(/kurang/i);
    const soft = deliveryAllocationSoftWarnings(
      {
        line_items: [{
          line_id: "l1",
          stock_allocations: [{ warehouse_id: "w1", qty: 5 }],
        }],
      },
      "w2",
    );
    expect(soft).toHaveLength(1);
    expect(soft[0].message).toMatch(/berbeda dari rencana/i);
    expect(deliveryAllocationSoftWarnings(
      { line_items: [{ stock_allocations: [{ warehouse_id: "w2", qty: 5 }] }] },
      "w2",
    )).toHaveLength(0);
  });
});
