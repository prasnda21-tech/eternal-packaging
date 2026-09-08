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

  test("open PDF is a native link to the token-scoped design URL", () => {
    expect(src).toMatch(/publicCustomerApprovalDesignUrl/);
    expect(src).toMatch(/resolvePublicApprovalDesignUrl/);
    expect(src).toMatch(/href=\{designUrl\}/);
    expect(src).toMatch(/target="_blank"/);
    expect(src).toMatch(/Buka Design PDF/);
    expect(src).not.toMatch(/download=\{view\.design_file\.name\}/);
    expect(src).not.toMatch(/href=\{url\}/);
  });

  test("mobile fallback is compact and not tied to iframe support", () => {
    expect(src).toMatch(/preferCompactPdfPreview/);
    expect(src).toMatch(/design-pdf-fallback/);
    expect(src).toMatch(/Preview PDF tidak tersedia di browser ini/);
    expect(src).toMatch(/compactPdf && \(/);
    expect(src).toMatch(/!compactPdf && canOpen/);
    expect(src).not.toMatch(/height:\s*480/);
  });

  test("desktop viewer provides Lihat PDF toggle and download PDF fallback", () => {
    expect(src).toMatch(/public-approval-design-pdf-toggle/);
    expect(src).toMatch(/public-approval-design-pdf-download/);
    expect(src).toMatch(/resolvePublicApprovalDesignDownloadUrl/);
    expect(src).toMatch(/download=\{file\.name \|\| "design\.pdf"\}/);
    expect(src).toMatch(/Download PDF/);
    expect(src).toMatch(/Lihat PDF/);
    expect(src).toMatch(/Tutup Preview/);
  });

  test("desktop fallback message informs user clearly when inline rendering fails", () => {
    expect(src).toMatch(/PDF tidak dapat ditampilkan langsung di browser ini\. Buka di tab baru atau download PDF\./);
  });

  test("lifecycle manages blob URL with revokeObjectURL", () => {
    expect(src).toMatch(/URL\.createObjectURL/);
    expect(src).toMatch(/URL\.revokeObjectURL/);
  });

  test("approval submit path is unchanged", () => {
    expect(src).toMatch(/publicCustomerApprovalSubmit\(token,/);
    expect(src).toMatch(/action === "approve"/);
    expect(src).toMatch(/action === "reject"/);
  });
});
