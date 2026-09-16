import { act } from "react";
import { createRoot } from "react-dom/client";
import useProductionSheetZoom from "./useProductionSheetZoom";
import {
  PRODUCTION_SHEET_ZOOM_CSS_VAR,
  PRODUCTION_SHEET_ZOOM_DEFAULT,
  PRODUCTION_SHEET_ZOOM_MAX,
  PRODUCTION_SHEET_ZOOM_MIN,
  PRODUCTION_SHEET_ZOOM_STORAGE_KEY,
  productionSheetZoomFromPinch,
  productionSheetZoomFromWheel,
  productionSheetZoomStyle,
  shouldSuppressSheetRowClick,
} from "../lib/productionSheetZoom";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("useProductionSheetZoom live gestures", () => {
  let container;
  let root;
  let renders;
  let rafNow;
  let harnessApi;

  const viewportEl = () => container.querySelector('[data-testid="zoom-viewport"]');
  const tableEl = () => container.querySelector(".production-sheet-table");
  const zoomVar = (el = viewportEl()) => el?.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR);

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
    harnessApi = null;
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
      harnessApi = sheetZoom;
      return (
        <div
          data-testid="zoom-viewport"
          className="production-sheet-viewport"
          ref={sheetZoom.setViewportRef}
          style={productionSheetZoomStyle(sheetZoom.zoom)}
        >
          <table className="production-sheet-table">
            <tbody>
              <tr>
                <td>cell</td>
              </tr>
            </tbody>
          </table>
        </div>
      );
    }
    await act(async () => { root.render(<ZoomHarness />); });
    const viewport = viewportEl();
    viewport.getBoundingClientRect = () => ({
      left: 10, top: 20, width: 400, height: 240, right: 410, bottom: 260,
    });
    Object.defineProperty(viewport, "clientWidth", { configurable: true, value: 400 });
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 240 });
    viewport.scrollLeft = 80;
    viewport.scrollTop = 40;
  };

  const dispatchWheel = async (opts) => {
    const viewport = viewportEl();
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        clientX: 190,
        clientY: 110,
        ...opts,
      }));
    });
  };

  const dispatchTouches = async (type, points) => {
    const viewport = viewportEl();
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, "touches", {
      value: points.map((point, identifier) => ({
        identifier,
        clientX: point.x,
        clientY: point.y,
      })),
    });
    await act(async () => { viewport.dispatchEvent(event); });
  };

  test("ctrlKey=false wheel does not zoom", async () => {
    await renderHarness();
    await dispatchWheel({ ctrlKey: false, deltaY: 240 });
    await flushRafs(3);
    expect(Number(zoomVar())).toBe(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(Number(zoomVar(tableEl())) || PRODUCTION_SHEET_ZOOM_DEFAULT).toBe(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(shouldSuppressSheetRowClick(harnessApi.gestureRef.current)).toBe(false);
  });

  test("ctrlKey=true trackpad-style wheel zooms continuously on rAF", async () => {
    await renderHarness();
    const first = productionSheetZoomFromWheel(PRODUCTION_SHEET_ZOOM_DEFAULT, 16);
    const second = productionSheetZoomFromWheel(first, 16);
    const third = productionSheetZoomFromWheel(second, 16);
    await dispatchWheel({ ctrlKey: true, deltaY: 16 });
    await dispatchWheel({ ctrlKey: true, deltaY: 16 });
    await dispatchWheel({ ctrlKey: true, deltaY: 16 });
    await flushRaf();
    expect(Number(zoomVar())).toBe(third);
    expect(Number(zoomVar())).toBeLessThan(second);
    expect(second).toBeLessThan(first);
    expect(first).toBeLessThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(Math.abs(first - PRODUCTION_SHEET_ZOOM_DEFAULT)).not.toBeCloseTo(0.05, 3);
  });

  test("wheel zoom respects min and max", async () => {
    await renderHarness();
    for (let i = 0; i < 40; i += 1) {
      await dispatchWheel({ ctrlKey: true, deltaY: 800 });
    }
    await flushRaf();
    expect(Number(zoomVar())).toBe(PRODUCTION_SHEET_ZOOM_MIN);
    for (let i = 0; i < 80; i += 1) {
      await dispatchWheel({ ctrlKey: true, deltaY: -800 });
    }
    await flushRaf();
    expect(Number(zoomVar())).toBe(PRODUCTION_SHEET_ZOOM_MAX);
  });

  test("trackpad pointer position is the zoom anchor", async () => {
    await renderHarness();
    const viewport = viewportEl();
    const beforeLeft = viewport.scrollLeft;
    const beforeTop = viewport.scrollTop;
    await dispatchWheel({ ctrlKey: true, deltaY: -240, clientX: 190, clientY: 110 });
    await flushRaf();
    const next = Number(zoomVar());
    expect(next).toBeGreaterThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(viewport.scrollLeft).toBeCloseTo((beforeLeft + 180) * next - 180, 5);
    expect(viewport.scrollTop).toBeCloseTo((beforeTop + 90) * next - 90, 5);
    expect(viewport.scrollLeft).not.toBe(0);
    expect(viewport.scrollTop).not.toBe(0);
  });

  test("touch pinch still zooms 1:1 from the midpoint", async () => {
    await renderHarness();
    const viewport = viewportEl();
    const beforeLeft = viewport.scrollLeft;
    const beforeTop = viewport.scrollTop;
    const target = productionSheetZoomFromPinch(PRODUCTION_SHEET_ZOOM_DEFAULT, 100, 150);
    await dispatchTouches("touchstart", [{ x: 10, y: 30 }, { x: 110, y: 30 }]);
    await dispatchTouches("touchmove", [{ x: 10, y: 30 }, { x: 160, y: 30 }]);
    await flushRaf();
    expect(Number(zoomVar())).toBe(target);
    expect(viewport.scrollLeft).toBeCloseTo((beforeLeft + 75) * target - 75, 5);
    expect(viewport.scrollTop).toBeCloseTo((beforeTop + 10) * target - 10, 5);
    await dispatchTouches("touchend", []);
    expect(Number(zoomVar())).toBe(target);
  });

  test("one-finger touch does not start a pinch", async () => {
    await renderHarness();
    await dispatchTouches("touchstart", [{ x: 12, y: 8 }]);
    await flushRaf();
    expect(Number(zoomVar())).toBe(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(shouldSuppressSheetRowClick(harnessApi.gestureRef.current)).toBe(false);
  });

  test("changing the zoom variable applies it to the table geometry path", async () => {
    await renderHarness();
    await dispatchWheel({ ctrlKey: true, deltaY: 120 });
    await flushRaf();
    const scale = Number(zoomVar());
    expect(scale).toBeLessThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(zoomVar(tableEl())).toBe(String(scale));
    expect(tableEl().dataset.productionSheetZoom).toBe(String(scale));
    expect(viewportEl().dataset.productionSheetZoom).toBe(String(scale));
  });

  test("row click is suppressed after trackpad pinch and after touch pinch", async () => {
    await renderHarness();
    await dispatchWheel({ ctrlKey: true, deltaY: 40 });
    await flushRaf();
    expect(shouldSuppressSheetRowClick(harnessApi.gestureRef.current)).toBe(true);
    expect(harnessApi.consumeClickSuppression()).toBe(true);
    expect(harnessApi.consumeClickSuppression()).toBe(false);

    await dispatchTouches("touchstart", [{ x: 10, y: 10 }, { x: 110, y: 10 }]);
    await dispatchTouches("touchmove", [{ x: 10, y: 10 }, { x: 180, y: 10 }]);
    await flushRaf();
    await dispatchTouches("touchend", []);
    expect(shouldSuppressSheetRowClick(harnessApi.gestureRef.current)).toBe(true);
    expect(harnessApi.consumeClickSuppression()).toBe(true);
  });

  test("two-finger rest does not consume a normal click", async () => {
    await renderHarness();
    await dispatchTouches("touchstart", [{ x: 10, y: 10 }, { x: 110, y: 10 }]);
    expect(harnessApi.gestureRef.current.pinching).toBe(true);
    expect(shouldSuppressSheetRowClick(harnessApi.gestureRef.current)).toBe(false);
    expect(harnessApi.consumeClickSuppression()).toBe(false);
  });

  test("ctrl+wheel does not permanently disable the next real click", async () => {
    await renderHarness();
    await dispatchWheel({ ctrlKey: true, deltaY: 40 });
    await flushRaf();
    expect(shouldSuppressSheetRowClick(harnessApi.gestureRef.current)).toBe(true);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 320));
    });
    expect(shouldSuppressSheetRowClick(harnessApi.gestureRef.current)).toBe(false);
    expect(harnessApi.consumeClickSuppression()).toBe(false);
  });

  test("ordinary wheel does not arm click suppression", async () => {
    await renderHarness();
    await dispatchWheel({ ctrlKey: false, deltaY: 240 });
    await flushRaf();
    expect(shouldSuppressSheetRowClick(harnessApi.gestureRef.current)).toBe(false);
    expect(harnessApi.consumeClickSuppression()).toBe(false);
  });

  test("after pinch suppression is consumed, the next click is not suppressed", async () => {
    await renderHarness();
    await dispatchTouches("touchstart", [{ x: 10, y: 10 }, { x: 110, y: 10 }]);
    await dispatchTouches("touchmove", [{ x: 10, y: 10 }, { x: 180, y: 10 }]);
    await flushRaf();
    await dispatchTouches("touchend", []);
    expect(harnessApi.consumeClickSuppression()).toBe(true);
    expect(harnessApi.consumeClickSuppression()).toBe(false);
  });

  test("live wheel does not re-render React per frame", async () => {
    await renderHarness();
    const rendersAfterMount = renders;
    await dispatchWheel({ ctrlKey: true, deltaY: -80 });
    await flushRafs(3);
    expect(Number(zoomVar())).toBe(productionSheetZoomFromWheel(PRODUCTION_SHEET_ZOOM_DEFAULT, -80));
    expect(renders).toBe(rendersAfterMount);
    expect(window.localStorage.getItem(PRODUCTION_SHEET_ZOOM_STORAGE_KEY)).toBeNull();
  });
});
