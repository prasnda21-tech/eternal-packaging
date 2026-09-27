import { isCrossOriginDocumentUrl } from "./pdfDocument";

describe("isCrossOriginDocumentUrl", () => {
  const original = process.env.REACT_APP_BACKEND_URL;

  afterEach(() => {
    process.env.REACT_APP_BACKEND_URL = original;
  });

  test("same-page, relative, data, and configured API origins can be fetched", () => {
    process.env.REACT_APP_BACKEND_URL = "https://api.eternal.test";
    expect(isCrossOriginDocumentUrl("")).toBe(false);
    expect(isCrossOriginDocumentUrl("/api/files/a.pdf")).toBe(false);
    expect(isCrossOriginDocumentUrl("data:application/pdf;base64,QQ==")).toBe(false);
    expect(isCrossOriginDocumentUrl("https://api.eternal.test/api/files/a.pdf")).toBe(false);
    expect(isCrossOriginDocumentUrl(window.location.origin + "/api/files/a.pdf")).toBe(false);
  });

  test("other http origins stay outside the viewer", () => {
    process.env.REACT_APP_BACKEND_URL = "https://api.eternal.test";
    expect(isCrossOriginDocumentUrl("https://files.example/desain.pdf")).toBe(true);
  });
});
