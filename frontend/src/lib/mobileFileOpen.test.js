import { browserSafeOpenTarget, designFileMeta, latestDesignFile, openBrowserSafeFile } from "./mobileFileOpen";

describe("mobile file open", () => {
  test("prefers authenticated storage URL over data_url", () => {
    const target = browserSafeOpenTarget({
      storage_path: "designs/a.pdf",
      data_url: "data:application/pdf;base64,AAA",
      name: "desain.pdf",
    });
    expect(target.ok).toBe(true);
    expect(target.mode).toBe("url");
    expect(target.kind).toBe("pdf");
    expect(target.href).toMatch(/\/files\/designs\/a\.pdf$/);
  });

  test("opens browser-safe URL without inventing a new token scheme", () => {
    const opened = [];
    const result = openBrowserSafeFile(
      { url: "https://example.test/desain.pdf", name: "desain.pdf" },
      (href, target, features) => {
        opened.push({ href, target, features });
        return { ok: true };
      },
    );
    expect(result.opened).toBe(true);
    expect(opened[0].href).toBe("https://example.test/desain.pdf");
    expect(opened[0].target).toBe("_blank");
  });

  test("falls back to existing data_url when no URL exists", () => {
    const target = browserSafeOpenTarget({ data_url: "data:image/png;base64,xx", name: "sample.png" });
    expect(target.ok).toBe(true);
    expect(target.mode).toBe("data");
    expect(target.kind).toBe("image");
  });

  test("latest design file and meta", () => {
    const order = {
      design_status: "Approved",
      design_files: [{ name: "old.pdf" }, { name: "final.pdf", content_type: "application/pdf" }],
    };
    expect(latestDesignFile(order).name).toBe("final.pdf");
    expect(designFileMeta(order).approval).toBe("Approved");
    expect(designFileMeta(order).type).toMatch(/pdf/i);
  });
});
