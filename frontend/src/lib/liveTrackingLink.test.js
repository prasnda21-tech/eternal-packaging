import { trackingAbsoluteUrl, trackingShareFromLink } from "./liveTrackingLink";

const ORIGIN = "https://produksi.eternalpackaging.id";
const CODE = "fixture-code-not-a-production-token";
const PATH = `/t/frutavor/PO-2026-10-02-000012/${CODE}`;

describe("Live Tracking share URL", () => {
  test("copy, WhatsApp, and QR use one absolute pretty URL", () => {
    const share = trackingShareFromLink({
      origin: ORIGIN,
      link: {
        token: CODE,
        path: PATH,
        public_path: PATH,
        legacy_path: `/tracking/${CODE}`,
        po_label: "single",
      },
      customerName: "Frutavor",
      poNumber: "PO-2026-10-02-000012",
    });
    const expected = `${ORIGIN}${PATH}`;
    expect(share.url).toBe(expected);
    expect(share.qrValue).toBe(expected);
    expect(share.legacyUrl).toBe(`${ORIGIN}/tracking/${CODE}`);
    expect(share.whatsappHref.startsWith("https://wa.me/?text=")).toBe(true);
    const message = decodeURIComponent(share.whatsappHref.slice("https://wa.me/?text=".length));
    expect(message).toContain(expected);
    expect(message).toContain("Frutavor");
    expect(message).toContain("PO-2026-10-02-000012");
    expect(message).not.toContain("02/10/2026");
  });

  test("encoded segments stay encoded and are not replaced by an external shortener", () => {
    const path = "/t/caf%C3%A9-ni%C3%B1o-co/PO-2026-10-02-000012/kode_acak-1";
    const share = trackingShareFromLink({
      origin: "http://localhost:3000",
      link: { token: "kode_acak-1", public_path: path, path, po_label: "single" },
      customerName: "Café Niño & Co.",
      poNumber: "PO/2026/10/02/000012",
    });
    expect(share.url).toBe(`http://localhost:3000${path}`);
    expect(share.url).not.toMatch(/bit\.ly|tinyurl|is\.gd|goo\.gl/i);
    expect(decodeURIComponent(share.whatsappHref.split("text=")[1])).toContain(share.url);
    expect(share.qrValue).toBe(share.url);
  });

  test("missing and multiple PO labels are not replaced with an invented number", () => {
    const missing = trackingShareFromLink({
      origin: ORIGIN,
      link: {
        token: CODE,
        public_path: `/t/frutavor/tanpa-po/${CODE}`,
        po_label: "missing",
      },
      customerName: "Frutavor",
      poNumber: "",
    });
    expect(missing.url).toContain("/tanpa-po/");
    expect(missing.message).not.toMatch(/PO-/);
    const many = trackingShareFromLink({
      origin: ORIGIN,
      link: {
        token: CODE,
        public_path: `/t/frutavor/beberapa-po/${CODE}`,
        po_label: "multiple",
      },
      customerName: "Frutavor",
      poNumber: "PO-2026-10-02-000012",
    });
    expect(many.url).toContain("/beberapa-po/");
    expect(many.url).not.toContain("PO-2026-10-02-000012");
    expect(many.message).not.toContain("PO-2026-10-02-000012");
  });

  test("an older API response still builds the legacy tracking path", () => {
    const share = trackingShareFromLink({
      origin: ORIGIN,
      link: { token: CODE, path: `/tracking/${CODE}` },
      customerName: "Frutavor",
      poNumber: "PO-1",
    });
    expect(share.url).toBe(`${ORIGIN}/tracking/${CODE}`);
    expect(trackingAbsoluteUrl(ORIGIN, "//evil.example/t/a/b/c")).toBe("");
    expect(trackingAbsoluteUrl("notaurl", PATH)).toBe("");
  });
});
