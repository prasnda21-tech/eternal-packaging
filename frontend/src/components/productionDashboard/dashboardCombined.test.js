import { act } from "react";
import { createRoot } from "react-dom/client";
import ProductionWidgetBoard from "./ProductionWidgetBoard";
import FinanceWidgetBoard from "../financeDashboard/FinanceWidgetBoard";
import { buildKpiCards } from "../../lib/dashboardRincianPresentation";

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => {
  const React = require("react");
  return {
    Link: ({ to, children, ...props }) => React.createElement("a", { href: to, ...props }, children),
    useNavigate: () => mockNavigate,
  };
}, { virtual: true });
global.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
beforeEach(() => {
  host = document.createElement("div"); document.body.appendChild(host);
  root = createRoot(host); mockNavigate.mockClear();
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

const widget = (id, h = 3) => ({ widget_id: id, x: 0, y: 0, w: 4, h, settings: {} });
const drill = { path: "/dashboard/rincian?dashboard=produksi&widget=po_aktif&basis=snapshot", any_of: ["nav.reports.status_po"], also_any_of: ["schedule.view"] };
function production(id, model, hasPerm = () => true, editing = false) {
  act(() => root.render(<ProductionWidgetBoard widgets={[widget(id, 2)]} models={{ [id]: model }} hasPerm={hasPerm} editing={editing} onLayout={jest.fn()} />));
}
function finance(id, model, settings = {}) {
  act(() => root.render(<FinanceWidgetBoard widgets={[{ ...widget(id, 4), settings }]} models={{ [id]: model }} hasPerm={() => true} editing={false} onLayout={jest.fn()} onSettings={jest.fn()} periodForWidget={() => ({ start: "2026-10-01", end: "2026-10-31" })} globalPreset="month" />));
}

test("count card exposes PO unit and footer path, including zero", () => {
  production("po_aktif", { status: "zero", count: 0, drill });
  expect(host.querySelector(".kb-metric").textContent).toBe("0 PO");
  expect(host.querySelector("article footer a").getAttribute("href")).toBe(drill.path);
  act(() => host.querySelector(".kb-body").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(mockNavigate).toHaveBeenCalledWith(drill.path);
});

test("locked count retains KPI without a link, editing removes navigation", () => {
  production("po_aktif", { status: "complete", count: 12345, drill }, p => p === "dashboard.production.view");
  expect(host.querySelector(".kb-metric").textContent).toBe("12.345 PO");
  expect(host.querySelector("footer").textContent).toBe("Akses rincian terbatas");
  expect(host.querySelector("a")).toBeNull();
  production("po_aktif", { status: "complete", count: 12345, drill }, () => true, true);
  expect(host.querySelector("footer")).toBeNull();
  expect(host.querySelector("[role=link]")).toBeNull();
});

test("remaining quantities never compare different units with bar lengths", () => {
  production("sisa_qty", { status: "complete", byUnit: { pcs: 1000, box: 5 } });
  expect(host.querySelectorAll(".pd-unit-values dd")).toHaveLength(2);
  expect(host.querySelector(".pd-track")).toBeNull();
  expect(host.textContent).toContain("1.000 pcs");
  expect(host.textContent).toContain("5 box");
});

test("all trend buckets remain visible and zero has no artificial height", () => {
  const points = Array.from({ length: 40 }, (_, i) => ({ label: `Day ${i}`, amount: i === 0 ? 0 : 100 }));
  finance("trend", { status: "complete", points, grain: "Harian" });
  const bars = host.querySelectorAll(".kb-bar i");
  expect(bars).toHaveLength(40);
  expect(bars[0].style.height).toBe("0%");
  expect(bars[1].style.height).toBe("100%");
  expect(host.querySelector(".kb-bars").getAttribute("aria-label")).toContain("Day 0");
});

test("all-zero trend renders a zero series and signed values preserve sign", () => {
  finance("trend", { status: "zero", points: [{ label: "A", amount: 0 }, { label: "B", amount: 0 }] });
  expect([...host.querySelectorAll(".kb-bar i")].every(n => n.style.height === "0%")).toBe(true);
  finance("trend", { status: "complete", points: [{ label: "A", amount: -50 }, { label: "B", amount: 100 }] });
  expect(host.querySelector(".kb-bar i").style.bottom).toBe("0%");
  expect(host.querySelectorAll(".kb-bar i")[1].style.bottom).not.toBe("0%");
});

test("reconciliation expand really exposes all returned rows", () => {
  finance("recon", { status: "complete", rows: Array.from({ length: 15 }, (_, i) => ({ key: `${i}`, name: `Bank ${i}`, statusLabel: "Draft", amount: 0 })), pairs: [] });
  expect(host.textContent).not.toContain("Bank 14");
  act(() => [...host.querySelectorAll("button")].find(n => n.textContent === "Lihat Semua").click());
  expect(host.textContent).toContain("Bank 14");
});

test("KPI quantities stay separate and profit/expense zero remains money", () => {
  const cards = buildKpiCards("sisa_qty", { by_unit: { pcs: 100, box: 5 }, count: 1, line_count: 2, grain: "line" });
  expect(cards.map(c => c.value)).toEqual(["100 pcs", "5 box", "1 PO", "2"]);
  expect(buildKpiCards("profit", { net_profit: 0, count: 3 })[0].value).toBe("Rp0");
  expect(buildKpiCards("expense", { amount: -20, count: 1, grain: "account" })[0].value).toBe("-Rp20");
});
