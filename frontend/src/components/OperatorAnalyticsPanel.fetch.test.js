import { act } from "react";
import { createRoot } from "react-dom/client";
import OperatorAnalyticsPanel from "./OperatorAnalyticsPanel";
import api from "../lib/api";

jest.mock("react-router-dom", () => ({
  Link: ({ children }) => <a>{children}</a>,
}), { virtual: true });

global.IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  api.analyticsOperatorKpis.mockResolvedValue({ rows: [], filters: {}, kpis: {} });
  api.analyticsPreparation.mockResolvedValue({ rows: [], filters: {}, kpis: {} });
});

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    analyticsOperatorKpis: jest.fn(() => Promise.resolve({ rows: [], filters: {}, kpis: {} })),
    analyticsPreparation: jest.fn(() => Promise.resolve({ rows: [], filters: {}, kpis: {} })),
    analyticsScreener: jest.fn(),
    analyticsDriver: jest.fn(),
    analyticsDesigns: jest.fn(),
    analyticsOutputExport: jest.fn(),
  },
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

test("analytics refetches when the tab changes and ignores a stale response", async () => {
  let resolveOperator;
  api.analyticsOperatorKpis.mockImplementationOnce(() => new Promise((resolve) => { resolveOperator = resolve; }));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<OperatorAnalyticsPanel />); });
  const helper = host.querySelector("[data-testid='op-an-tab-helper']");
  expect(helper).toBeTruthy();
  await act(async () => { helper.click(); await flush(); });
  expect(api.analyticsPreparation).toHaveBeenCalled();
  await act(async () => {
    resolveOperator({ rows: [{ id: "stale" }], filters: {}, kpis: { stale: true } });
    await flush();
  });
  expect(host.textContent).not.toContain("stale");
  expect(host.querySelector("[data-testid='op-an-refresh']").disabled).toBe(false);
  await act(async () => { root.unmount(); });
  host.remove();
});

test("custom period without both dates releases a busy in-flight request", async () => {
  let resolveOperator;
  api.analyticsOperatorKpis.mockImplementationOnce(() => new Promise((resolve) => { resolveOperator = resolve; }));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<OperatorAnalyticsPanel />); });
  expect(host.querySelector("[data-testid='op-an-refresh']").disabled).toBe(true);
  const period = host.querySelector("[data-testid='op-an-period']");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
    setter.call(period, "custom");
    period.dispatchEvent(new Event("change", { bubbles: true }));
    await flush();
  });
  expect(api.analyticsOperatorKpis).toHaveBeenCalledTimes(1);
  expect(host.querySelector("[data-testid='op-an-refresh']").disabled).toBe(false);
  await act(async () => {
    resolveOperator({ rows: [{ id: "stale" }], filters: {}, kpis: { stale: true } });
    await flush();
  });
  expect(host.textContent).not.toContain("stale");
  expect(host.querySelector("[data-testid='op-an-refresh']").disabled).toBe(false);
  await act(async () => { root.unmount(); });
  host.remove();
});
