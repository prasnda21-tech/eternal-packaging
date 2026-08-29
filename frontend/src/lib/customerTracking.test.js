import { trackingDisplayLabel, trackingStatusClass, trackingStatusLabel } from "./customerTracking";

describe("customer tracking status labels", () => {
  test("maps the three public statuses only", () => {
    expect(trackingStatusLabel("completed")).toBe("Selesai");
    expect(trackingStatusLabel("in_progress")).toBe("Proses");
    expect(trackingStatusLabel("waiting")).toBe("Menunggu");
    expect(trackingStatusLabel("bypass")).toBe("Menunggu");
    expect(trackingStatusLabel("error")).toBe("Menunggu");
  });

  test("prefers Alur Pekerjaan label from payload", () => {
    expect(trackingDisplayLabel({ status: "in_progress", label: "Menunggu bukti pengiriman" }))
      .toBe("Menunggu bukti pengiriman");
    expect(trackingDisplayLabel({ status: "completed" })).toBe("Selesai");
  });

  test("badge classes stay compact", () => {
    expect(trackingStatusClass("completed")).toMatch(/is-done/);
    expect(trackingStatusClass("in_progress")).toMatch(/is-run/);
    expect(trackingStatusClass("waiting")).toMatch(/is-wait/);
  });
});
