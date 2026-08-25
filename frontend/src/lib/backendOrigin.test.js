import { resolveBackendOrigin } from "./backendOrigin";

describe("resolveBackendOrigin", () => {
  test("aligns 127.0.0.1 API to a localhost page so auth cookies stay first-party", () => {
    expect(resolveBackendOrigin("http://127.0.0.1:8000", "localhost")).toBe(
      "http://localhost:8000"
    );
  });

  test("aligns localhost API to a 127.0.0.1 page", () => {
    expect(resolveBackendOrigin("http://localhost:8000", "127.0.0.1")).toBe(
      "http://127.0.0.1:8000"
    );
  });

  test("keeps matching loopback hosts unchanged", () => {
    expect(resolveBackendOrigin("http://localhost:8000", "localhost")).toBe(
      "http://localhost:8000"
    );
  });

  test("does not rewrite a production API host", () => {
    expect(resolveBackendOrigin("https://api.example.com", "localhost")).toBe(
      "https://api.example.com"
    );
  });
});
