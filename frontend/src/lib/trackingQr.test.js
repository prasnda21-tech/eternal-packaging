jest.mock("qrcode", () => ({
  toDataURL: jest.fn(async (value) => `data:image/png;base64,${Buffer.from(String(value)).toString("base64")}`),
}));

import { trackingQrDataUrl } from "./trackingQr";

describe("Live Tracking QR", () => {
  test("encodes the tracking URL and does not call a shortener", async () => {
    const url = "https://produksi.eternalpackaging.id/t/frutavor/PO-2026-10-02-000012/fixture-code";
    const dataUrl = await trackingQrDataUrl(url);
    const encoded = dataUrl.slice("data:image/png;base64,".length);
    expect(Buffer.from(encoded, "base64").toString("utf8")).toBe(url);
    expect(dataUrl).not.toMatch(/bit\.ly|tinyurl/i);
    expect(await trackingQrDataUrl("")).toBe("");
  });
});
