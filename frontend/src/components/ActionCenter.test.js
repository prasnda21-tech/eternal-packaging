import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { dashboardActionItems: jest.fn() },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: () => true }),
}));

jest.mock("../lib/useAutoRefresh", () => () => {});

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : "#"} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
}), { virtual: true });

describe("Tugas Saya routing", () => {
  const src = fs.readFileSync(path.join(__dirname, "ActionCenter.jsx"), "utf8");
  test("uses taskPoHref not raw /po/id for all cards", () => {
    expect(src).toMatch(/taskPoHref/);
    expect(src).not.toMatch(/to=\{`\/po\/\$\{o\.id\}`\}/);
    expect(src).not.toMatch(/& Tim/);
  });

  test("division cards redirect to /tasks submenu", () => {
    expect(src).toMatch(/actionKeyToTaskPath/);
    expect(src).toMatch(/navigate\(href\)/);
  });

  test("design approve status uses existing field", () => {
    expect(src).toMatch(/design_status/);
    expect(src).toMatch(/design_approve/);
  });
});

describe("Action Center maps design and scheduling to division tasks", () => {
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  test("design_upload and penjadwalan use existing task paths", () => {
    expect(nav).toMatch(/design_upload:\s*TASK_NAV_PATHS\.designer/);
    expect(nav).toMatch(/penjadwalan:\s*TASK_NAV_PATHS\.kepala_produksi/);
  });
});

describe("Action Center incomplete read", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.dashboardActionItems.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("shows the read cap only when completeness is incomplete", async () => {
    api.dashboardActionItems.mockResolvedValue({
      actions: [],
      completeness: "incomplete",
      read_limit: 2000,
      matched_count: 4000,
    });
    const { default: ActionCenter } = await import("./ActionCenter");
    await act(async () => { root.render(<ActionCenter />); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    const note = container.querySelector("[data-testid='ac-incomplete']");
    expect(note.textContent).toContain("Tugas belum lengkap");
    expect(note.textContent).toContain("2000");
    expect(note.textContent).not.toContain("4000");
  });

  test("keeps an API error and hides the incomplete note", async () => {
    api.dashboardActionItems.mockRejectedValue({ response: { data: { detail: "Gagal memuat" } } });
    const { default: ActionCenter } = await import("./ActionCenter");
    await act(async () => { root.render(<ActionCenter />); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(container.querySelector("[data-testid='action-center-error']").textContent).toContain("Gagal memuat");
    expect(container.querySelector("[data-testid='ac-incomplete']")).toBeNull();
  });
});
