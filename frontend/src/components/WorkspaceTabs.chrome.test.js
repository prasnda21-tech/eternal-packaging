/**
 * Focused tests for desktop workspace/history tab chrome styling.
 */
import fs from "fs";
import path from "path";
import {
  WORKSPACE_TAB_PREVIEW_FLAG,
  mergeWorkspaceTabsPreview,
} from "../lib/workspaceTabsPreview";

const tabsSrc = fs.readFileSync(path.join(__dirname, "WorkspaceTabs.jsx"), "utf8");
const shellSrc = fs.readFileSync(path.join(__dirname, "AppShell.jsx"), "utf8");
const cssSrc = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
const previewSrc = fs.readFileSync(path.join(__dirname, "../lib/workspaceTabsPreview.js"), "utf8");

describe("Desktop workspace tab chrome", () => {
  test("A. Multiple workspace tabs render from tabs prop", () => {
    expect(tabsSrc).toMatch(/tabs\.map\(\(tab\)/);
    expect(tabsSrc).toMatch(/data-workspace-tab="1"/);
    expect(shellSrc).toMatch(/mergeWorkspaceTabsPreview/);
  });

  test("B. Every workspace tab uses bordered kb-workspace-tab structure", () => {
    expect(tabsSrc).toMatch(/className=\{\"kb-workspace-tab\" \+ \(isActive \? \" is-active\" : \"\"\)\}/);
    expect(cssSrc).toMatch(/\.kb-workspace-tab\{[^}]*border:\s*1px solid var\(--line\)/);
  });

  test("C. Active tab receives is-active class", () => {
    expect(tabsSrc).toMatch(/isActive \? \" is-active\"/);
    expect(tabsSrc).toMatch(/data-active=\{isActive \? \"1\" : \"0\"\}/);
    expect(cssSrc).toMatch(/\.kb-workspace-tab\.is-active\{/);
  });

  test("D. Active tab has stronger typography contract", () => {
    expect(cssSrc).toMatch(/\.kb-workspace-tab\.is-active \.kb-workspace-tab-label\{[^}]*font-weight:\s*700/);
    expect(cssSrc).toMatch(/\.kb-workspace-tab-label\{[^}]*font:\s*600/);
  });

  test("E. Inactive tabs do not receive active-only style", () => {
    expect(tabsSrc).toMatch(/isActive \? \" is-active\" : \"\"/);
    expect(cssSrc).toMatch(/\.kb-workspace-tab\{[^}]*color:\s*var\(--ink\)/);
    expect(cssSrc).not.toMatch(/kb-workspace-tab--history/);
  });

  test("F. Active styling follows activePath / isActive, not page-name hardcoding", () => {
    expect(tabsSrc).toMatch(/activeId === tab\.id/);
    expect(tabsSrc).not.toMatch(/Jadwal Pengiriman/);
    expect(tabsSrc).not.toMatch(/Riwayat Payroll/);
    expect(tabsSrc).not.toMatch(/hr-payroll-riwayat/);
    expect(shellSrc).not.toMatch(/hr-payroll-riwayat/);
    expect(shellSrc).not.toMatch(/workspaceHistoryTab/);
  });

  test("G. Close button remains rendered", () => {
    expect(tabsSrc).toMatch(/kb-workspace-tab-close/);
    expect(tabsSrc).toMatch(/onClose\?\.\(tab\.id\)/);
    expect(tabsSrc).toMatch(/Tutup \$\{tab\.label\}/);
  });

  test("H. No History/Payroll-specific hardcoding remains", () => {
    expect(tabsSrc).not.toMatch(/isWorkspaceHistoryTab|WORKSPACE_HISTORY_TAB|data-history-tab|kb-workspace-tab--history/);
    expect(cssSrc).not.toMatch(/kb-workspace-tab--history|History workspace tab only/);
    expect(shellSrc).not.toMatch(/mergeHistoryTabPreview|previewHistoryTab/);
  });

  test("I. Dev-only preview is absent from production behavior", () => {
    expect(previewSrc).toMatch(/process\.env\.NODE_ENV !== \"development\"/);
    expect(previewSrc).toMatch(WORKSPACE_TAB_PREVIEW_FLAG);
    const originalEnv = process.env.NODE_ENV;
    process.env.NODE_ENV = "production";
    const productionTabs = mergeWorkspaceTabsPreview(
      [{ id: "dashboard", path: "/dashboard", label: "Dashboard" }],
      `?${WORKSPACE_TAB_PREVIEW_FLAG}=1`,
    );
    expect(productionTabs).toEqual([{ id: "dashboard", path: "/dashboard", label: "Dashboard" }]);
    process.env.NODE_ENV = "development";
    const previewTabs = mergeWorkspaceTabsPreview([], `?${WORKSPACE_TAB_PREVIEW_FLAG}=1`);
    expect(previewTabs.length).toBeGreaterThanOrEqual(1);
    process.env.NODE_ENV = originalEnv;
  });
});
