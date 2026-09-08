import fs from "fs";
import path from "path";

describe("AttachmentsPanel Design File Delete & Lifecycle", () => {
  const panelSrc = fs.readFileSync(path.join(__dirname, "AttachmentsPanel.jsx"), "utf8");
  const caPanelSrc = fs.readFileSync(path.join(__dirname, "CustomerApprovalPanel.jsx"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const publicSrc = fs.readFileSync(path.join(__dirname, "../pages/PublicCustomerApproval.jsx"), "utf8");

  test("1, 2: internal Design UI provides Hapus File Design action gated by permission", () => {
    expect(panelSrc).toMatch(/kind === "design" && canUploadDesign/);
    expect(panelSrc).toMatch(/Hapus File Design/);
    expect(panelSrc).toMatch(/data-testid=\{`delete-design-file-\$\{f\.id\}`\}/);
    expect(panelSrc).toMatch(/data-testid=\{`view-\$\{kind\}-file-\$\{f\.id\}`\}/);
  });

  test("3: destructive action requires confirmation modal displaying exact file metadata", () => {
    expect(panelSrc).toMatch(/data-testid="delete-design-confirm-modal"/);
    expect(panelSrc).toMatch(/Hapus file design ini\?/);
    expect(panelSrc).toMatch(/data-testid="delete-design-filename"/);
    expect(panelSrc).toMatch(/File attachment akan dihapus dari Design/);
    expect(panelSrc).toMatch(/Record revisi dan history tetap dipertahankan/);
  });

  test("4: cancel button closes modal with zero mutation", () => {
    expect(panelSrc).toMatch(/data-testid="cancel-delete-design-button"/);
    expect(panelSrc).toMatch(/setPendingDeleteDesign\(null\)/);
  });

  test("5, 6, 8: confirm button calls api.deleteDesignFile with exact file id", () => {
    expect(panelSrc).toMatch(/data-testid="confirm-delete-design-button"/);
    expect(panelSrc).toMatch(/api\.deleteDesignFile\(order\.id,\s*pendingDeleteDesign\.id\)/);
    expect(panelSrc).toMatch(/onUpdated\?\.\(res\.order \|\| res\)/);
    expect(apiSrc).toMatch(/deleteDesignFile:\s*\(id,\s*fileId\)\s*=>/);
    expect(apiSrc).toMatch(/\/orders\/\$\{id\}\/design\/files\//);
  });

  test("7: error does not fake success and displays error message", () => {
    expect(panelSrc).toMatch(/data-testid="delete-design-error"/);
    expect(panelSrc).toMatch(/setDeleteErr/);
  });

  test("9: customer approval panel displays error when no valid active PDF is available", () => {
    expect(caPanelSrc).toMatch(/data-testid="ca-error"/);
    expect(caPanelSrc).toMatch(/formatApiError\(e,\s*"Gagal membuat link approval"\)/);
  });

  test("10: public Approval Design page never renders Hapus File Design control", () => {
    expect(publicSrc).not.toMatch(/Hapus File Design/);
    expect(publicSrc).not.toMatch(/deleteDesignFile/);
    expect(publicSrc).not.toMatch(/delete-design-file/);
  });
});
