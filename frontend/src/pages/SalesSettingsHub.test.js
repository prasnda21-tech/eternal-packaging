import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { settingsAliasFromPath, settingsTabFromPath } from "../lib/navAccess";
import api from "../lib/api";
import SalesSettingsHub from "./SalesSettingsHub";

const hubSrc = fs.readFileSync(path.join(__dirname, "SalesSettingsHub.jsx"), "utf8");
const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");

let mockSearch = "section=semua";
const mockNavigate = jest.fn();
let mockHasPerm = () => true;

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: "/settings/pengaturan-sales", search: mockSearch.startsWith("?") ? mockSearch : `?${mockSearch}` }),
  useSearchParams: () => [new URLSearchParams(mockSearch.startsWith("?") ? mockSearch.slice(1) : mockSearch)],
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", role: "admin" },
    hasPerm: (key) => mockHasPerm(key),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {},
}));

jest.mock("../components/SalesMasterSettings", () => ({
  __esModule: true,
  default: function MockMaster({ canEdit, focusSalesId }) {
    return (
      <div data-testid="sales-master-tab" data-can-edit={String(!!canEdit)} data-focus={focusSalesId || ""}>
        master-reuse
      </div>
    );
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("SalesSettingsHub", () => {
  let container;
  let root;

  beforeEach(() => {
    mockNavigate.mockReset();
    mockHasPerm = () => true;
    mockSearch = "section=semua";
    Object.assign(api, {
      salesMasterList: jest.fn(async () => ({
        items: [{ id: "s1", sales_name: "Ayub", user_id: "u1", branch_ids: ["jkt"], active: true }],
      })),
      salesCandidatesList: jest.fn(async () => ({
        items: [{ id: "c1", exact_label: "New Sales", status: "pending", seen_count: 3 }],
      })),
      salesCandidateApproveNew: jest.fn(async () => ({ message: "ok", master: { id: "s2" } })),
      salesCandidateLink: jest.fn(async () => ({ message: "linked" })),
      adminListUsers: jest.fn(async () => ({ users: [{ id: "u1", name: "Ayub", role: "sales_eternal" }] })),
      branches: jest.fn(async () => ({ items: [{ id: "jkt", name: "Jakarta", active: true }] })),
      adminAuditLogs: jest.fn(async () => ({
        logs: [
          { id: "a1", action: "settings.sales_master.update", at: "2026-01-01T00:00:00Z", note: "update s1" },
          { id: "a2", action: "orders.sales.update", at: "2026-01-01T00:00:00Z", note: "should hide" },
        ],
      })),
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  test("source reuses single Master editor and has no separate Mapping page editor", () => {
    expect(hubSrc).toMatch(/SalesMasterSettings/);
    expect(hubSrc).not.toMatch(/SalesUserMappingSettings/);
    expect(hubSrc).toMatch(/Daftarkan sebagai Sales baru/);
    expect(hubSrc).toMatch(/Hubungkan/);
    expect(hubSrc).not.toMatch(/salesMasterCreate|salesMasterUpdate|updateSalesUserMapping/);
    expect(settingsSrc).toMatch(/id:\s*"pengaturan-sales"/);
    expect(settingsSrc).toMatch(/SalesSettingsHub/);
  });

  test("legacy settings paths alias to pengaturan-sales semua", () => {
    expect(settingsAliasFromPath("/settings/sales-master")).toEqual({
      tab: "pengaturan-sales",
      section: "semua",
    });
    expect(settingsAliasFromPath("/settings/sales-user-mapping")).toEqual({
      tab: "pengaturan-sales",
      section: "semua",
    });
    expect(settingsTabFromPath("/settings/sales-master")).toBe("pengaturan-sales");
    expect(settingsTabFromPath("/settings/pengaturan-sales", "?section=verifikasi")).toBe("pengaturan-sales");
  });

  test("renders Semua Sales and does not grant edit without canEditMaster", async () => {
    await act(async () => {
      root.render(
        <SalesSettingsHub
          notify={() => {}}
          canViewMaster
          canEditMaster={false}
          canViewMapping
          canEditMapping={false}
        />,
      );
    });
    expect(container.querySelector('[data-testid="ps-nav-semua"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ps-nav-verifikasi"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ps-nav-riwayat"]')).toBeTruthy();
    const master = container.querySelector('[data-testid="sales-master-tab"]');
    expect(master).toBeTruthy();
    expect(master.getAttribute("data-can-edit")).toBe("false");
  });

  test("verifikasi panel lists candidates readonly without manage", async () => {
    mockSearch = "section=verifikasi";
    await act(async () => {
      root.render(
        <SalesSettingsHub
          notify={() => {}}
          canViewMaster
          canEditMaster={false}
          canViewMapping
          canEditMapping={false}
        />,
      );
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(container.querySelector('[data-testid="ps-verifikasi-panel"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ps-verifikasi-readonly"]')).toBeTruthy();
    expect(api.salesCandidatesList).toHaveBeenCalled();
    const approve = container.querySelector('[data-testid="ps-cand-approve-c1"]');
    expect(approve).toBeTruthy();
    expect(approve.disabled).toBe(true);
  });

  test("riwayat filters to real audit actions only", async () => {
    mockSearch = "section=riwayat";
    await act(async () => {
      root.render(
        <SalesSettingsHub
          notify={() => {}}
          canViewMaster
          canEditMaster
          canViewMapping
          canEditMapping
        />,
      );
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    const rows = container.querySelectorAll('[data-testid="ps-riwayat-row"]');
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).toContain("settings.sales_master.update");
    expect(container.textContent).not.toContain("orders.sales.update");
  });

  test("riwayat without audit.view stays informative", async () => {
    mockSearch = "section=riwayat";
    mockHasPerm = (key) => key !== "audit.view";
    await act(async () => {
      root.render(
        <SalesSettingsHub
          notify={() => {}}
          canViewMaster
          canEditMaster={false}
          canViewMapping={false}
          canEditMapping={false}
        />,
      );
    });
    expect(container.querySelector('[data-testid="ps-riwayat-denied"]')).toBeTruthy();
    expect(api.adminAuditLogs).not.toHaveBeenCalled();
  });
});
