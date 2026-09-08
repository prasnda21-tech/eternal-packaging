import { act } from "react";
import { createRoot } from "react-dom/client";
import useProductionSheetZoom from "./useProductionSheetZoom";
import {
  PRODUCTION_SHEET_ZOOM_CSS_VAR,
  PRODUCTION_SHEET_ZOOM_DEFAULT,
  PRODUCTION_SHEET_ZOOM_STORAGE_KEY,
  productionSheetZoomFromPinch,
  productionSheetZoomFromWheel,
  productionSheetZoomStyle,
} from "../lib/productionSheetZoom";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("useProductionSheetZoom display smoothing", () => {
  let container;
  let root;
  let renders;
  let rafNow;

  const zoomVar = () => container.querySelector('[data-testid="zoom-viewport"]')
    ?.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR);

  const flushRaf = async () => {
    await act(async () => {
      await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    });
  };

  const flushRafs = async (times) => {
    for (let i = 0; i < times; i += 1) await flushRaf();
  };

  beforeEach(() => {
    window.localStorage.clear();
    renders = 0;
    rafNow = 0;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    jest.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => (
      window.setTimeout(() => {
        rafNow += 1000 / 120;
        cb(rafNow);
      }, 0)
    ));
    jest.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => window.clearTimeout(id));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    window.requestAnimationFrame.mockRestore();
    window.cancelAnimationFrame.mockRestore();
  });

  const renderHarness = async () => {
    function ZoomHarness() {
      renders += 1;
      const sheetZoom = useProductionSheetZoom();
      return (
        <div
          data-testid="zoom-viewport"
          ref={sheetZoom.setViewportRef}
          style={productionSheetZoomStyle(sheetZoom.zoom)}
        />
      );
    }
    await act(async () => { root.render(<ZoomHarness />); });
  };

  test("wheel updates target immediately while display eases without React state per frame", async () => {
    await renderHarness();
    const viewport = container.querySelector('[data-testid="zoom-viewport"]');
    const target = productionSheetZoomFromWheel(PRODUCTION_SHEET_ZOOM_DEFAULT, -80);
    const rendersAfterMount = renders;
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", {
        ctrlKey: true, deltaY: -80, bubbles: true, cancelable: true, clientX: 40, clientY: 20,
      }));
    });
    await flushRafs(2);
    const afterOne = Number(zoomVar());
    expect(afterOne).toBeGreaterThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(afterOne).toBeLessThan(target);
    expect(window.localStorage.getItem(PRODUCTION_SHEET_ZOOM_STORAGE_KEY)).toBeNull();
    expect(renders).toBe(rendersAfterMount);
    await flushRafs(4);
    const afterFew = Number(zoomVar());
    expect(afterFew).toBeGreaterThan(afterOne);
    expect(afterFew).toBeLessThanOrEqual(target);
    expect(renders).toBe(rendersAfterMount);
    expect(window.localStorage.getItem(PRODUCTION_SHEET_ZOOM_STORAGE_KEY)).toBeNull();
  });

  test("a second wheel during easing retargets without resetting display to the start", async () => {
    await renderHarness();
    const viewport = container.querySelector('[data-testid="zoom-viewport"]');
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", {
        ctrlKey: true, deltaY: -240, bubbles: true, cancelable: true,
      }));
    });
    await flushRafs(3);
    const mid = Number(zoomVar());
    expect(mid).toBeGreaterThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", {
        ctrlKey: true, deltaY: 240, bubbles: true, cancelable: true,
      }));
    });
    await flushRaf();
    const reversed = Number(zoomVar());
    expect(reversed).toBeLessThan(mid);
    expect(reversed).not.toBe(PRODUCTION_SHEET_ZOOM_DEFAULT);
  });

  test("pinch target math is unchanged and display interpolates toward it", async () => {
    await renderHarness();
    const viewport = container.querySelector('[data-testid="zoom-viewport"]');
    const target = productionSheetZoomFromPinch(PRODUCTION_SHEET_ZOOM_DEFAULT, 100, 150);
    const dispatchTouches = (type, points) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(event, "touches", {
        value: points.map((point, identifier) => ({
          identifier,
          clientX: point.x,
          clientY: point.y,
        })),
      });
      viewport.dispatchEvent(event);
    };
    await act(async () => {
      dispatchTouches("touchstart", [{ x: 10, y: 10 }, { x: 110, y: 10 }]);
      dispatchTouches("touchmove", [{ x: 10, y: 10 }, { x: 160, y: 10 }]);
    });
    await flushRafs(2);
    const afterOne = Number(zoomVar());
    expect(afterOne).toBeGreaterThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(afterOne).toBeLessThan(target);
    await flushRafs(4);
    expect(Number(zoomVar())).toBeGreaterThan(afterOne);
    expect(Number(zoomVar())).toBeLessThanOrEqual(target);
  });
});
