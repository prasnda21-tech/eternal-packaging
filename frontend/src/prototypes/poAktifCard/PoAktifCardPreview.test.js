import { act } from "react";
import { createRoot } from "react-dom/client";
import PoAktifCardPreview from "./PoAktifCardPreview";
import { PO_AKTIF_DRILL_PATH, formatPoCount } from "./poAktifPreviewShared";

jest.mock("lucide-react", () => {
  const React = require("react");
  const Icon = (props) => React.createElement("span", props);
  return {
    ArrowUpRight: Icon,
    Layers: Icon,
    MoreHorizontal: Icon,
    X: Icon,
  };
});

function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(<PoAktifCardPreview />);
  });
  return {
    host,
    unmount() {
      act(() => root.unmount());
      host.remove();
    },
  };
}

test("formatPoCount uses id-ID grouping", () => {
  expect(formatPoCount(12345)).toBe("12.345");
  expect(formatPoCount(128)).toBe("128");
});

test("preview renders variants with unit PO and no drill on locked/forbidden", () => {
  const view = mount();
  expect(view.host.querySelector("[data-testid='pac-metric-normal']").textContent).toContain("128");
  expect(view.host.querySelector("[data-testid='pac-metric-normal']").textContent).toContain("PO");
  expect(view.host.querySelector("[data-testid='pac-metric-long']").textContent).toContain("12.345");
  expect(view.host.querySelector("[data-testid='pac-metric-zero']").textContent).toContain("0");
  expect(view.host.querySelector("[data-testid='pac-card-zero']").textContent).toContain("Tidak ada PO aktif");
  expect(view.host.querySelector("[data-testid='pac-loading-loading']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='pac-forbidden-forbidden']").textContent).toContain("Tidak ada izin");
  expect(view.host.querySelector("[data-testid='pac-metric-forbidden']")).toBeNull();
  expect(view.host.querySelector("[data-testid='pac-locked-locked']").textContent).toContain("Akses rincian terbatas");
  expect(view.host.querySelector("[data-testid='pac-drill-locked']")).toBeNull();
  expect(view.host.querySelector("[data-testid='pac-drill-normal']")).toBeTruthy();
  const largeText = view.host.querySelector("[data-testid='pac-card-large']").textContent;
  expect(largeText).toContain("PO unik · Snapshot saat ini");
  expect(largeText.match(/Snapshot saat ini/g) || []).toHaveLength(1);
  view.unmount();
});

test("body and drill share simulated navigation path", () => {
  const view = mount();
  act(() => {
    view.host.querySelector("[data-testid='pac-body-normal']").click();
  });
  expect(view.host.querySelector("[data-testid='pac-sim-path']").textContent).toBe(PO_AKTIF_DRILL_PATH);
  act(() => {
    view.host.querySelector("[data-testid='pac-reset']").click();
  });
  expect(view.host.querySelector("[data-testid='pac-sim-path']")).toBeNull();
  act(() => {
    view.host.querySelector("[data-testid='pac-drill-large']").click();
  });
  expect(view.host.querySelector("[data-testid='pac-sim-path']").textContent).toBe(PO_AKTIF_DRILL_PATH);
  view.unmount();
});

test("locked and edit mode do not navigate; menu remove is local only", () => {
  const view = mount();
  act(() => {
    view.host.querySelector("[data-testid='pac-body-locked']").click();
  });
  expect(view.host.querySelector("[data-testid='pac-sim-path']")).toBeNull();

  act(() => {
    view.host.querySelector("[data-testid='pac-edit-toggle']").click();
  });
  act(() => {
    view.host.querySelector("[data-testid='pac-body-normal']").click();
  });
  expect(view.host.querySelector("[data-testid='pac-sim-path']")).toBeNull();

  act(() => {
    view.host.querySelector("[data-testid='pac-menu-editable']").click();
  });
  act(() => {
    view.host.querySelector("[data-testid='pac-remove-editable']").click();
  });
  expect(view.host.querySelector("[data-testid='pac-card-editable']")).toBeNull();
  act(() => {
    view.host.querySelector("[data-testid='pac-reset']").click();
  });
  expect(view.host.querySelector("[data-testid='pac-card-editable']")).toBeTruthy();
  view.unmount();
});

test("info widget opens and Escape restores focus path", () => {
  const view = mount();
  const menu = view.host.querySelector("[data-testid='pac-menu-normal']");
  act(() => {
    menu.focus();
    menu.click();
  });
  act(() => {
    view.host.querySelector("[data-testid='pac-info-normal']").click();
  });
  expect(view.host.querySelector("[data-testid='pac-info-panel-normal']").textContent)
    .toContain("PO unik non-batal");
  act(() => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  expect(view.host.querySelector("[data-testid='pac-info-panel-normal']")).toBeNull();
  view.unmount();
});
