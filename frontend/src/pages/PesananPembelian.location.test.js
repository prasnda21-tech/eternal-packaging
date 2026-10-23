import fs from "fs";
import path from "path";
import {
  PO_CREATE_BRANCH,
  applyCreatePoBranchFetchResult,
  purchaseBranchLabel,
  resolveCreatePoBranchLabel,
} from "../lib/purchaseUi";

const page = fs.readFileSync(path.join(__dirname, "PesananPembelian.jsx"), "utf8");

describe("PesananPembelian LOC-C1 Cabang Pembelian UI", () => {
  test("create and detail expose readonly Cabang Pembelian from helpers/master", () => {
    expect(page).toMatch(/po-create-branch/);
    expect(page).toMatch(/po-detail-branch/);
    expect(page).toMatch(/Cabang Pembelian/);
    expect(page).toMatch(/PO_CREATE_BRANCH/);
    expect(page).toMatch(/applyCreatePoBranchFetchResult/);
    expect(page).toMatch(/purchaseBranchLabel/);
    expect(page).toMatch(/api\.branches\(\)/);
    expect(page).toMatch(/readOnly/);
    expect(page).toMatch(/createBranchFetchGen/);
  });

  test("create payload does not send client branch_id (server-authoritative YGY-first)", () => {
    expect(page).toMatch(/createPurchaseOrder\(\{/);
    const start = page.indexOf("createPurchaseOrder({");
    const end = page.indexOf("});", start);
    const payload = page.slice(start, end);
    expect(payload).not.toMatch(/branch_id/);
    // Create path must stay free of client branch override; legacy completion is a separate action.
    expect(payload).not.toMatch(/setPurchaseOrderBranch/);
  });

  test("YGY aktif → label benar", () => {
    const label = resolveCreatePoBranchLabel({
      kind: "ok",
      items: [
        { id: "branch-smg", code: "SMG", name: "Semarang", active: true },
        { id: "branch-ygy", code: "YGY", name: "Yogyakarta", active: true },
      ],
    });
    expect(label).toBe("Yogyakarta (YGY)");
    expect(label).not.toMatch(/branch-ygy/);
  });

  test("API sukses tanpa YGY → pesan missing (bukan — dan bukan fake YGY)", () => {
    const label = resolveCreatePoBranchLabel({
      kind: "ok",
      items: [{ id: "branch-smg", code: "SMG", name: "Semarang", active: true }],
    });
    expect(label).toBe(PO_CREATE_BRANCH.missing);
    expect(label).not.toBe("—");
    expect(label).not.toBe("Yogyakarta (YGY)");
  });

  test("HTTP 403 → pesan akses", () => {
    expect(resolveCreatePoBranchLabel({ kind: "http", status: 403 })).toBe(
      PO_CREATE_BRANCH.forbidden,
    );
  });

  test("Network/API error → pesan kegagalan", () => {
    expect(resolveCreatePoBranchLabel({ kind: "error" })).toBe(PO_CREATE_BRANCH.error);
    expect(resolveCreatePoBranchLabel({ kind: "http", status: 500 })).toBe(PO_CREATE_BRANCH.error);
  });

  test("Loading state → tampil benar", () => {
    expect(resolveCreatePoBranchLabel({ kind: "loading" })).toBe(PO_CREATE_BRANCH.loading);
    expect(resolveCreatePoBranchLabel(null)).toBe(PO_CREATE_BRANCH.loading);
    expect(page).toMatch(/PO_CREATE_BRANCH\.loading/);
  });

  test("PO legacy dengan branch_id null → em dash (detail helper)", () => {
    expect(purchaseBranchLabel({})).toBe("—");
    expect(purchaseBranchLabel({ branch_id: null })).toBe("—");
    expect(page).toMatch(/purchaseBranchLabel\(detail\)/);
  });

  test("tidak membuat fake branch_id; empty success bukan invent YGY", () => {
    expect(resolveCreatePoBranchLabel({ kind: "ok", items: [] })).toBe(PO_CREATE_BRANCH.missing);
    expect(page).not.toMatch(/branch_id:\s*["']branch-/);
  });

  test("stale response tidak menimpa state terbaru", () => {
    const stale = applyCreatePoBranchFetchResult(1, 2, false, {
      kind: "ok",
      items: [{ id: "x", code: "YGY", name: "Yogyakarta", active: true }],
    });
    expect(stale).toBeNull();
    const unmounted = applyCreatePoBranchFetchResult(2, 2, true, {
      kind: "ok",
      items: [{ id: "x", code: "YGY", name: "Yogyakarta", active: true }],
    });
    expect(unmounted).toBeNull();
    const fresh = applyCreatePoBranchFetchResult(3, 3, false, {
      kind: "ok",
      items: [{ id: "x", code: "YGY", name: "Yogyakarta", active: true }],
    });
    expect(fresh).toBe("Yogyakarta (YGY)");
  });
});
