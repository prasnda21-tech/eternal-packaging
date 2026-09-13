import WorkspaceChunkFallback from "./WorkspaceChunkFallback";

describe("WorkspaceChunkFallback", () => {
  test("exports compact in-content loading copy", () => {
    expect(typeof WorkspaceChunkFallback).toBe("function");
    const src = require("fs").readFileSync(require("path").join(__dirname, "WorkspaceChunkFallback.jsx"), "utf8");
    expect(src).toMatch(/Memuat halaman\.\.\./);
    expect(src).toMatch(/muted small/);
    expect(src).not.toMatch(/className="loading"/);
  });
});
