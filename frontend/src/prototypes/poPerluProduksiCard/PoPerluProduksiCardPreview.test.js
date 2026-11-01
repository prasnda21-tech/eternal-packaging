import { act } from "react";
import { createRoot } from "react-dom/client";
import PoPerluProduksiCardPreview from "./PoPerluProduksiCardPreview";
import {
  PO_PERLU_DRILL_ANY_OF,
  PO_PERLU_DRILL_PATH,
  formatPoCount,
} from "./poPerluProduksiPreviewShared";

jest.mock("lucide-react", () => {
  const React = require("react");
  const Icon = (props) => React.createElement("span", props);
  return {
    ArrowUpRight: Icon,
    Printer: Icon,
    MoreHorizontal: Icon,
    X: Icon,
  };
});

function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(<PoPerluProduksiCardPreview />);
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
  expect(formatPoCount(36)).toBe("36");
});

test("drill path and permission fixture follow perlu produksi audit", () => {
  expect(PO_PERLU_DRILL_PATH).toBe(
    "/dashboard/rincian?dashboard=produksi&widget=perlu_produksi",
  );
  expect(PO_PERLU_DRILL_ANY_OF).toEqual([
    "nav.tasks.kepala_produksi",
    "schedule.edit",
  ]);
});

test("preview renders variants with unit PO and locked/forbidden behavior", () => {
  const view = mount();
  expect(view.host.querySelector("[data-testid='ppp-metric-normal']").textContent).toContain("36");
  expect(view.host.querySelector("[data-testid='ppp-metric-normal']").textContent).toContain("PO");
  expect(view.host.querySelector("[data-testid='ppp-metric-long']").textContent).toContain("12.345");
  expect(view.host.querySelector("[data-testid='ppp-card-zero']").textContent).toContain("Tidak ada PO perlu produksi");
  expect(view.host.querySelector("[data-testid='ppp-loading-loading']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='ppp-forbidden-forbidden']").textContent).toContain("Tidak ada izin");
  expect(view.host.querySelector("[data-testid='ppp-metric-forbidden']")).toBeNull();
  expect(view.host.querySelector("[data-testid='ppp-locked-locked']").textContent).toContain("Akses rincian terbatas");
  expect(view.host.querySelector("[data-testid='ppp-drill-locked']")).toBeNull();
  expect(view.host.querySelector("[data-testid='ppp-drill-normal']")).toBeTruthy();
  const largeText = view.host.querySelector("[data-testid='ppp-card-large']").textContent;
  expect(largeText).toContain("PO unik · Snapshot saat ini");
  expect(largeText.match(/Snapshot saat ini/g) || []).toHaveLength(1);
  expect(largeText).not.toContain("Siap diproduksi");
  view.unmount();
});

test("body and drill share simulated navigation path", () => {
  const view = mount();
  act(() => {
    view.host.querySelector("[data-testid='ppp-body-normal']").click();
  });
  expect(view.host.querySelector("[data-testid='ppp-sim-path']").textContent).toBe(PO_PERLU_DRILL_PATH);
  act(() => {
    view.host.querySelector("[data-testid='ppp-reset']").click();
  });
  expect(view.host.querySelector("[data-testid='ppp-sim-path']")).toBeNull();
  act(() => {
    view.host.querySelector("[data-testid='ppp-drill-large']").click();
  });
  expect(view.host.querySelector("[data-testid='ppp-sim-path']").textContent).toBe(PO_PERLU_DRILL_PATH);
  view.unmount();
});

test("locked and edit mode do not navigate; menu remove is local only", () => {
  const view = mount();
  act(() => {
    view.host.querySelector("[data-testid='ppp-body-locked']").click();
  });
  expect(view.host.querySelector("[data-testid='ppp-sim-path']")).toBeNull();

  act(() => {
    view.host.querySelector("[data-testid='ppp-edit-toggle']").click();
  });
  act(() => {
    view.host.querySelector("[data-testid='ppp-body-normal']").click();
  });
  expect(view.host.querySelector("[data-testid='ppp-sim-path']")).toBeNull();

  act(() => {
    view.host.querySelector("[data-testid='ppp-menu-editable']").click();
  });
  act(() => {
    view.host.querySelector("[data-testid='ppp-remove-editable']").click();
  });
  expect(view.host.querySelector("[data-testid='ppp-card-editable']")).toBeNull();
  act(() => {
    view.host.querySelector("[data-testid='ppp-reset']").click();
  });
  expect(view.host.querySelector("[data-testid='ppp-card-editable']")).toBeTruthy();
  view.unmount();
});

test("info widget opens and Escape closes", () => {
  const view = mount();
  const menu = view.host.querySelector("[data-testid='ppp-menu-normal']");
  act(() => {
    menu.focus();
    menu.click();
  });
  act(() => {
    view.host.querySelector("[data-testid='ppp-info-normal']").click();
  });
  expect(view.host.querySelector("[data-testid='ppp-info-panel-normal']").textContent)
    .toContain("sisa cetak");
  expect(view.host.querySelector("[data-testid='ppp-info-panel-normal']").textContent)
    .toContain("Bukan sisa penjadwalan");
  act(() => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  expect(view.host.querySelector("[data-testid='ppp-info-panel-normal']")).toBeNull();
  view.unmount();
});
