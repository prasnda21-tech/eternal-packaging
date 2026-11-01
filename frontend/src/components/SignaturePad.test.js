import { act } from "react";
import { createRoot } from "react-dom/client";
import SignaturePad from "./SignaturePad";

global.IS_REACT_ACT_ENVIRONMENT = true;

// 1x1 PNG
const TINY_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

function mount(ui) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(ui);
  });
  return {
    host,
    rerender(next) {
      act(() => {
        root.render(next);
      });
    },
    unmount() {
      act(() => root.unmount());
      host.remove();
    },
  };
}

describe("SignaturePad draft restore", () => {
  beforeAll(() => {
    // jsdom canvas stubs
    HTMLCanvasElement.prototype.getContext = () => ({
      setTransform() {},
      scale() {},
      clearRect() {},
      drawImage() {},
      beginPath() {},
      moveTo() {},
      lineTo() {},
      stroke() {},
      lineWidth: 1,
      lineCap: "round",
      lineJoin: "round",
      strokeStyle: "#111",
    });
    HTMLCanvasElement.prototype.toDataURL = () => TINY_PNG;
    HTMLCanvasElement.prototype.getBoundingClientRect = () => ({
      width: 320, height: 160, top: 0, left: 0, right: 320, bottom: 160, x: 0, y: 0, toJSON() {},
    });
  });

  beforeEach(() => {
    class MockImage {
      set src(v) {
        this._src = v;
        queueMicrotask(() => {
          if (String(v).includes("not-valid")) this.onerror?.(new Error("bad"));
          else this.onload?.();
        });
      }
      get src() { return this._src; }
    }
    global.Image = MockImage;
  });

  test("paints value draft and reports ready; failed image reports failed and clears via onChange", async () => {
    const states = [];
    const changes = [];
    const { host, unmount } = mount(
      <SignaturePad
        value={TINY_PNG}
        onRestoreState={(s) => states.push(s)}
        onChange={(v) => changes.push(v)}
      />
    );

    // Allow image onload microtasks
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(states).toContain("restoring");
    expect(states).toContain("ready");
    expect(host.querySelector('[data-testid="signature-empty-hint"]')).toBeNull();
    expect(host.querySelector('[data-testid="signature-pad"]').getAttribute("data-restore")).toBe("ready");
    expect(host.querySelector('[data-testid="signature-restore-failed"]')).toBeNull();

    // Failed restore
    const states2 = [];
    const changes2 = [];
    const bad = mount(
      <SignaturePad
        value="data:image/png;base64,not-valid!!!"
        onRestoreState={(s) => states2.push(s)}
        onChange={(v) => changes2.push(v)}
      />
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(states2).toContain("failed");
    expect(changes2).toContain(null);
    expect(bad.host.querySelector('[data-testid="signature-restore-failed"]')).toBeTruthy();
    expect(bad.host.querySelector('[data-testid="signature-empty-hint"]')).toBeTruthy();
    bad.unmount();
    unmount();
  });

  test("clear empties canvas and emits onChange(null)", async () => {
    const changes = [];
    const { host, unmount } = mount(
      <SignaturePad value={TINY_PNG} onChange={(v) => changes.push(v)} />
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    });
    const clearBtn = host.querySelector('[data-testid="signature-clear-button"]');
    expect(clearBtn.disabled).toBe(false);
    await act(async () => { clearBtn.click(); });
    expect(changes).toContain(null);
    expect(host.querySelector('[data-testid="signature-empty-hint"]')).toBeTruthy();
    unmount();
  });
});
