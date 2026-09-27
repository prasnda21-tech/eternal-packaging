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

  test("Lihat PDF uses the in-app viewer and the token-scoped design URL", () => {
    expect(src).toMatch(/publicCustomerApprovalDesignUrl/);
    expect(src).toMatch(/resolvePublicApprovalDesignUrl/);
    expect(src).toMatch(/InAppDocumentViewer/);
    expect(src).toMatch(/credentials: "omit"/);
    expect(src).not.toMatch(/target="_blank"/);
    expect(src).not.toMatch(/<iframe/);
    expect(src).not.toMatch(/<object/);
    expect(src).not.toMatch(/localStorage/);
    expect(src).not.toMatch(/download=\{view\.design_file\.name\}/);
    expect(src).not.toMatch(/href=\{url\}/);
  });

  test("narrow screens use the same in-app PDF viewer", () => {
    expect(src).toMatch(/public-approval-design-pdf-viewer/);
    expect(src).not.toMatch(/preferCompactPdfPreview/);
    expect(src).not.toMatch(/<iframe/);
    expect(src).not.toMatch(/height:\s*480/);
  });

  test("Lihat PDF opens the viewer and Download PDF stays a download", () => {
    expect(src).toMatch(/public-approval-design-pdf-toggle/);
    expect(src).toMatch(/public-approval-design-pdf-download/);
    expect(src).toMatch(/resolvePublicApprovalDesignDownloadUrl/);
    expect(src).toMatch(/download=\{file\.name \|\| "design\.pdf"\}/);
    expect(src).toMatch(/Download PDF/);
    expect(src).toMatch(/Lihat PDF/);
    expect(src).toMatch(/allowDownload/);
  });

  test("PDF actions are Lihat PDF and Download PDF in the same row", () => {
    expect(src).toMatch(/data-testid="public-approval-design-pdf-actions"/);
    expect(src).toMatch(/data-testid="public-approval-design-pdf-toggle"/);
    expect(src).toMatch(/data-testid="public-approval-design-pdf-download"/);
    expect(src).not.toMatch(/Buka di Tab Baru/);
    expect(src).not.toMatch(/public-approval-design-pdf-open/);
  });

  test("external origin PDFs are recorded and not proxied", () => {
    expect(src).toMatch(/isCrossOriginDocumentUrl/);
    expect(src).toMatch(/Preview PDF dari origin lain tidak dapat dimuat di aplikasi/);
    expect(src).not.toMatch(/target="_blank"/);
  });

  test("preview bytes are requested on open and are not stored in localStorage", () => {
    expect(src).toMatch(/cache: "no-store"/);
    expect(src).not.toMatch(/localStorage/);
    expect(src).not.toMatch(/URL\.createObjectURL/);
  });

  test("approval submit path is unchanged", () => {
    expect(src).toMatch(/publicCustomerApprovalSubmit\(token,/);
    expect(src).toMatch(/action === "approve"/);
    expect(src).toMatch(/action === "reject"/);
  });

  test("public approval design page hard lock: never shows delete or hapus button", () => {
    expect(src).not.toMatch(/Hapus/i);
    expect(src).not.toMatch(/delete/i);
  });

});
