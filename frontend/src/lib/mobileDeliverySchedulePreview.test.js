import {
  DUMMY_DATA_COUNT,
  PWA_DELIVERY_SJ_PREVIEW_PARAM,
  getPwaDeliverySjPreviewProductionRows,
  getPwaDeliverySjPreviewRows,
  getPwaDeliverySjPreviewScenarios,
  isPwaDeliverySjPreviewEnabled,
} from "./mobileDeliverySchedulePreview";
import { pwaDeliveryScheduleRows, sjItemLinesFromRow } from "./mobileDeliverySchedule";

describe("PWA delivery SJ preview fixture", () => {
  const origEnv = process.env.NODE_ENV;

  afterEach(() => {
    process.env.NODE_ENV = origEnv;
  });

  test("requires development and exact query flag", () => {
    process.env.NODE_ENV = "development";
    expect(isPwaDeliverySjPreviewEnabled(`?${PWA_DELIVERY_SJ_PREVIEW_PARAM}=1`)).toBe(true);
    expect(isPwaDeliverySjPreviewEnabled(`${PWA_DELIVERY_SJ_PREVIEW_PARAM}=1`)).toBe(true);
    expect(isPwaDeliverySjPreviewEnabled("?previewPwaDeliverySj=0")).toBe(false);
    process.env.NODE_ENV = "production";
    expect(isPwaDeliverySjPreviewEnabled(`?${PWA_DELIVERY_SJ_PREVIEW_PARAM}=1`)).toBe(false);
  });

  test("exposes exactly 3 dummy scenarios and does not leak invalid SJ into visible rows", () => {
    expect(DUMMY_DATA_COUNT).toBe(3);
    const scenarios = getPwaDeliverySjPreviewScenarios();
    expect(scenarios).toHaveLength(3);
    expect(scenarios.map((s) => s.id)).toEqual(["dummy-1", "dummy-2", "dummy-3"]);
    const payload = getPwaDeliverySjPreviewRows();
    const visible = pwaDeliveryScheduleRows(payload);
    expect(payload.some((r) => r.order_number === "PO-DUMMY-2")).toBe(true);
    expect(visible.some((r) => r.order_number === "PO-DUMMY-2")).toBe(false);
    expect(visible.filter((r) => r.order_number === "PO-DUMMY-1")).toHaveLength(1);
    expect(sjItemLinesFromRow(visible.find((r) => r.order_number === "PO-DUMMY-1"))).toHaveLength(3);
    expect(visible.filter((r) => r.order_number === "PO-DUMMY-3")).toHaveLength(2);
    expect(visible.map((r) => r.shipment_id).sort()).toEqual(["sh-dummy-1", "sh-dummy-3a", "sh-dummy-3b"]);
    expect(getPwaDeliverySjPreviewProductionRows().every((r) => r.id.startsWith("preview-d"))).toBe(true);
  });
});
