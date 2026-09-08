import {
  canOpenPublicApprovalDesign,
  isPublicApprovalPdf,
  preferCompactPdfPreview,
  resolvePublicApprovalDesignUrl,
} from "./publicApprovalDesign";

describe("public approval design URL", () => {
  const file = { id: "d1", name: "APPROVAL NILU.pdf", content_type: "application/pdf" };

  test("resolves token-scoped view URL independently of inline preview", () => {
    const url = resolvePublicApprovalDesignUrl("tok-abc", file, {
      apiBase: "http://localhost:8000/api",
      status: "active",
    });
    expect(url).toBe("http://localhost:8000/api/public/customer-approval/tok-abc/design");
    expect(url).not.toMatch(/undefined/);
    expect(url).not.toBe("");
  });

  test("prefers absolute fallback over relative view_url so mobile opens the backend", () => {
    const url = resolvePublicApprovalDesignUrl("tok-abc", {
      ...file,
      view_url: "/api/public/customer-approval/tok-abc/design",
    }, {
      apiBase: "http://localhost:8000/api",
      status: "active",
      fallbackUrl: "http://localhost:8000/api/public/customer-approval/tok-abc/design",
    });
    expect(url).toBe("http://localhost:8000/api/public/customer-approval/tok-abc/design");
  });

  test("prefixes relative view_url when only apiBase is provided", () => {
    const url = resolvePublicApprovalDesignUrl("tok-abc", {
      ...file,
      view_url: "/api/public/customer-approval/tok-abc/design",
    }, { apiBase: "http://localhost:8000/api", status: "active" });
    expect(url).toBe("http://localhost:8000/api/public/customer-approval/tok-abc/design");
  });

  test("does not fall back to empty data_url", () => {
    const url = resolvePublicApprovalDesignUrl("tok-abc", { ...file, data_url: null }, {
      apiBase: "http://localhost:8000/api",
      status: "active",
    });
    expect(url).toContain("/public/customer-approval/tok-abc/design");
  });

  test("blocks open URL when approval link is no longer viewable", () => {
    expect(canOpenPublicApprovalDesign("expired")).toBe(false);
    expect(resolvePublicApprovalDesignUrl("tok-abc", file, {
      apiBase: "http://localhost:8000/api",
      status: "expired",
    })).toBe("");
  });

  test("detects PDF from name or content type", () => {
    expect(isPublicApprovalPdf(file)).toBe(true);
    expect(isPublicApprovalPdf({ name: "x.png", content_type: "image/png" })).toBe(false);
  });
});

describe("compact PDF fallback", () => {
  test("uses compact fallback when the browser reports no PDF viewer", () => {
    const original = navigator.pdfViewerEnabled;
    Object.defineProperty(navigator, "pdfViewerEnabled", { configurable: true, value: false });
    expect(preferCompactPdfPreview()).toBe(true);
    Object.defineProperty(navigator, "pdfViewerEnabled", { configurable: true, value: original });
  });

  test("prefers compact preview on mobile viewport", () => {
    const original = window.matchMedia;
    window.matchMedia = (query) => ({
      matches: query.includes("max-width: 768px"),
      media: query,
      addEventListener() {},
      removeEventListener() {},
    });
    expect(preferCompactPdfPreview()).toBe(true);
    window.matchMedia = original;
  });
});
