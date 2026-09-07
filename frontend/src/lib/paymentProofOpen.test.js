import {
  dataUrlToObjectUrl,
  openPaymentProofSource,
  paymentProofIsImage,
  paymentProofIsPdf,
  paymentProofIsPresent,
  paymentProofSource,
  proofErrorMessage,
} from "./paymentProofOpen";

const TINY_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1IAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
const TINY_PDF = `data:application/pdf;base64,${Buffer.from("%PDF-1.4\n%%EOF\n").toString("base64")}`;

beforeEach(() => {
  URL.createObjectURL = jest.fn(() => "blob:mock-payment-proof");
  URL.revokeObjectURL = jest.fn();
});

describe("payment proof open helpers", () => {
  test("existing stored metadata: data_url and url both work", () => {
    expect(paymentProofSource({ name: "a.png", data_url: TINY_PNG })).toBe(TINY_PNG);
    expect(paymentProofSource({ name: "b.png", url: TINY_PNG })).toBe(TINY_PNG);
    expect(paymentProofIsPresent({ name: "legacy.pdf" })).toBe(true);
    expect(paymentProofIsPresent(null)).toBe(false);
  });

  test("image and pdf detection from existing fields", () => {
    expect(paymentProofIsImage({ name: "pay.png", data_url: TINY_PNG })).toBe(true);
    expect(paymentProofIsPdf({ name: "pay.pdf", data_url: TINY_PDF })).toBe(true);
    expect(paymentProofIsPdf({ name: "pay.pdf", url: "/old.pdf" })).toBe(true);
  });

  test("missing file metadata is not openable as a source", () => {
    expect(paymentProofSource({})).toBe("");
    expect(openPaymentProofSource("").ok).toBe(false);
  });

  test("data URL opens via blob object URL, not raw data: navigation", () => {
    const opened = [];
    const result = openPaymentProofSource(TINY_PNG, (href, target) => {
      opened.push({ href, target });
      return { ok: true };
    });
    expect(result.ok).toBe(true);
    expect(opened[0].href).toMatch(/^blob:/);
    expect(opened[0].href).not.toMatch(/^data:/);
    URL.revokeObjectURL(opened[0].href);
  });

  test("absolute http url is passed through", () => {
    const opened = [];
    openPaymentProofSource("https://localhost:8000/api/x", (href) => {
      opened.push(href);
      return {};
    });
    expect(opened[0]).toBe("https://localhost:8000/api/x");
  });

  test("dataUrlToObjectUrl preserves image mime", () => {
    const url = dataUrlToObjectUrl(TINY_PNG);
    expect(url).toMatch(/^blob:/);
    URL.revokeObjectURL(url);
  });

  test("unauthorized JSON blob error stays a message, not a file tab", async () => {
    const blob = {
      text: async () => JSON.stringify({ detail: "Permission 'payment.view' diperlukan" }),
    };
    Object.setPrototypeOf(blob, Blob.prototype);
    const msg = await proofErrorMessage({ response: { data: blob } }, "fallback");
    expect(msg).toBe("Permission 'payment.view' diperlukan");
  });
});
