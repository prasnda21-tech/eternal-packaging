import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import Settings from "./Settings";

let mockPath = "/settings/roles";
let mockSearch = "";
let mockUser = { id: "u-dir", role: "direktur_utama", email: "direktur@example.test" };
let mockRoles = [];

jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: mockPath, search: mockSearch }),
  useSearchParams: () => [new URLSearchParams(mockSearch.startsWith("?") ? mockSearch.slice(1) : mockSearch)],
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: mockUser,
    hasPerm: () => true,
    refresh: jest.fn(),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {},
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const catalog = {
  modules: {
    PO: [
      { key: "po.view", label: "Lihat PO" },
      { key: "paint.view", label: "Cat produksi", opt_in: true },
    ],
    Pembelian: [
      { key: "purchase_orders.view", label: "Lihat PO Pembelian" },
    ],
  },
};

const branches = {
  branches: [
    { id: "branch-ygy", code: "YGY", name: "Yogyakarta" },
    { id: "branch-smg", code: "SMG", name: "Semarang" },
  ],
  primary_branch_id: "branch-ygy",
  global_access: false,
};

function stubApi() {
  const resolved = (value) => jest.fn(() => Promise.resolve(value));
  Object.assign(api, {
    adminPermissionCatalog: resolved(catalog),
    adminListRoles: jest.fn(() => Promise.resolve({ roles: mockRoles })),
    adminListUsers: resolved({ users: [] }),
    adminPatchRoleBusinessPermissions: jest.fn(() => Promise.resolve({ ok: true })),
    adminCreateUser: jest.fn(() => Promise.resolve({ id: "new-user" })),
    operationalBranchOptions: resolved(branches),
  });
}

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function setInput(el, value) {
  const proto = window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("Settings opt-in draft and employee branch payload", () => {
  let container;
  let root;
  let confirmSpy;

  const renderAt = async (pathname, user) => {
    mockPath = pathname;
    mockUser = user;
    await act(async () => root.render(<Settings />));
    await flush();
    await flush();
    await flush();
  };

  beforeEach(() => {
    stubApi();
    confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(false);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockRoles = [{
      role: "crm",
      label: "CRM",
      status: "active",
      description: "",
      permissions: ["po.view"],
    }];
  });

  afterEach(async () => {
    confirmSpy.mockRestore();
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("opt-in cancel keeps the draft, 409 keeps it, confirm saves, package stays locked", async () => {
    await renderAt("/settings/roles", {
      id: "u-dir",
      role: "direktur_utama",
      email: "direktur@example.test",
    });

    const paint = container.querySelector('[data-testid="role-perm-crm-paint.view"] input');
    const pkg = container.querySelector('[data-testid="role-perm-package-locked-purchase_orders.view"]');
    expect(paint).toBeTruthy();
    expect(paint.checked).toBe(false);
    expect(pkg).toBeTruthy();
    expect(pkg.disabled).toBe(true);

    await act(async () => { pkg.click(); });
    expect(pkg.checked).toBe(false);

    await act(async () => { paint.click(); });
    expect(container.querySelector('[data-testid="role-perm-crm-paint.view"] input').checked).toBe(true);

    await act(async () => {
      container.querySelector('[data-testid="role-save-permissions"]').click();
    });
    await flush();
    expect(confirmSpy).toHaveBeenCalled();
    expect(String(confirmSpy.mock.calls[0][0])).toMatch(/paint\.view/);
    expect(api.adminPatchRoleBusinessPermissions).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="role-perm-crm-paint.view"] input').checked).toBe(true);

    confirmSpy.mockReturnValue(true);
    api.adminPatchRoleBusinessPermissions.mockRejectedValueOnce({
      response: { data: { detail: "Permission role telah berubah. Muat ulang sebelum menyimpan." } },
    });
    await act(async () => {
      container.querySelector('[data-testid="role-save-permissions"]').click();
    });
    await flush();
    await flush();
    expect(container.querySelector('[data-testid="role-error"]').textContent)
      .toMatch(/Permission role telah berubah/);
    expect(container.querySelector('[data-testid="role-perm-crm-paint.view"] input').checked).toBe(true);
    expect(api.adminPatchRoleBusinessPermissions).toHaveBeenCalledTimes(1);

    api.adminPatchRoleBusinessPermissions.mockResolvedValueOnce({ ok: true });
    await act(async () => {
      container.querySelector('[data-testid="role-save-permissions"]').click();
    });
    await flush();
    await flush();
    expect(api.adminPatchRoleBusinessPermissions).toHaveBeenCalledTimes(2);
    const [, body] = api.adminPatchRoleBusinessPermissions.mock.calls[1];
    expect(body.confirm_opt_in_keys).toEqual(["paint.view"]);
    expect(body.add).toEqual(["paint.view"]);
    expect(body.expected_permissions).toEqual(["po.view"]);
    expect(body.add).not.toContain("purchase_orders.view");
  });

  test("Direktur employee form defaults Yogyakarta and stores the selected branches", async () => {
    mockRoles = [
      { role: "helper", label: "Helper", status: "active", permissions: [], description: "" },
    ];
    await renderAt("/settings/employees", {
      id: "u-dir",
      role: "direktur_utama",
      email: "direktur@example.test",
    });
    await act(async () => {
      container.querySelector('[data-testid="employee-add-button"]').click();
    });
    await flush();
    await flush();

    expect(container.textContent).toMatch(/Yogyakarta/);
    const boxes = [...container.querySelectorAll('[data-testid="employee-form"] input[type="checkbox"]')];
    const ygy = boxes.find((el) => el.parentElement.textContent.includes("Yogyakarta"));
    const smg = boxes.find((el) => el.parentElement.textContent.includes("Semarang"));
    expect(ygy.checked).toBe(true);
    expect(smg.checked).toBe(false);
    expect(container.querySelector('[data-testid="employee-form"] select').value).toBe("branch-ygy");

    await act(async () => { smg.click(); });
    await act(async () => {
      setInput(container.querySelector('[data-testid="emp-name"]'), "Sari Baru");
      setInput(container.querySelector('[data-testid="emp-email"]'), "sari.baru@example.test");
    });
    await act(async () => {
      container.querySelector('[data-testid="employee-form"]').requestSubmit();
    });
    await flush();

    expect(api.adminCreateUser).toHaveBeenCalledTimes(1);
    const payload = api.adminCreateUser.mock.calls[0][0];
    expect(payload.branch_ids).toEqual(["branch-ygy", "branch-smg"]);
    expect(payload.primary_branch_id).toBe("branch-ygy");
    expect(payload.name).toBe("Sari Baru");
    expect(payload.email).toBe("sari.baru@example.test");
  });

  test("Admin employee form cannot assign branches and omits them from the payload", async () => {
    mockRoles = [
      { role: "helper", label: "Helper", status: "active", permissions: [], description: "" },
    ];
    await renderAt("/settings/employees", {
      id: "u-admin",
      role: "admin",
      email: "admin@example.test",
    });
    await act(async () => {
      container.querySelector('[data-testid="employee-add-button"]').click();
    });
    await flush();

    expect(container.textContent).toMatch(/Pengaturan akses cabang dikelola Direktur\/Holding/);
    expect(container.querySelectorAll('[data-testid="employee-form"] input[type="checkbox"]').length).toBe(0);
    expect(api.operationalBranchOptions).not.toHaveBeenCalled();

    await act(async () => {
      setInput(container.querySelector('[data-testid="emp-name"]'), "Budi Admin");
      setInput(container.querySelector('[data-testid="emp-email"]'), "budi.admin@example.test");
    });
    await act(async () => {
      container.querySelector('[data-testid="employee-form"]').requestSubmit();
    });
    await flush();

    expect(api.adminCreateUser).toHaveBeenCalledTimes(1);
    const payload = api.adminCreateUser.mock.calls[0][0];
    expect(payload).not.toHaveProperty("branch_ids");
    expect(payload).not.toHaveProperty("primary_branch_id");
    expect(payload.name).toBe("Budi Admin");
    expect(payload.role).toBe("helper");
  });
});
